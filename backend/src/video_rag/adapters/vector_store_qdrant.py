"""Production Qdrant index. One collection, filtered by course_id."""

from __future__ import annotations

import hashlib
from typing import Any

from ..core.models import Chunk
from ..core.ranking import lexical_score


def point_id(video_id: str, start: float, pipeline: str) -> str:
    h = hashlib.sha1(f"{video_id}:{start}:{pipeline}".encode()).hexdigest()
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


class QdrantVectorStore:
    def __init__(
        self, url: str, api_key: str | None, collection: str, pipeline_version: str = "v1"
    ):
        from qdrant_client import QdrantClient
        from qdrant_client.models import Distance, VectorParams

        self.client = QdrantClient(url=url, api_key=api_key, timeout=30)
        self.collection = collection
        self.pipeline = pipeline_version
        try:
            self.client.get_collection(collection)
        except Exception:
            self.client.create_collection(
                collection, vectors_config=VectorParams(size=1024, distance=Distance.COSINE)
            )

    def _scope_filter(self, course_id: str, video_id: str | None = None) -> Any:
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        must: list[Any] = [FieldCondition(key="course_id", match=MatchValue(value=course_id))]
        if video_id:
            must.append(FieldCondition(key="video_id", match=MatchValue(value=video_id)))
        return Filter(must=must)

    def upsert(self, course_id: str, chunks: list[Chunk], vectors: list[list[float]]) -> int:
        from qdrant_client.models import PointStruct

        points = [
            PointStruct(
                id=point_id(c.video_id, c.start_sec, self.pipeline),
                vector=v,
                payload={
                    "course_id": course_id,
                    "video_id": c.video_id,
                    "video_title": c.video_title,
                    "position": c.position,
                    "start_seconds": c.start_sec,
                    "end_seconds": c.end_sec,
                    "text": c.text,
                    "sentences": c.sentences,
                },
            )
            for c, v in zip(chunks, vectors)
        ]
        self.client.upsert(collection_name=self.collection, points=points)
        return len(points)

    def _to_chunk(self, course_id: str, pay: dict) -> Chunk:
        sents = pay.get("sentences") or []
        norm = [
            {"t": s.get("start", s.get("t", 0)), "s": s.get("text", s.get("s", ""))} for s in sents
        ]
        return Chunk(
            video_id=pay.get("video_id", ""),
            course_id=course_id,
            video_title=pay.get("video_title", ""),
            position=pay.get("position", 0),
            start_sec=float(pay.get("start_seconds", 0)),
            end_sec=float(pay.get("end_seconds", 0)),
            text=pay.get("text", ""),
            sentences=norm,
        )

    def search_dense(
        self, course_id: str, query_vector: list[float], top_k: int, video_id: str | None = None
    ) -> list[tuple[Chunk, float]]:
        res = self.client.query_points(
            collection_name=self.collection,
            query=query_vector,
            query_filter=self._scope_filter(course_id, video_id),
            limit=top_k,
            with_payload=True,
        ).points
        return [(self._to_chunk(course_id, p.payload or {}), float(p.score)) for p in res]

    def search_keyword(
        self, course_id: str, query: str, top_k: int, video_id: str | None = None
    ) -> list[tuple[Chunk, float]]:
        pts, _ = self.client.scroll(
            collection_name=self.collection,
            scroll_filter=self._scope_filter(course_id, video_id),
            limit=500,
            with_payload=True,
        )
        out = [
            (
                self._to_chunk(course_id, p.payload or {}),
                lexical_score(query, (p.payload or {}).get("text", "")),
            )
            for p in pts
        ]
        return sorted(out, key=lambda x: -x[1])[:top_k]

    def count_chunks(self, course_id: str, video_id: str | None = None) -> int:
        return self.client.count(
            collection_name=self.collection,
            count_filter=self._scope_filter(course_id, video_id),
            exact=False,
        ).count

    def count_chunks_by_course_and_video(
        self, course_ids: list[str]
    ) -> dict[str, dict[str, int]]:
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        if not course_ids:
            return {}
        counts: dict[str, dict[str, int]] = {}
        offset = None
        while True:
            points, offset = self.client.scroll(
                collection_name=self.collection,
                scroll_filter=Filter(
                    should=[
                        FieldCondition(key="course_id", match=MatchValue(value=course_id))
                        for course_id in course_ids
                    ]
                ),
                limit=2048,
                offset=offset,
                with_payload=["course_id", "video_id"],
                with_vectors=False,
            )
            for point in points:
                payload = point.payload or {}
                point_course_id = payload.get("course_id")
                video_id = payload.get("video_id")
                if point_course_id in course_ids and video_id:
                    video_counts = counts.setdefault(point_course_id, {})
                    video_counts[video_id] = video_counts.get(video_id, 0) + 1
            if offset is None:
                return counts

    def count_chunks_by_video(self, course_id: str) -> dict[str, int]:
        return self.count_chunks_by_course_and_video([course_id]).get(course_id, {})
