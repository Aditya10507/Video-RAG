"""Refine the start second of a match. 3s lead-in so the player lands early."""

from __future__ import annotations

from .ranking import tokens


def format_label(seconds: float) -> str:
    s = max(0, int(seconds))
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def refine(
    chunk_sentences: list[dict], query: str, chunk_start: float, lead_in: int = 3
) -> tuple[float, str]:
    """Pick the sentence that best matches the query; start lead_in before it.

    Sentences score by stemmed word overlap (the same normalizer retrieval uses),
    so "hashmaps" in the question matches "hashmap" in the transcript. A sentence
    with zero overlap never moves the timestamp: with no match the chunk start
    stands.
    """
    q = set(tokens(query))
    best_t = chunk_start
    best_hit = 0
    for sent in chunk_sentences:
        text = sent.get("s") or sent.get("text") or ""
        t = float(sent.get("t", sent.get("start", chunk_start)))
        hit = len(q & set(tokens(text)))
        if hit > best_hit:
            best_hit = hit
            best_t = t
    start = max(0.0, best_t - lead_in)
    return start, format_label(start)


def youtube_url(video_id: str, seconds: float) -> str:
    return f"https://www.youtube.com/watch?v={video_id}&t={int(seconds)}s"
