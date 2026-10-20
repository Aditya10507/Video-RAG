"""Use case two: answer one question about one course.

Retrieval hard-filters course_id (+ optional video_id). Refusal is a code rule.
Streaming reuses the same retrieval + prompt; only the LLM call streams.

Live tracing: when a tracer is attached (API app), every ask is recorded for
the /eval dashboard — dense/keyword scores, pre/post-rerank order, what the
LLM saw, the answer and post-answer judge scores. Capture is a read-only side
channel: with tracer=None the behaviour is byte-for-byte the old one, and no
trace hook can ever break an answer.
"""

from __future__ import annotations

import contextlib
import time

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

    def __init__(self, settings, embedder, store, reranker, llm, registry=None, tracer=None):
        self.settings = settings
        self.embedder = embedder
        self.store = store
        self.reranker = reranker
        self.llm = llm
        self.registry = registry
        self.tracer = tracer

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
        with contextlib.suppress(Exception):
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

    # --- live trace hooks (no-ops without a tracer; must never raise) -------

    @staticmethod
    def _chunk_key(c) -> tuple:
        return (c.video_id, round(float(c.start_sec), 3))

    def _capture_pack(self, dense, kw, seen: dict, order) -> dict | None:
        """Per-chunk before/after picture, or None without a tracer.

        dense/keyword scores keyed by chunk; union_rank is the pre-rerank
        position (dense results first, then keyword misses — the same order
        the dedupe used); rerank holds post-rerank scores and the full order.
        """
        if self.tracer is None:
            return None
        dense_scores = {(c.video_id, round(float(c.start_sec), 3)): float(s) for c, s in dense}
        keyword_scores = {(c.video_id, round(float(c.start_sec), 3)): float(s) for c, s in kw}
        return {
            "dense_scores": dense_scores,
            "keyword_scores": keyword_scores,
            "union_rank": {key: rank for rank, key in enumerate(seen, start=1)},
            "rerank_scores": {self._chunk_key(c): float(s) for c, s in order},
            "rerank_order": [self._chunk_key(c) for c, _s in order],
            "chunks": dict(seen),
        }

    def _trace_open(self, question, course_id, video_id, source):
        if self.tracer is None:
            return None
        try:
            return self.tracer.open(
                {
                    "question": question,
                    "course_id": course_id,
                    "video_id": video_id,
                    "source": source,
                }
            )
        except Exception:
            return None

    def _trace_retrieval(self, tid, capture) -> None:
        if tid is None or not capture:
            return
        with contextlib.suppress(Exception):
            self.tracer.set_retrieval(tid, capture)

    def _trace_fail(self, tid, error) -> None:
        if tid is None:
            return
        with contextlib.suppress(Exception):
            self.tracer.fail(tid, str(error))

    def _trace_finish(
        self,
        tid,
        out,
        capture,
        *,
        effective,
        lang,
        gate_decision,
        verify_verdict,
        timings,
        usage,
    ) -> None:
        if tid is None:
            return
        with contextlib.suppress(Exception):
            self.tracer.finish(
                tid,
                out,
                capture,
                effective=effective,
                lang=lang,
                gate=gate_decision,
                verify_verdict=verify_verdict,
                timings=timings,
                usage=usage,
            )

    @staticmethod
    def _timings(t0: float, retrieve_ms: float, verify_ms: float = 0.0,
                 generate_ms: float = 0.0) -> dict:
        return {
            "retrieve_ms": round(float(retrieve_ms), 1),
            "verify_ms": round(float(verify_ms), 1),
            "generate_ms": round(float(generate_ms), 1),
            "total_ms": round((time.perf_counter() - t0) * 1000.0, 1),
        }

    # --- retrieval -----------------------------------------------------------

    def _retrieve(self, question: str, course_id: str, video_id: str | None = None):
        """Returns (ranked, capture). Pipeline behaviour unchanged; `capture`
        is the dashboard's before/after picture or None without a tracer."""
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
        order = sorted(zip(chunks, scores, strict=True), key=lambda x: -x[1])
        capture = self._capture_pack(dense, kw, seen, order)
        return order[: self.settings.rerank_top_k], capture

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

    # --- use case entry points -----------------------------------------------

    def ask(self, question: str, course_id: str, video_id: str | None = None) -> dict:
        self._checks(question, course_id, video_id)
        tid = self._trace_open(question, course_id, video_id, "ask")
        try:
            t0 = time.perf_counter()
            history = self._recent_turns(course_id)
            effective = self._rewrite_query(question, history)
            lang = detect_answer_language(question)
            t_r = time.perf_counter()
            ranked, capture = self._retrieve(effective, course_id, video_id)
            retrieve_ms = (time.perf_counter() - t_r) * 1000.0
            self._trace_retrieval(tid, capture)
            scores = [s for _c, s in ranked]
            top1 = scores[0] if scores else 0.0
            decision = gate(top1, scores, self.settings.threshold_high, self.settings.threshold_low)
            verify_verdict, verify_ms = None, 0.0
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
                self._trace_finish(
                    tid, out, capture,
                    effective=effective, lang=lang, gate_decision=decision,
                    verify_verdict=None,
                    timings=self._timings(t0, retrieve_ms),
                    usage={},
                )
                return out
            else:
                chunk = ranked[0][0] if ranked else None
                t_v = time.perf_counter()
                explains = chunk is not None and self._verify_explanation(effective, chunk)
                verify_ms = (time.perf_counter() - t_v) * 1000.0
                verify_verdict = "explains" if explains else "mentions"
                if explains:
                    status, message = "answered", "Answered from the course excerpts below."
                else:
                    status = "partial"
                    message = self._localize_message(
                        "This topic is only briefly mentioned in the course "
                        "and is not covered in depth.",
                        lang,
                    )
            t_g = time.perf_counter()
            answer = self._synthesize(effective, ranked, lang) if status != "not_covered" else ""
            generate_ms = (time.perf_counter() - t_g) * 1000.0
            primary, also = (
                self._citations(effective, ranked)
                if status != "not_covered"
                else (None, [])
            )
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
            self._trace_finish(
                tid, out, capture,
                effective=effective, lang=lang, gate_decision=decision,
                verify_verdict=verify_verdict,
                timings=self._timings(t0, retrieve_ms, verify_ms, generate_ms),
                usage=dict(getattr(self.llm, "last_usage", {}) or {}),
            )
            return out
        except Exception as e:
            self._trace_fail(tid, e)
            raise

    def ask_stream(self, question: str, course_id: str, video_id: str | None = None):
        """Yield (kind, payload): ('token', str) ... ('result', dict). Same logic as ask."""
        self._checks(question, course_id, video_id)
        tid = self._trace_open(question, course_id, video_id, "ask_stream")
        try:
            t0 = time.perf_counter()
            history = self._recent_turns(course_id)
            effective = self._rewrite_query(question, history)
            lang = detect_answer_language(question)
            t_r = time.perf_counter()
            ranked, capture = self._retrieve(effective, course_id, video_id)
            retrieve_ms = (time.perf_counter() - t_r) * 1000.0
            self._trace_retrieval(tid, capture)
            scores = [s for _c, s in ranked]
            top1 = scores[0] if scores else 0.0
            decision = gate(top1, scores, self.settings.threshold_high, self.settings.threshold_low)
            verify_verdict, verify_ms = None, 0.0
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
                self._trace_finish(
                    tid, out, capture,
                    effective=effective, lang=lang, gate_decision=decision,
                    verify_verdict=None,
                    timings=self._timings(t0, retrieve_ms),
                    usage={},
                )
                yield ("result", out)
                return
            if decision == "verify":
                chunk = ranked[0][0] if ranked else None
                t_v = time.perf_counter()
                explains = chunk is not None and self._verify_explanation(effective, chunk)
                verify_ms = (time.perf_counter() - t_v) * 1000.0
                verify_verdict = "explains" if explains else "mentions"
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
            t_g = time.perf_counter()
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
            generate_ms = (time.perf_counter() - t_g) * 1000.0
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
            self._trace_finish(
                tid, out, capture,
                effective=effective, lang=lang, gate_decision=decision,
                verify_verdict=verify_verdict,
                timings=self._timings(t0, retrieve_ms, verify_ms, generate_ms),
                usage=dict(getattr(self.llm, "last_usage", {}) or {}),
            )
            yield ("result", out)
        except Exception as e:
            self._trace_fail(tid, e)
            raise
