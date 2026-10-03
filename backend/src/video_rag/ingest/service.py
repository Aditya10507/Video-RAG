"""Use case one: index a course. Idempotent, resumable, per-video queryable."""

from __future__ import annotations

import asyncio
import logging

from ..core.chunking import chunk_words
from ..core.models import Chunk, VideoJob

log = logging.getLogger(__name__)


class IngestService:
    def __init__(self, settings, catalog, transcripts, embedder, store, registry=None):
        self.settings = settings
        self.catalog = catalog
        self.transcripts = transcripts
        self.embedder = embedder
        self.store = store
        self.registry = registry

    def _register_course(
        self, course_id: str, source_url: str, title: str, jobs: list[VideoJob]
    ) -> None:
        """Write courses + videos + course_videos rows so the Library lists
        fresh ingests. Registry is optional (CLI smoke passes None); failures
        are logged, never fatal — vectors are the source of truth for answers."""
        if not self.registry:
            return
        try:
            self.registry.upsert_course(course_id, source_url, title, len(jobs))
            for j in jobs:
                self.registry.upsert_video(
                    j.video_id, j.title, j.duration_sec, status="pending"
                )
                self.registry.link_video_to_course(course_id, j.video_id, j.position)
        except Exception as e:
            log.warning("registry write failed (Library may be stale): %s", e)

    async def ingest(
        self, url: str, job_store=None, job_id: str | None = None, force: bool = False
    ) -> dict:
        from ..core.url_parsing import course_id_for, parse_url

        parsed = parse_url(url)
        if parsed.get("kind") == "ambiguous":
            raise ValueError("ambiguous URL: choose single video or playlist")
        course_id = course_id_for(parsed)
        info, jobs = self.catalog.expand(parsed)
        for j in jobs:
            j.course_id = course_id
        self._register_course(course_id, info.get("source_url", url), info.get("title", ""), jobs)
        # Progress reporting: the video count is only known after the catalog
        # expand, so publish it here. Without this the bar cannot move.
        if job_store and job_id:
            job_store.set_total(job_id, len(jobs))
        # Skip videos whose chunks already exist: saves transcript + embedding
        # calls. Truth is the vector store, not the registry. force=True
        # re-indexes everything (e.g. after a pipeline bump).
        skipped = 0
        if not force:
            remaining = []
            for j in jobs:
                try:
                    n = self.store.count_chunks(course_id, j.video_id)
                except Exception:
                    n = 0
                if n > 0:
                    skipped += 1
                    if job_store and job_id:
                        job_store.bump(job_id)
                else:
                    remaining.append(j)
            jobs = remaining
        sem = asyncio.Semaphore(self.settings.ingest_concurrency)
        indexed, failed = skipped, 0

        def _mark(video_id: str, status: str, error: str = "") -> None:
            if not self.registry:
                return
            try:
                self.registry.mark_video_status(video_id, status, error)
            except Exception as e:
                log.warning("registry status write failed for %s: %s", video_id, e)

        async def one(job):
            nonlocal indexed, failed
            async with sem:
                _mark(job.video_id, "indexing")
                try:
                    doc = await asyncio.to_thread(self.transcripts.fetch, job.video_id)
                    words = doc.raw_words or []
                    parts = chunk_words(
                        words,
                        self.settings.chunk_window_seconds,
                        self.settings.chunk_overlap_seconds,
                        self.settings.min_chunk_characters,
                    )
                    chunks = [
                        Chunk(
                            video_id=job.video_id,
                            course_id=course_id,
                            video_title=job.title,
                            position=job.position,
                            start_sec=p["start_sec"],
                            end_sec=p["end_sec"],
                            text=p["text"],
                            sentences=p["sentences"],
                        )
                        for p in parts
                    ]
                    if not chunks:
                        failed += 1
                        _mark(job.video_id, "failed", "empty transcript")
                    else:
                        vecs = await asyncio.to_thread(
                            self.embedder.embed_texts, [c.text for c in chunks]
                        )
                        await asyncio.to_thread(self.store.upsert, course_id, chunks, vecs)
                        indexed += 1
                        _mark(job.video_id, "indexed")
                except Exception as e:
                    failed += 1
                    _mark(job.video_id, "failed", str(e)[:200])
                if job_store and job_id:
                    job_store.bump(job_id)

        await asyncio.gather(*(one(j) for j in jobs))
        if self.registry:
            try:
                await asyncio.to_thread(
                    self.registry.set_course_status,
                    course_id,
                    "ready" if indexed else "failed",
                )
            except Exception as e:
                log.warning("registry course status write failed: %s", e)
        try:
            chunk_count = await asyncio.to_thread(self.store.count_chunks, course_id)
        except Exception:
            chunk_count = 0
        return {
            "course_id": course_id,
            "requested": len(jobs) + skipped,
            "indexed": indexed,
            "failed": failed,
            "skipped": skipped,
            "chunk_count": chunk_count,
        }
