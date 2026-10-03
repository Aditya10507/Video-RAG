"""Doubles used by the tests. Offline, deterministic."""

from __future__ import annotations

from ..core.models import Chunk, TranscriptDocument, VideoJob
from .llm import StubLanguageModel


class FakeCatalog:
    def __init__(self, jobs: list[VideoJob], title: str = "Fake Course"):
        self.jobs = jobs
        self.title = title

    def expand(self, parsed_url: dict):
        return {"title": self.title, "source_url": "fake"}, self.jobs


class FakeTranscripts:
    def __init__(self, docs: dict[str, TranscriptDocument]):
        self.docs = docs

    def fetch(self, video_id: str) -> TranscriptDocument:
        return self.docs[video_id]


class FakeEmbedder:
    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        from .embeddings import HashingEmbedder

        return HashingEmbedder().embed_texts(texts)

    def embed_query(self, text: str) -> list[float]:
        from .embeddings import HashingEmbedder

        return HashingEmbedder().embed_query(text)


class FakeStore:
    def __init__(self, chunks: list[Chunk]):
        self.chunks = chunks

    def upsert(self, course_id: str, chunks: list[Chunk], vectors) -> int:
        self.chunks.extend(chunks)
        return len(chunks)

    def _scoped(self, course_id: str, video_id=None) -> list[Chunk]:
        return [
            c
            for c in self.chunks
            if c.course_id == course_id and (video_id is None or c.video_id == video_id)
        ]

    def search_dense(self, course_id, query_vector, top_k, video_id=None):
        scoped = self._scoped(course_id, video_id)
        return [(c, 0.5) for c in scoped[:top_k]]

    def search_keyword(self, course_id, query, top_k, video_id=None):
        from ..core.ranking import lexical_score

        scoped = sorted(
            self._scoped(course_id, video_id), key=lambda c: -lexical_score(query, c.text)
        )
        return [(c, lexical_score(query, c.text)) for c in scoped[:top_k]]

    def count_chunks(self, course_id, video_id=None) -> int:
        return len(self._scoped(course_id, video_id))

    def count_chunks_by_video(self, course_id) -> dict[str, int]:
        return self.count_chunks_by_course_and_video([course_id]).get(course_id, {})

    def count_chunks_by_course_and_video(self, course_ids) -> dict[str, dict[str, int]]:
        counts = {}
        selected_courses = set(course_ids)
        for chunk in self.chunks:
            if chunk.course_id not in selected_courses:
                continue
            video_counts = counts.setdefault(chunk.course_id, {})
            video_counts[chunk.video_id] = video_counts.get(chunk.video_id, 0) + 1
        return counts


__all__ = ["FakeCatalog", "FakeTranscripts", "FakeEmbedder", "FakeStore", "StubLanguageModel"]
