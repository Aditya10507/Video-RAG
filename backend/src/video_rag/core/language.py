"""Answer language mirrors the query. Generic mirror + Hinglish/Hindi guard.

The multilingual embedder understands Hinglish, but English excerpts pull the
model back to English without an explicit directive. Detection stays tiny so
the English path keeps its zero-LLM-call refusal.
"""

from __future__ import annotations

import re

_DEVANAGARI = re.compile(r"[\u0900-\u097F]")

_STRONG_MARKERS = {
    "ka",
    "ki",
    "ke",
    "hai",
    "hain",
    "kya",
    "kaise",
    "kahan",
    "hota",
    "hote",
    "hoti",
    "nahi",
    "matlab",
    "samjhao",
    "batao",
}

_GENERIC = "Reply in the same language and script as the Question."


def detect_answer_language(question: str) -> str:
    if _DEVANAGARI.search(question or ""):
        return "hindi"
    words = set(re.findall(r"[a-z]+", (question or "").lower()))
    if words & _STRONG_MARKERS:
        return "hinglish"
    return "english"


def language_directive(lang: str) -> str:
    if lang == "hinglish":
        return (
            _GENERIC + " For Hinglish written in Roman script, reply in Hinglish in Roman script."
        )
    if lang == "hindi":
        return _GENERIC + " For Hindi in Devanagari, reply in Hindi in Devanagari."
    return _GENERIC


def language_label(lang: str) -> str:
    return {"hindi": "Hindi", "hinglish": "Hinglish"}.get(lang, "English")
