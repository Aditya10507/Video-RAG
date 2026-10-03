"""Lexical reranker (offline default). Production uses Jina multilingual."""

from __future__ import annotations

import httpx

from ..core.models import Chunk
from ..core.ranking import lexical_score
from ..errors import RetrievalError


class LexicalReranker:
    def score(self, query: str, chunks: list[Chunk]) -> list[float]:
        return [lexical_score(query, c.text) for c in chunks]


class JinaReranker:
    """Hosted cross-encoder. Scores are calibrated for the 0.55/0.15 gate."""

    def __init__(self, api_key: str, model: str = "jina-reranker-v2-base-multilingual",
                 timeout: float = 30.0):
        if not api_key:
            raise RetrievalError("VIDEO_RAG_JINA_API_KEY is required for reranking")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def score(self, query: str, chunks: list[Chunk]) -> list[float]:
        if not chunks:
            return []
        import logging

        try:
            r = httpx.post("https://api.jina.ai/v1/rerank",
                           headers={"Authorization": f"Bearer {self.api_key}",
                                    "Content-Type": "application/json"},
                           json={"model": self.model, "query": query,
                                 "documents": [c.text[:1000] for c in chunks]},
                           timeout=self.timeout)
            r.raise_for_status()
            results = (r.json().get("results") or [])
            by_index = {res.get("index"): float(res.get("relevance_score", 0))
                        for res in results}
            return [by_index.get(i, 0.0) for i in range(len(chunks))]
        except Exception as e:
            logging.getLogger(__name__).warning("jina rerank failed, lexical fallback: %s", e)
            return LexicalReranker().score(query, chunks)
