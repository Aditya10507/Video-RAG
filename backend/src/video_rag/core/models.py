"""Domain objects shared across layers."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class VideoJob:
    video_id: str
    course_id: str
    title: str = ""
    duration_sec: int = 0
    position: int = 0


@dataclass
class Word:
    word: str
    start: float


@dataclass
class TranscriptDocument:
    video_id: str
    title: str = ""
    duration_sec: int = 0
    transcript_source: str = "auto_captions"
    pipeline_version: str = "v1"
    raw_words: list = field(default_factory=list)  # list[Word-like dicts]
    clean_text: str = ""


@dataclass
class Chunk:
    video_id: str
    course_id: str
    video_title: str = ""
    position: int = 0
    start_sec: float = 0.0
    end_sec: float = 0.0
    text: str = ""
    sentences: list = field(default_factory=list)  # [{t, s}]


@dataclass
class ScoredChunk:
    chunk: Chunk
    score: float


@dataclass
class Citation:
    video_id: str
    video_title: str
    url: str
    timestamp_label: str
    start_seconds: float
