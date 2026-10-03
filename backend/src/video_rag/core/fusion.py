"""Merge results from several searches with Reciprocal Rank Fusion."""

from __future__ import annotations


def rrf(lists: list[list], k: int = 60) -> list:
    scores: dict = {}
    for lst in lists:
        for rank, item in enumerate(lst):
            key = getattr(item, "video_id", None) or str(item)
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores.items(), key=lambda x: -x[1])
