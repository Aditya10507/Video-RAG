"""Use case two: answer one question about one course.

Retrieval hard-filters course_id (+ optional video_id). Refusal is a code rule.
Streaming reuses the same retrieval + prompt; only the LLM call streams.
"""

from __future__ import annotations

from ..core.coverage import gate
from ..core.language import detect_answer_language, language_directive
from ..core.ranking import rank_videos
from ..core.timestamps import refine, youtube_url


class QueryService:
    # Follow-up signals: short/anaphoric questions that need history to stand
    # alone ("explain more", "give an example of it"). Standalone questions
    # skip the extra LLM call. History is used for rewriting only, never as
    # answer context, so grounding in video excerpts is preserved.
    FOLLOWUP_HINTS = (
        " it ", " this ", " that ", " these ", " those ", " they ", " them ",
        " its ", " more ", " above ", " previous ", " earlier ", " also ",
        " else ", " example ", " explain ", " elaborate ", " why ",
        " how about ", " what about ",
    )

    def __init__(self, settings, embedder, store, reranker, llm, registry=None):
        self.settings = settings
        self.embedder = embedder
        self.store = store
        self.reranker = reranker
        self.llm = llm
        self.registry = registry

    def _recent_turns(self, course_id: str, n: int = 3) -> list[dict]:
        if not self.registry:
            return []
        try:
            msgs = self.registry.list_messages(course_id, limit=20)
        except Exception:
            return []
        return msgs[-n:] if n > 0 else []

    def _needs_rewrite(self, question: str, history: list[dict]) -> bool:
        if not history:
            return False
        words = question.strip().split()
        if len(words) <= 4:
            return True
        ql = f" {question.lower()} "
        return any(h in ql for h in self.FOLLOWUP_HINTS)

    def _rewrite_query(self, question: str, history: list[dict]) -> str:
        if not self._needs_rewrite(question, history):
            return question
        try:
            ctx = "\n".join(
                f"Q: {m.get('question', '')[:200]}\nA: {m.get('answer', '')[:300]}"
                for m in history[-3:]
            )
            out = self.llm.complete(
                [
                    {
                        "role": "user",
                        "content": f"Conversation so far:\n{ctx}\n\nFollow-up: {question}\n"
                        "Rewrite it as one standalone question with the same meaning. "
                        "Return only the rewritten question.",
                    }
                ],
                max_tokens=100,
            ).strip()
            if not out or len(out) > 500:
                return question
            return out
        except Exception:
            return question

    def _save_turn(
        self,
        course_id: str,
        question: str,
        out: dict,
        video_id: str | None = None,
        rewritten: str = "",
    ) -> None:
        if not self.registry:
            return
        try:
            self.registry.save_message(
                course_id,
                question,
                out.get("answer", ""),
                out.get("status", ""),
                out.get("top_score", 0.0),
                out.get("primary_source"),
                out.get("also_mentioned_in") or [],
                video_id,
                rewritten if rewritten and rewritten != question else "",
            )
        except Exception:
            pass

    def _retrieve(self, question: str, course_id: str, video_id: str | None = None):
        qv = self.embedder.embed_query(question)
        dense = self.store.search_dense(course_id, qv, self.settings.retrieve_top_k, video_id)
        kw = self.store.search_keyword(course_id, question, self.settings.retrieve_top_k, video_id)
        seen: dict = {}
        for chunk, _ in dense + kw:
            key = (chunk.video_id, round(float(chunk.start_sec), 3))
            if key not in seen:
                seen[key] = chunk
        chunks = list(seen.values())
        scores = self.reranker.score(question, chunks)
        order = sorted(zip(chunks, scores), key=lambda x: -x[1])
        return order[: self.settings.rerank_top_k]

    def _prompt(self, question: str, ranked, lang: str) -> list[dict]:
        ctx = "\n\n".join(
            f"[{i + 1}] {c.video_title}: {c.text[:600]}" for i, (c, _s) in enumerate(ranked[:6])
        )
        system = (
            "You answer using ONLY the excerpts below. "
            + language_directive(lang)
            + " Keep it to 4-6 sentences grounded in the excerpts."
        )
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": f"Excerpts:\n{ctx}\n\nQuestion: {question}\nAnswer:"},
        ]

    def _synthesize(self, question: str, ranked, lang: str) -> str:
        return self.llm.complete(
            self._prompt(question, ranked, lang), self.settings.llm_max_output_tokens
        ).strip()

    def _verify_explanation(self, question: str, chunk) -> bool:
        try:
            verdict = self.llm.complete_json(
                [
                    {
                        "role": "user",
                        "content": f"Transcript excerpt: {chunk.text[:800]}\nQuestion: {question}\n"
                        f"Does this excerpt actually EXPLAIN the concept, or merely mention it? "
                        f'Reply JSON: {{"explains": bool, "confidence": 0-1}}',
                    }
                ]
            )
            return bool(verdict.get("explains"))
        except Exception:
            return False

    def _localize_message(self, text: str, lang: str) -> str:
        if lang == "english":
            return text
        try:
            return (
                self.llm.complete(
                    [{"role": "user", "content": f"Translate to {lang}: {text}"}], max_tokens=100
                ).strip()
                or text
            )
        except Exception:
            return text

    def _build_refusal(self, course_id: str, lang: str) -> dict:
        covers: list = []
        if self.registry:
            try:
                covers = [
                    v.get("title", "") for v in self.registry.list_course_videos(course_id)[:6]
                ]
            except Exception:
                covers = []
        return {"course_covers": [c for c in covers if c]}

    def _checks(self, question: str, course_id: str, video_id: str | None):
        if len(question) > self.settings.max_question_characters:
            raise ValueError("question too long")
        if video_id and self.registry:
            vids = {v.get("video_id") for v in self.registry.list_course_videos(course_id)}
            if video_id not in vids:
                raise ValueError("video is not part of this course")

    def _citations(self, question: str, ranked) -> tuple[dict | None, list]:
        if not ranked:
            return None, []
        scored = [(c.video_id, s, c) for c, s in ranked]
        order = rank_videos(scored, self.settings.lambda_coverage)
        best_vid = order[0][0]
        # Best chunk of the winning video by rerank score, then the sentence
        # inside it that best matches the question, minus the lead-in.
        candidates = [cs for cs in ranked if cs[0].video_id == best_vid]
        best = max(candidates, key=lambda cs: cs[1])[0]
        start, label = refine(
            best.sentences or [{"t": best.start_sec, "s": best.text}],
            question,
            best.start_sec,
            self.settings.lead_in_seconds,
        )

        def cite(c, st, lb):
            return {
                "video_id": c.video_id,
                "video_title": c.video_title,
                "url": youtube_url(c.video_id, st),
                "timestamp_label": lb,
                "start_seconds": st,
            }

        primary = cite(best, start, label)
        also, seen = [], {best_vid}
        for c, _s in ranked:
            if c.video_id not in seen:
                # Refine inside the mentioned video's own chunk, never the primary's.
                st, lb = refine(
                    c.sentences or [{"t": c.start_sec, "s": c.text}],
                    question,
                    c.start_sec,
                    self.settings.lead_in_seconds,
                )
                also.append(cite(c, st, lb))
                seen.add(c.video_id)
            if len(also) >= 3:
                break
        return primary, also

    def ask(self, question: str, course_id: str, video_id: str | None = None) -> dict:
        self._checks(question, course_id, video_id)
        history = self._recent_turns(course_id)
        effective = self._rewrite_query(question, history)
        lang = detect_answer_language(question)
        ranked = self._retrieve(effective, course_id, video_id)
        scores = [s for _c, s in ranked]
        top1 = scores[0] if scores else 0.0
        decision = gate(top1, scores, self.settings.threshold_high, self.settings.threshold_low)
        if decision == "covered":
            status, message = "answered", "Answered from the course excerpts below."
        elif decision == "not_covered":
            status = "not_covered"
            message = self._localize_message(self.settings.refusal_message, lang)
            out = {
                "status": status,
                "message": message,
                "answer": "",
                "top_score": round(top1, 4),
                "primary_source": None,
                "also_mentioned_in": [],
                **self._build_refusal(course_id, lang),
            }
            self._save_turn(course_id, question, out, video_id, effective)
            return out
        else:
            chunk = ranked[0][0] if ranked else None
            if chunk is not None and self._verify_explanation(effective, chunk):
                status, message = "answered", "Answered from the course excerpts below."
            else:
                status = "partial"
                message = self._localize_message(
                    "This topic is only briefly mentioned in the course and is not covered in depth.",
                    lang,
                )
        answer = self._synthesize(effective, ranked, lang) if status != "not_covered" else ""
        primary, also = self._citations(effective, ranked) if status != "not_covered" else (None, [])
        out = {
            "status": status,
            "message": message,
            "answer": answer,
            "top_score": round(top1, 4),
            "primary_source": primary,
            "also_mentioned_in": also,
            "course_covers": [],
        }
        self._save_turn(course_id, question, out, video_id, effective)
        return out

    def ask_stream(self, question: str, course_id: str, video_id: str | None = None):
        """Yield (kind, payload): ('token', str) ... ('result', dict). Same logic as ask."""
        self._checks(question, course_id, video_id)
        history = self._recent_turns(course_id)
        effective = self._rewrite_query(question, history)
        lang = detect_answer_language(question)
        ranked = self._retrieve(effective, course_id, video_id)
        scores = [s for _c, s in ranked]
        top1 = scores[0] if scores else 0.0
        decision = gate(top1, scores, self.settings.threshold_high, self.settings.threshold_low)
        if decision == "not_covered":
            message = self._localize_message(self.settings.refusal_message, lang)
            out = {
                "status": "not_covered",
                "message": message,
                "answer": "",
                "top_score": round(top1, 4),
                "primary_source": None,
                "also_mentioned_in": [],
                **self._build_refusal(course_id, lang),
            }
            self._save_turn(course_id, question, out, video_id, effective)
            yield ("result", out)
            return
        if decision == "verify":
            chunk = ranked[0][0] if ranked else None
            explains = self._verify_explanation(effective, chunk) if chunk else False
            status = "answered" if explains else "partial"
            message = (
                "Answered from the course excerpts below."
                if explains
                else self._localize_message(
                    "This topic is only briefly mentioned in the course "
                    "and is not covered in depth.",
                    lang,
                )
            )
        else:
            status, message = "answered", "Answered from the course excerpts below."
        messages = self._prompt(effective, ranked, lang)
        parts: list[str] = []
        try:
            for tok in self.llm.stream_complete(messages, self.settings.llm_max_output_tokens):
                parts.append(tok)
                yield ("token", tok)
        except Exception:
            text = self._synthesize(effective, ranked, lang)
            yield ("token", text)
            parts = [text]
        answer = "".join(parts).strip()
        primary, also = self._citations(effective, ranked)
        out = {
            "status": status,
            "message": message,
            "answer": answer,
            "top_score": round(top1, 4),
            "primary_source": primary,
            "also_mentioned_in": also,
            "course_covers": [],
        }
        self._save_turn(course_id, question, out, video_id, effective)
        yield ("result", out)
