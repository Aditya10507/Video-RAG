"""Thin wrapper turning the real pipeline into the spec's adapter contract.

No pipeline behaviour changes: timings are measured around the existing
calls, answers come from the real `ask()`, usage is read off the LLM client.
One question costs two retrieval passes (traced + inside ask); documented.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace

from ..ask.service import QueryService


@dataclass
class RetrievedChunk:
    chunk_id: str
    video_id: str
    start_s: float
    end_s: float
    text: str
    score: float | None
    rank: int
    modality: str = "transcript"


@dataclass
class Citation:
    video_id: str
    start_s: float
    end_s: float
    chunk_id: str | None = None
    claim_text: str | None = None


@dataclass
class RAGResponse:
    answer: str
    abstained: bool
    retrieved: list[RetrievedChunk] = field(default_factory=list)
    citations: list[Citation] = field(default_factory=list)
    timings_ms: dict = field(default_factory=dict)
    usage: dict = field(default_factory=dict)
    citation_source: str = "pipeline"


def _chunk_id(video_id: str, start_s: float) -> str:
    return f"{video_id}:{round(float(start_s), 3)}"


class VideoRagAdapter:
    def __init__(self, settings, embedder, store, reranker, llm):
        self.settings = settings
        self.embedder = embedder
        self.store = store
        self.reranker = reranker
        self.llm = llm
        self._QueryService = QueryService

    def _svc(self, overrides: dict | None = None):
        settings, reranker = self.settings, self.reranker
        top_k = (overrides or {}).get("top_k")
        if top_k:
            settings = replace(
                settings, retrieve_top_k=top_k, rerank_top_k=min(top_k, settings.rerank_top_k)
            )
        if (overrides or {}).get("reranker") == "off":
            from ..adapters.reranker import LexicalReranker

            reranker = LexicalReranker()
        return self._QueryService(settings, self.embedder, self.store, reranker, self.llm, None)

    def query(self, question: str, course_id: str, overrides: dict | None = None) -> RAGResponse:
        svc = self._svc(overrides)
        t, chunks = self._traced_retrieval(svc, question, course_id)
        t0 = time.perf_counter()
        try:
            out = svc.ask(question, course_id)
        except ValueError as e:
            out = {"status": "not_covered", "answer": "", "primary_source": None,
                   "also_mentioned_in": []}
            out["_error"] = str(e)
        gen_ms = (time.perf_counter() - t0) * 1000
        timings = {**t, "generation": round(gen_ms, 1),
                   "total": round(sum(t.values()) + gen_ms, 1)}
        by_id = {(c.video_id, round(c.start_s, 3)): c for c in chunks}
        cites = self._citations(out, by_id)
        usage = dict(getattr(self.llm, "last_usage", {}) or {})
        return RAGResponse(
            answer=out.get("answer", ""),
            abstained=out.get("status") == "not_covered",
            retrieved=chunks,
            citations=cites,
            timings_ms=timings,
            usage=usage,
        )

    def _traced_retrieval(self, svc, question: str, course_id: str):
        t: dict = {}
        t0 = time.perf_counter()
        qv = self.embedder.embed_query(question)
        t["embed"] = (time.perf_counter() - t0) * 1000
        top_k = svc.settings.retrieve_top_k
        t0 = time.perf_counter()
        dense = svc.store.search_dense(course_id, qv, top_k)
        t["dense"] = (time.perf_counter() - t0) * 1000
        t0 = time.perf_counter()
        kw = svc.store.search_keyword(course_id, question, top_k)
        t["keyword"] = (time.perf_counter() - t0) * 1000
        seen: dict = {}
        for chunk, _ in dense + kw:
            seen.setdefault((chunk.video_id, round(float(chunk.start_sec), 3)), chunk)
        t0 = time.perf_counter()
        order = svc.reranker.score(question, list(seen.values()))
        ranked = sorted(zip(seen.values(), order, strict=True), key=lambda x: -x[1])
        t["rerank"] = (time.perf_counter() - t0) * 1000
        chunks = [
            RetrievedChunk(
                chunk_id=_chunk_id(c.video_id, c.start_sec), video_id=c.video_id,
                start_s=float(c.start_sec), end_s=float(c.end_sec), text=c.text,
                score=round(float(s), 4), rank=i + 1,
            )
            for i, (c, s) in enumerate(ranked[: svc.settings.rerank_top_k])
        ]
        return {k: round(v, 1) for k, v in t.items()}, chunks

    def _citations(self, out: dict, by_id: dict) -> list[Citation]:
        cites = []
        for src in [out.get("primary_source")] + list(out.get("also_mentioned_in") or []):
            if not src or not src.get("video_id"):
                continue
            start = float(src.get("start_seconds", 0) or 0)
            match = by_id.get((src["video_id"], round(start, 3)))
            if match is None:
                near = [c for (v, _s), c in by_id.items() if v == src["video_id"]]
                match = min(near, key=lambda c: abs(c.start_s - start), default=None)
            cites.append(
                Citation(
                    video_id=src["video_id"], start_s=start,
                    end_s=match.end_s if match else start,
                    chunk_id=match.chunk_id if match else None,
                )
            )
        return cites
