"""Sentence-aware windows of about a minute. Timings stay word-anchored."""

from __future__ import annotations

import re

SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")


def split_sentences(text: str) -> list[str]:
    parts = [p.strip() for p in SENT_SPLIT.split(text.strip()) if p.strip()]
    return parts or ([text.strip()] if text.strip() else [])


def chunk_words(
    words: list[dict], window: int = 60, overlap: int = 15, min_chars: int = 80
) -> list[dict]:
    """words: [{word, start}]. Returns [{start_sec, end_sec, text}]."""
    if not words:
        return []
    full = " ".join(w["word"] for w in words)
    sentences = split_sentences(full)
    # Map each sentence to a start time by scanning word offsets.
    spans: list = []
    idx = 0
    for sent in sentences:
        n = len(sent.split())
        w0 = words[idx] if idx < len(words) else words[-1]
        w1 = words[min(idx + max(0, n - 1), len(words) - 1)]
        spans.append({"text": sent, "start": float(w0["start"]), "end": float(w1["start"]) + 2.0})
        idx += n
    out: list = []
    i = 0
    while i < len(spans):
        j = i
        texts: list = []
        while j < len(spans) and spans[j]["start"] - spans[i]["start"] < window:
            texts.append(spans[j])
            j += 1
        if not texts:
            texts = [spans[i]]
            j = i + 1
        text = " ".join(t["text"] for t in texts)
        if len(text) >= min_chars or i == 0:
            out.append(
                {
                    "start_sec": texts[0]["start"],
                    "end_sec": texts[-1]["end"],
                    "text": text,
                    "sentences": [{"t": t["start"], "s": t["text"]} for t in texts],
                }
            )
        # Overlap: step back while still covering overlap seconds.
        nxt = j
        while nxt > i + 1 and spans[nxt - 1]["start"] > spans[i]["start"] + window - overlap:
            nxt -= 1
        i = max(i + 1, nxt) if nxt <= i else nxt
        if j >= len(spans):
            break
    return out
