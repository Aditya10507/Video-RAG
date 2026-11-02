"""Request models with validation limits on every field."""

from __future__ import annotations

from pydantic import BaseModel, Field


class IngestRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2000)
    force: bool = False


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    course_id: str = Field(min_length=1, max_length=200)
    video_id: str | None = Field(default=None, max_length=50)


class TranslateRequest(BaseModel):
    answer: str = Field(min_length=1, max_length=12000)
    target_language: str = Field(min_length=2, max_length=40)


class SpeakRequest(BaseModel):
    """Text to read aloud (matches the Gnani Timbre TTS payload)."""

    text: str = Field(min_length=1, max_length=4000)
    # "auto" lets the backend voice the language the answer was displayed in;
    # an explicit value like "hi-IN" or "hi-en" forces a synthesis language.
    language: str = Field(default="auto", max_length=10)
