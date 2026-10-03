"""yt-dlp subtitle fallback. Same tracks via watch data, runs on API miss."""

from __future__ import annotations

from ..core.models import TranscriptDocument
from ..errors import TranscriptUnavailableError


class YtDlpSubtitleProvider:
    def fetch(self, video_id: str) -> TranscriptDocument:
        try:
            from yt_dlp import YoutubeDL
        except ImportError as e:
            raise TranscriptUnavailableError(f"yt-dlp missing: {e}") from e
        opts = {
            "quiet": True,
            "skip_download": True,
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": ["en.*", "en"],
            "subtitlesformat": "json3",
        }
        try:
            with YoutubeDL(opts) as ydl:
                info = ydl.extract_info(
                    f"https://www.youtube.com/watch?v={video_id}", download=False
                )
        except Exception as e:
            raise TranscriptUnavailableError(f"subtitle download failed: {e}") from e
        subs = (info or {}).get("subtitles") or (info or {}).get("automatic_captions") or {}
        if not subs:
            raise TranscriptUnavailableError("video has no caption tracks at all")
        raise TranscriptUnavailableError("yt-dlp subtitle parse not configured; use captions")
