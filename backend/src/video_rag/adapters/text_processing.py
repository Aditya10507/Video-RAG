"""Punctuation and jargon correction. Terminology refiner always runs when enabled."""

from __future__ import annotations


def restore_punctuation(text: str) -> str:
    return text  # optional heavy model lives behind punctuation_enabled


def refine_terminology(text: str, title: str, llm) -> str:
    """No is_enabled gate: caller decides. Kept targeted at technical terms only."""
    prompt = (
        f"Fix transcription errors in technical terms only. Do not reword.\n"
        f"Video title: {title}\nText: {text[:2000]}"
    )
    try:
        return llm.complete([{"role": "user", "content": prompt}], max_tokens=800)
    except Exception:
        return text
