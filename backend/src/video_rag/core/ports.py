"""Interfaces every adapter must satisfy. One contract per layer boundary."""

from __future__ import annotations

from typing import Protocol

from .models import Chunk, TranscriptDocument, VideoJob


class CatalogProvider(Protocol):
    def expand(self, parsed_url: dict) -> tuple[dict, list[VideoJob]]:
        """Return (course_info, jobs). Raises CatalogUnavailableError."""
        ...


class TranscriptProvider(Protocol):
    def fetch(self, video_id: str) -> TranscriptDocument: ...


class Embedder(Protocol):
    def embed_texts(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class Reranker(Protocol):
    def score(self, query: str, chunks: list[Chunk]) -> list[float]: ...


class VectorStore(Protocol):
    def upsert(self, course_id: str, chunks: list[Chunk], vectors: list[list[float]]) -> int: ...

    def search_dense(
        self, course_id: str, query_vector: list[float], top_k: int, video_id: str | None = None
    ) -> list[tuple[Chunk, float]]: ...

    def search_keyword(
        self, course_id: str, query: str, top_k: int, video_id: str | None = None
    ) -> list[tuple[Chunk, float]]: ...

    def count_chunks(self, course_id: str, video_id: str | None = None) -> int: ...

    def count_chunks_by_course_and_video(
        self, course_ids: list[str]
    ) -> dict[str, dict[str, int]]: ...

    def count_chunks_by_video(self, course_id: str) -> dict[str, int]: ...


class CourseRegistry(Protocol):
    def list_courses(self) -> list[dict]: ...

    def list_course_videos(self, course_id: str) -> list[dict]: ...

    # Write side: ingestion records the course and its videos so the Library
    # reflects fresh ingests. Optional in tests; ingest degrades gracefully.
    def upsert_course(
        self, course_id: str, source_url: str, title: str, video_count: int
    ) -> None: ...

    def upsert_video(
        self,
        video_id: str,
        title: str,
        duration_sec: int,
        status: str = "pending",
        error: str = "",
    ) -> None: ...

    def link_video_to_course(self, course_id: str, video_id: str, position: int) -> None: ...

    def set_course_status(self, course_id: str, status: str) -> None: ...

    def mark_video_status(self, video_id: str, status: str, error: str = "") -> None: ...


class LanguageModel(Protocol):
    """LLM is required. No is_enabled flag, no disabled fallback."""

    def complete(self, messages: list[dict], max_tokens: int = 600) -> str: ...

    def complete_json(self, messages: list[dict]) -> dict: ...

    def stream_complete(self, messages: list[dict], max_tokens: int = 600) -> object:
        """Yield content deltas (str). Streaming is a method, not a new file."""
        ...
