"""Deterministic scorers. Formulas in docstrings; NULL means not computable."""

from __future__ import annotations


def overlap(r: dict, g: dict) -> float:
    """Seconds of time overlap if same video, else 0."""
    if r.get("video_id") != g.get("video_id"):
        return 0.0
    return max(0.0, min(float(r.get("end_s", 0)), float(g.get("end_s", 0)))
               - max(float(r.get("start_s", 0)), float(g.get("start_s", 0))))


def relevant_flags(chunks: list[dict], gt: list[dict], min_overlap_s: float) -> list[bool]:
    """Chunk i is relevant iff it overlaps some GT segment by >= min_overlap_s."""
    return [any(overlap(c, g) >= min_overlap_s for g in gt) for c in chunks]


def segment_coverage(chunks: list[dict], gt: list[dict], min_overlap_s: float, k: int) -> float:
    """Recall@k: share of GT segments with >= 1 relevant chunk in top-k."""
    top = chunks[:k] if k > 0 else []
    if not gt:
        return 0.0
    hit = sum(
        1 for g in gt
        if any(overlap(c, g) >= min_overlap_s for c in top)
    )
    return hit / len(gt)


def hit_at_k(flags: list[bool], k: int) -> float:
    """1 if any chunk in top-k is relevant, else 0."""
    return 1.0 if any(flags[:k] if k > 0 else []) else 0.0


def mrr(chunks: list[dict], gt: list[dict], min_overlap_s: float, k_max: int) -> float:
    """1 / rank of the first relevant chunk within k_max, else 0."""
    for c in chunks[:k_max] if k_max > 0 else []:
        if any(overlap(c, g) >= min_overlap_s for g in gt):
            return 1.0 / max(1, int(c.get("rank", 0) or 0))
    return 0.0


def precision_at_k(flags: list[bool], k: int) -> float | None:
    """Relevant chunks in top-k / len(top-k). NULL when top-k is empty."""
    top = flags[:k] if k > 0 else []
    if not top:
        return None
    return sum(1 for f in top if f) / len(top)
