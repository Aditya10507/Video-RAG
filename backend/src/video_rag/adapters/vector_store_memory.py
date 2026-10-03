"""Local JSON index. Good for dev/tests. Course-filtered like Qdrant."""

from __future__ import annotations

import json
from pathlib import Path

from ..core.models import Chunk
from ..core.ranking import lexical_score


class MemoryVectorStore:
    def __init__(self, path: str | Path = "data/memory_store.json"):
        import logging

        self.path = Path(path)
        self.items: list[dict] = []
        self.log = logging.getLogger(__name__)
        if self.path.exists():
            try:
                self.items = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception as e:
                try:
                    bak = self.path.with_suffix(".corrupt.bak")
                    self.path.rename(bak)
                except Exception:
                    pass
                self.log.warning("memory store corrupt, starting empty: %s", e)
                self.items = []

    def _save(self) -> None:
        import os
        import tempfile

        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), prefix=".store-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.items, f)
            os.replace(tmp, self.path)
        except Exception:
            try:
                os.unlink(tmp)
            except Exception:
                pass
            raise

    def upsert(self, course_id: str, chunks: list[Chunk], vectors: list[list[float]]) -> int:
        drop = {(course_id, c.video_id, c.start_sec) for c in chunks}
        if drop:
            self.items = [
                i
                for i in self.items
                if (i["course_id"], i["video_id"], i["start_sec"]) not in drop
            ]
        for c, v in zip(chunks, vectors):
            self.items.append(
                {
                    "course_id": course_id,
                    "video_id": c.video_id,
                    "video_title": c.video_title,
                    "position": c.position,
                    "start_sec": c.start_sec,
                    "end_sec": c.end_sec,
                    "text": c.text,
                    "sentences": c.sentences,
                    "vector": v,
                }
            )
        self._save()
        return len(chunks)

    def _scoped(self, course_id: str, video_id: str | None = None) -> list[dict]:
        return [
            i
            for i in self.items
            if i["course_id"] == course_id and (video_id is None or i["video_id"] == video_id)
        ]

    def search_dense(
        self, course_id: str, query_vector: list[float], top_k: int, video_id: str | None = None
    ) -> list[tuple[Chunk, float]]:
        import math

        out = []
        qn = math.sqrt(sum(x * x for x in query_vector)) or 1.0
        for i in self._scoped(course_id, video_id):
            v = i["vector"]
            dot = sum(a * b for a, b in zip(query_vector, v))
            vn = math.sqrt(sum(x * x for x in v)) or 1.0
            score = dot / (qn * vn)
            out.append(
                (
                    Chunk(
                        video_id=i["video_id"],
                        course_id=course_id,
                        video_title=i["video_title"],
                        position=i["position"],
                        start_sec=i["start_sec"],
                        end_sec=i["end_sec"],
                        text=i["text"],
                        sentences=i["sentences"],
                    ),
                    score,
                )
            )
        return sorted(out, key=lambda x: -x[1])[:top_k]

    def search_keyword(
        self, course_id: str, query: str, top_k: int, video_id: str | None = None
    ) -> list[tuple[Chunk, float]]:
        out = []
        for i in self._scoped(course_id, video_id):
            out.append(
                (
                    Chunk(
                        video_id=i["video_id"],
                        course_id=course_id,
                        video_title=i["video_title"],
                        position=i["position"],
                        start_sec=i["start_sec"],
                        end_sec=i["end_sec"],
                        text=i["text"],
                        sentences=i["sentences"],
                    ),
                    lexical_score(query, i["text"]),
                )
            )
        return sorted(out, key=lambda x: -x[1])[:top_k]

    def count_chunks(self, course_id: str, video_id: str | None = None) -> int:
        return len(self._scoped(course_id, video_id))

    def count_chunks_by_course_and_video(
        self, course_ids: list[str]
    ) -> dict[str, dict[str, int]]:
        counts: dict[str, dict[str, int]] = {}
        selected_courses = set(course_ids)
        for item in self.items:
            course_id = item["course_id"]
            if course_id not in selected_courses:
                continue
            video_counts = counts.setdefault(course_id, {})
            video_id = item["video_id"]
            video_counts[video_id] = video_counts.get(video_id, 0) + 1
        return counts

    def count_chunks_by_video(self, course_id: str) -> dict[str, int]:
        return self.count_chunks_by_course_and_video([course_id]).get(course_id, {})
