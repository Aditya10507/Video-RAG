"""Timestamp hit rate: share of answer citations overlapping a GT segment
(widened by tolerance on both sides). 0 when the answer has no citations."""

from __future__ import annotations


def timestamp_hit_rate(
    citations: list[dict], gt: list[dict], tolerance_s: float
) -> float | None:
    """NULL for unanswerable questions (no GT to hit); otherwise 0..1."""
    if not gt:
        return None
    if not citations:
        return 0.0
    wide = [
        {"video_id": g.get("video_id"),
         "start_s": float(g.get("start_s", 0)) - tolerance_s,
         "end_s": float(g.get("end_s", 0)) + tolerance_s}
        for g in gt
    ]
    hit = 0
    for c in citations:
        seg = {"video_id": c.get("video_id"), "start_s": float(c.get("start_s", 0)),
               "end_s": float(c.get("end_s", c.get("start_s", 0)))}
        if seg["video_id"] is None:
            continue
        ok = any(
            seg["video_id"] == g["video_id"]
            and min(seg["end_s"], g["end_s"]) - max(seg["start_s"], g["start_s"]) > 0
            for g in wide
        )
        hit += 1 if ok else 0
    return hit / len(citations)
