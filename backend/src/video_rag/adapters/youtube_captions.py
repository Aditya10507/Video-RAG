"""Fast caption API first. Prefers human > auto; translates when allowed."""

from __future__ import annotations

import time

from ..core.models import TranscriptDocument
from ..core.transcript_cleaning import dedupe_cues, words_with_times
from ..errors import TranscriptUnavailableError


class CaptionTranscriptProvider:
    def __init__(
        self,
        languages=("en",),
        allow_any: bool = True,
        retry_attempts: int = 3,
        base_delay: float = 1.0,
    ):
        self.languages = tuple(languages)
        self.allow_any = allow_any
        self.retry_attempts = retry_attempts
        self.base_delay = base_delay

    def fetch(self, video_id: str) -> TranscriptDocument:
        from youtube_transcript_api import YouTubeTranscriptApi

        last: Exception | None = None
        for attempt in range(self.retry_attempts):
            try:
                api = YouTubeTranscriptApi()
                tracks = api.list(video_id)
                track = None
                for lang in self.languages:
                    try:
                        track = tracks.find_transcript([lang])
                        break
                    except Exception:
                        continue
                if track is None:
                    if not self.allow_any:
                        raise TranscriptUnavailableError("no caption in requested languages")
                    avail = list(tracks)
                    if not avail:
                        raise TranscriptUnavailableError("video has no caption tracks at all")
                    track = avail[0]
                    try:
                        track = track.translate("en")
                    except Exception:
                        pass
                cues = track.fetch()
                norm = [{"text": c.text, "start": c.start, "duration": c.duration} for c in cues]
                clean = dedupe_cues(norm)
                if not clean:
                    raise TranscriptUnavailableError("empty transcript")
                words: list = []
                for cue in clean:
                    words.extend(words_with_times(cue))
                return TranscriptDocument(
                    video_id=video_id,
                    transcript_source="auto_captions",
                    raw_words=words,
                    clean_text=" ".join(c["text"] for c in clean),
                )
            except TranscriptUnavailableError:
                raise
            except Exception as e:
                last = e
                msg = str(e)
                if "429" in msg or "throttl" in msg.lower():
                    time.sleep(self.base_delay * (2**attempt))
                    continue
                time.sleep(self.base_delay)
        raise TranscriptUnavailableError(f"captions failed: {last}")
