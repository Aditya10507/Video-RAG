"""Jina hosted embedder (multilingual, 1024-dim). Key required."""

from __future__ import annotations

import httpx

from ..errors import EmbeddingError


class JinaEmbedder:
    def __init__(
        self,
        api_key: str,
        model: str = "jina-embeddings-v3",
        batch_size: int = 32,
        timeout: float = 30.0,
    ):
        if not api_key:
            raise EmbeddingError("VIDEO_RAG_JINA_API_KEY is required")
        self.api_key = api_key
        self.model = model
        self.batch_size = batch_size
        self.timeout = timeout

    def _post(self, texts: list[str], task: str) -> list[list[float]]:
        import time

        last: Exception | None = None
        for attempt in range(3):
            try:
                r = httpx.post(
                    "https://api.jina.ai/v1/embeddings",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={"model": self.model, "task": task, "input": texts},
                    timeout=self.timeout,
                )
                if r.status_code in (429, 502, 503, 504) and attempt < 2:
                    time.sleep(1.0 * (attempt + 1))
                    continue
                r.raise_for_status()
                return [d["embedding"] for d in r.json()["data"]]
            except EmbeddingError:
                raise
            except Exception as e:
                last = e
                if attempt < 2:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                raise EmbeddingError(f"embedding failed: {e}") from e
        raise EmbeddingError(f"embedding failed: {last}")

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            out.extend(self._post(texts[i : i + self.batch_size], "retrieval.passage"))
        return out

    def embed_query(self, text: str) -> list[float]:
        return self._post([text], "retrieval.query")[0]


class HashingEmbedder:
    """Offline fallback for tests/smoke. Deterministic, no network."""

    def __init__(self, dim: int = 128):
        self.dim = dim

    def _vec(self, text: str) -> list[float]:
        import hashlib

        v = [0.0] * self.dim
        for w in text.lower().split():
            h = int(hashlib.md5(w.encode()).hexdigest(), 16) % self.dim
            v[h] += 1.0
        n = sum(x * x for x in v) ** 0.5 or 1.0
        return [x / n for x in v]

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)
