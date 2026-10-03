"""Tidy caption text, keep the timings. Timing truth is raw_words only."""

from __future__ import annotations

import re

_WS = re.compile(r"\s+")


def dedupe_cues(cues: list[dict]) -> list[dict]:
    """Remove rolling-caption repetition: cue prefix == previous suffix."""
    out: list = []
    prev = ""
    for cue in cues:
        text = _WS.sub(" ", (cue.get("text") or "")).strip()
        if not text:
            continue
        if prev and (text in prev or prev.endswith(text[:40])):
            continue
        # Strip overlapping prefix.
        for k in range(min(len(prev), len(text), 120), 10, -1):
            if text.startswith(prev[-k:]):
                text = text[k:].strip()
                break
        if text:
            out.append(
                {
                    "text": text,
                    "start": float(cue.get("start", 0)),
                    "duration": float(cue.get("duration", 2.0)),
                }
            )
            prev = (prev + " " + text)[-400:]
    return out


def words_with_times(cue: dict) -> list[dict]:
    words = (cue.get("text") or "").split()
    if not words:
        return []
    total = sum(len(w) for w in words) or 1
    t, out = float(cue["start"]), []
    for w in words:
        out.append({"word": w, "start": t})
        t += float(cue.get("duration", 2.0)) * len(w) / total
    return out


def normalize_text(text: str) -> str:
    text = _WS.sub(" ", text.lower()).strip()
    for a, b in (
        ("hash map", "hashmap"),
        ("hash table", "hashmap"),
        ("big oh", "big o"),
        ("time complexity", "complexity"),
    ):
        text = text.replace(a, b)
    return text
