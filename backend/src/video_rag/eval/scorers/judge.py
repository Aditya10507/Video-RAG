"""LLM-judge scorers: temperature 0, versioned prompts, cache, retries."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from video_rag.adapters.llm import LanguageModel

PROMPT_VERSION = "v1"
PROMPTS = Path(__file__).resolve().parent.parent / "prompts"


def prompt(name: str) -> str:
    return (PROMPTS / f"{name}_{PROMPT_VERSION}.txt").read_text(encoding="utf-8")


class JudgeError(Exception):
    pass


class Judge:
    def __init__(self, settings, db_path: str, cfg: dict, max_tokens: int = 800):
        model = (settings.judge_model or "").strip()
        if not model:
            raise JudgeError("no judge model: set VIDEO_RAG_JUDGE_MODEL")
        self.model = model
        self.warning = (
            "judge model equals generator model; ratings may be generous"
            if model == settings.llm_model
            else ""
        )
        self.client = LanguageModel(
            settings.judge_base_url or settings.llm_base_url,
            model,
            settings.judge_api_key or settings.llm_api_key,
            settings.llm_timeout_seconds,
            max_tokens,
        )
        self.db_path = db_path
        self.retries = int(cfg.get("judge_retries", 3))
        self.max_tokens = max_tokens

    def _key(self, text: str) -> str:
        return hashlib.sha256(
            "|".join([self.model, PROMPT_VERSION, "0", text]).encode()
        ).hexdigest()

    def _con(self):
        from ..db import connect

        return connect(self.db_path)

    def _cached(self, key: str) -> str | None:
        con = self._con()
        try:
            row = con.execute("SELECT response FROM judge_cache WHERE key = ?", (key,)).fetchone()
            return row[0] if row else None
        finally:
            con.close()

    def _store(self, key: str, response: str) -> None:
        con = self._con()
        try:
            con.execute(
                "INSERT OR IGNORE INTO judge_cache (key, response, created_at)"
                " VALUES (?, ?, ?)",
                (key, response, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())),
            )
            con.commit()
        finally:
            con.close()

    def call(self, name: str, **fields) -> dict:
        """Render prompt, return parsed JSON. Retries malformed output, else raises."""
        text = prompt(name).format(**fields)
        key = self._key(text)
        hit = self._cached(key)
        if hit is not None:
            return json.loads(hit)
        last = "no response"
        for _ in range(max(1, self.retries)):
            try:
                raw = self.client.complete(
                    [{"role": "user", "content": text}], self.max_tokens
                ).strip()
            except Exception as e:
                last = str(e)[:200]
                continue
            try:
                start, end = raw.index("{"), raw.rindex("}") + 1
                out = json.loads(raw[start:end])
            except Exception:
                last = raw[:200]
                continue
            self._store(key, json.dumps(out))
            return out
        raise JudgeError(f"judge JSON failed after retries: {last}")

    def _excerpts(self, chunks: list, limit: int = 8) -> str:
        return "\n\n".join(
            f"[{i + 1}] {c.get('title', '')}: {(c.get('text', '') or '')[:600]}"
            for i, c in enumerate(chunks[:limit])
        )

    def faithfulness(self, answer: str, chunks: list) -> tuple[float, dict]:
        out = self.call("faithfulness", excerpts=self._excerpts(chunks), answer=answer)
        claims = out.get("claims") or []
        if not claims:
            raise JudgeError("judge returned no claims")
        sup = sum(1 for c in claims if c.get("supported"))
        return sup / len(claims), {"claims": claims}

    def context_recall(self, reference: str, chunks: list) -> tuple[float, dict]:
        out = self.call("context_recall", reference=reference, excerpts=self._excerpts(chunks))
        stmts = out.get("statements") or []
        if not stmts:
            raise JudgeError("judge returned no statements")
        attr = sum(1 for s in stmts if s.get("attributable"))
        return attr / len(stmts), {"statements": stmts}

    def correctness(self, answer: str, reference: str) -> tuple[float, dict]:
        out = self.call("correctness", reference=reference, answer=answer)
        score = float(out.get("score", -1))
        if score not in (0, 0.25, 0.5, 0.75, 1.0):
            raise JudgeError(f"judge score off-rubric: {out.get('score')}")
        return score, {"reasoning": out.get("reasoning", "")}

    def relevancy(self, question: str, answer: str) -> tuple[float, dict]:
        out = self.call("relevancy", question=question, answer=answer)
        score = out.get("score", -1)
        if not isinstance(score, int) or not 0 <= score <= 4:
            raise JudgeError(f"judge score off-rubric: {out.get('score')}")
        return score / 4, {"score": score, "reasoning": out.get("reasoning", "")}

    def citation_accuracy(self, answer: str, cited: list) -> tuple[float, dict]:
        if not cited:
            raise JudgeError("no cited chunks to check")
        shown = "\n\n".join(
            f"({c.get('chunk_id')}) {(c.get('text', '') or '')[:600]}" for c in cited
        )
        out = self.call("citation", answer=answer, cited=shown)
        pairs = out.get("pairs") or []
        if not pairs:
            raise JudgeError("judge returned no pairs")
        sup = sum(1 for p in pairs if p.get("supported"))
        return sup / len(pairs), {"pairs": pairs}
