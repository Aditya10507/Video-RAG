"""Post-answer judge scoring for live queries. Runs in a daemon thread so it
never delays the answer; failures leave metrics NULL, never zero.

Judge model = the generator LLM itself (owner choice: same Groq model), so no
extra env vars are needed. Same-model bias is a known limitation; the value
here is watching trends, not absolute truth.
"""

from __future__ import annotations

import contextlib
import json
import logging
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from ..eval.scorers.judge import PROMPT_VERSION, prompt

MAX_TOKENS = 800
MAX_SCORING_WORKERS = 2
MAX_PENDING_SCORING = 100
log = logging.getLogger(__name__)
_executor = ThreadPoolExecutor(
    max_workers=MAX_SCORING_WORKERS, thread_name_prefix="live-eval"
)
_capacity = threading.BoundedSemaphore(MAX_SCORING_WORKERS + MAX_PENDING_SCORING)


@dataclass
class MetricResult:
    value: float | None
    detail: dict
    status: str
    error: str = ""


class ScoringError(Exception):
    pass


def _parse_json(raw: str) -> dict:
    start, end = raw.find("{"), raw.rfind("}") + 1
    if start == -1 or end == 0:
        raise ScoringError(f"no JSON object in judge reply: {raw[:120]}")
    try:
        out = json.loads(raw[start:end])
    except ValueError as e:
        raise ScoringError(f"judge JSON parse failed: {e}") from e
    if not isinstance(out, dict):
        raise ScoringError("judge reply must be a JSON object")
    return out


def _excerpts(items: list[dict], cap: int = 600) -> str:
    return "\n\n".join(
        f"[{i + 1}] {c.get('title', '')}: {(c.get('text', '') or '')[:cap]}"
        for i, c in enumerate(items)
    )


def chunk_relevance(llm, question: str, chunks: list[dict]) -> dict[int, tuple[float, str]]:
    """One judge call grades every retrieved chunk against the question.

    This is the "did the retriever find relevant chunks, or did it settle for
    junk because nothing relevant exists" signal, per chunk.
    """
    if not chunks:
        return {}
    text = prompt("chunk_relevance").format(
        question=question, excerpts=_excerpts(chunks)
    )
    raw = llm.complete([{"role": "user", "content": text}], MAX_TOKENS).strip()
    out = _parse_json(raw)
    items = out.get("items")
    if not isinstance(items, list):
        raise ScoringError(f"chunk_relevance: no items: {raw[:120]}")
    result: dict[int, tuple[float, str]] = {}
    if len(items) != len(chunks):
        raise ScoringError(f"chunk_relevance: expected {len(chunks)} items, received {len(items)}")
    for item in items:
        if not isinstance(item, dict):
            raise ScoringError(f"chunk_relevance: item must be an object: {item!r}")
        idx = item.get("idx")
        if isinstance(idx, bool) or not isinstance(idx, int):
            raise ScoringError(f"chunk_relevance: idx must be an integer: {item!r}")
        if not 1 <= idx <= len(chunks):
            raise ScoringError(f"chunk_relevance: idx {idx} out of range")
        if idx in result:
            raise ScoringError(f"chunk_relevance: duplicate idx {idx}")
        relevant = item.get("relevant")
        if not isinstance(relevant, bool):
            raise ScoringError(f"chunk_relevance: relevant must be boolean for idx {idx}")
        reason = item.get("reason", "")
        if not isinstance(reason, str):
            raise ScoringError(f"chunk_relevance: reason must be text for idx {idx}")
        result[idx] = (1.0 if relevant else 0.0, reason[:300])
    if len(result) != len(chunks):
        raise ScoringError(
            f"chunk_relevance: {len(result)} of {len(chunks)} chunks graded"
        )
    return result


def faithfulness(llm, answer: str, sent_chunks: list[dict]) -> tuple[float, dict]:
    text = prompt("faithfulness").format(
        excerpts=_excerpts(sent_chunks), answer=answer
    )
    raw = llm.complete([{"role": "user", "content": text}], MAX_TOKENS).strip()
    claims = _parse_json(raw).get("claims")
    if not isinstance(claims, list):
        raise ScoringError("faithfulness: claims must be a list")
    if not claims:
        raise ScoringError(f"faithfulness: no claims: {raw[:120]}")
    for claim in claims:
        if (
            not isinstance(claim, dict)
            or not isinstance(claim.get("text"), str)
            or not claim["text"].strip()
            or not isinstance(claim.get("supported"), bool)
        ):
            raise ScoringError(f"faithfulness: malformed claim: {claim!r}")
    sup = sum(1 for c in claims if c["supported"])
    return sup / len(claims), {"claims": claims}


def relevancy(llm, question: str, answer: str) -> tuple[float, dict]:
    text = prompt("relevancy").format(question=question, answer=answer)
    raw = llm.complete([{"role": "user", "content": text}], MAX_TOKENS).strip()
    out = _parse_json(raw)
    score = out.get("score")
    if isinstance(score, bool) or not isinstance(score, int) or not 0 <= score <= 4:
        raise ScoringError(f"relevancy off-rubric: {score!r}")
    reasoning = out.get("reasoning", "")
    if not isinstance(reasoning, str):
        raise ScoringError("relevancy reasoning must be text")
    return score / 4, {"score": score, "reasoning": reasoning[:500]}


def schedule(llm, db_path: str, query_id: int, question: str) -> None:
    """Queue a bounded scoring job without delaying the user-facing answer."""
    db_path, query_id = str(db_path), int(query_id)
    if not _capacity.acquire(blocking=False):
        try:
            _set_job_status(
                db_path, query_id, "overloaded",
                "Live judge queue is full; scores were skipped for this query.",
            )
        except sqlite3.Error:
            log.exception("unable to mark live scoring queue overload for query %s", query_id)
        return
    try:
        _set_job_status(db_path, query_id, "queued", "")
        future = _executor.submit(_score_and_store, llm, db_path, query_id, question)
        future.add_done_callback(lambda _future: _capacity.release())
    except Exception as e:
        _capacity.release()
        log.exception("unable to queue live scoring for query %s", query_id)
        try:
            _set_job_status(db_path, query_id, "error", f"Unable to queue scoring: {e}"[:300])
        except sqlite3.Error:
            log.exception("unable to mark live scoring queue failure for query %s", query_id)


def _set_job_status(db_path: str, query_id: int, status: str, error: str) -> None:
    con = sqlite3.connect(db_path, timeout=30.0)
    try:
        con.execute(
            "UPDATE live_queries SET scoring_status = ?, scoring_error = ? WHERE id = ?",
            (status, error[:300], query_id),
        )
        con.commit()
    finally:
        con.close()


def _metric(
    fn, *args, detail_default: dict | None = None
) -> MetricResult:
    try:
        value, detail = fn(*args)
        return MetricResult(float(value), detail, "done")
    except Exception as e:
        return MetricResult(None, detail_default or {}, "error", str(e)[:200])


def _score_and_store(llm, db_path: str, query_id: int, question: str) -> None:
    try:
        _set_job_status(db_path, query_id, "running", "")
        con = sqlite3.connect(str(db_path), timeout=30.0)
        con.row_factory = sqlite3.Row
        try:
            q = con.execute("SELECT * FROM live_queries WHERE id = ?", (query_id,)).fetchone()
            chunks = [
                dict(r)
                for r in con.execute(
                    "SELECT * FROM live_query_chunks WHERE query_id = ? ORDER BY idx",
                    (query_id,),
                ).fetchall()
            ]
        finally:
            con.close()
        if q is None:
            return
        effective = (q["effective_question"] or "").strip() or question
        results: dict[str, MetricResult] = {}
        errors: list[str] = []

        if not chunks:
            results["retrieval_relevance"] = MetricResult(None, {}, "not_applicable")
        else:
            try:
                rel = chunk_relevance(llm, effective, chunks)
                _apply_chunk_relevance(db_path, query_id, rel)
                hits = sum(1 for value, _reason in rel.values() if value > 0)
                results["retrieval_relevance"] = MetricResult(
                    hits / len(chunks),
                    {"relevant": hits, "of": len(chunks)},
                    "done",
                )
            except Exception as e:
                results["retrieval_relevance"] = MetricResult(
                    None, {}, "error", str(e)[:200]
                )

        answer = (q["answer"] or "").strip()
        sent = [c for c in chunks if c.get("sent_to_llm")]
        if answer and sent:
            results["faithfulness"] = _metric(faithfulness, llm, answer, sent)
        else:
            results["faithfulness"] = MetricResult(None, {}, "not_applicable")
        if answer:
            results["answer_relevancy"] = _metric(relevancy, llm, effective, answer)
        else:
            results["answer_relevancy"] = MetricResult(None, {}, "not_applicable")

        for name, result in results.items():
            if result.status == "error":
                errors.append(f"{name}: {result.error}")

        model = getattr(llm, "model", "")
        con = sqlite3.connect(str(db_path), timeout=30.0)
        try:
            for metric, result in results.items():
                con.execute(
                    "INSERT OR REPLACE INTO live_query_scores"
                    " (query_id, metric, value, detail, judge_model, prompt_version, status, error)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        query_id, metric, result.value, json.dumps(result.detail), model,
                        PROMPT_VERSION, result.status, result.error,
                    ),
                )
            successful = sum(r.status == "done" for r in results.values())
            failed = sum(r.status == "error" for r in results.values())
            status = (
                "partial" if successful and failed else
                "error" if failed else
                "done"
            )
            error = "; ".join(errors)[:300] if errors else ""
            con.execute(
                "UPDATE live_queries SET scoring_status = ?, scoring_error = ? WHERE id = ?",
                (status, error, query_id),
            )
            con.commit()
        finally:
            con.close()
    except Exception as e:  # absolute last resort: mark, never crash the thread
        log.exception("live scoring failed for query %s", query_id)
        with contextlib.suppress(Exception):
            con = sqlite3.connect(str(db_path), timeout=30.0)
            con.execute(
                "UPDATE live_queries SET scoring_status = 'error', scoring_error = ?"
                " WHERE id = ?",
                (str(e)[:300], query_id),
            )
            con.commit()
            con.close()


def _apply_chunk_relevance(db_path: str, query_id: int, rel: dict[int, tuple[float, str]]) -> None:
    con = sqlite3.connect(str(db_path), timeout=30.0)
    try:
        for idx, (value, reason) in rel.items():
            con.execute(
                "UPDATE live_query_chunks SET relevance = ?, relevance_detail = ?"
                " WHERE query_id = ? AND idx = ?",
                (value, reason, query_id, idx),
            )
        con.commit()
    finally:
        con.close()
