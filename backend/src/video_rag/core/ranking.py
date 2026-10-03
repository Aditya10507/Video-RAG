"""Score chunks, then score videos. Dedicated lecture beats passing mention."""

from __future__ import annotations

import math
import re

_TOKEN = re.compile(r"[a-z0-9]+")


def _stem(word: str) -> str:
    # Light English stemming so "hashmaps" matches "hashmap", "used" -> "use".
    if len(word) > 4 and word.endswith("ing"):
        word = word[:-3]
    if len(word) > 3 and word.endswith("es"):
        word = word[:-2]
    elif len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        word = word[:-1]
    if len(word) > 3 and word.endswith("ed"):
        word = word[:-1]  # used -> use, cached -> cache
    return word


def tokens(text: str) -> list[str]:
    text = text.lower().replace("hash map", "hashmap").replace("hash table", "hashmap")
    return [_stem(w) for w in _TOKEN.findall(text)]


def lexical_score(query: str, text: str) -> float:
    q = tokens(query)
    if not q:
        return 0.0
    t = tokens(text)
    if not t:
        return 0.0
    vocab = set(t)
    hits = sum(1 for w in set(q) if w in vocab)
    # Saturating TF: long Hinglish queries don't dilute to zero.
    tf = sum(min(3, t.count(w)) for w in set(q) if w in vocab)
    return (hits / len(set(q))) * (1.0 - 1.0 / (1.0 + tf / 4.0))


def rank_videos(scored: list[tuple], lambda_coverage: float = 0.3) -> list[tuple]:
    """scored: [(video_id, chunk_score, chunk)]. Returns [(video_id, score)]."""
    by_video: dict = {}
    for vid, score, _chunk in scored:
        by_video.setdefault(vid, []).append(float(score))
    ranked = []
    for vid, scores in by_video.items():
        ranked.append((vid, max(scores) + lambda_coverage * math.log(1 + len(scores))))
    return sorted(ranked, key=lambda x: -x[1])
