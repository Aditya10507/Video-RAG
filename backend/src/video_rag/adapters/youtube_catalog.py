"""yt-dlp playlist expansion. CHANNEL passes source_url through untouched."""

from __future__ import annotations

from ..core.models import VideoJob
from ..errors import CatalogUnavailableError

_TABS = (
    "/videos",
    "/shorts",
    "/streams",
    "/live",
    "/featured",
    "/playlists",
    "/community",
    "/about",
)


def build_target_url(parsed: dict) -> str:
    kind = parsed.get("kind")
    src = parsed.get("source_url", "")
    if kind == "channel":
        # Preserve @handle / /channel/ / /c/ / /user/ exactly; yt-dlp 404s otherwise.
        base = src.split("?")[0].rstrip("/")
        if (
            any(base.endswith(t) or f"{t}/" in src for t in _TABS)
            or "/@" in base
            or "/channel/" in base
        ):
            if any(base.endswith(t) for t in _TABS):
                return base
            if "/@" in base or "/channel/" in base or "/c/" in base or "/user/" in base:
                return base if any(base.endswith(t) for t in _TABS) else base + "/videos"
        return base + "/videos"
    return src


class YtDlpCatalogProvider:
    def __init__(self, max_videos: int = 500, timeout: float = 30.0):
        self.max_videos = max_videos
        self.timeout = timeout

    def expand(self, parsed_url: dict) -> tuple[dict, list[VideoJob]]:
        try:
            from yt_dlp import YoutubeDL
        except ImportError as e:
            raise CatalogUnavailableError(f"yt-dlp missing: {e}") from e
        target = build_target_url(parsed_url)
        opts = {
            "quiet": True,
            "skip_download": True,
            "extract_flat": True,
            "ignoreerrors": True,
            "socket_timeout": self.timeout,
        }
        try:
            with YoutubeDL(opts) as ydl:
                info = ydl.extract_info(target, download=False)
        except Exception as e:
            raise CatalogUnavailableError(f"listing failed: {e}") from e
        if not info:
            raise CatalogUnavailableError("URL returned no usable metadata")
        entries = info.get("entries") or []
        if info.get("_type") in (None, "video") and info.get("id"):
            entries = [info]
        jobs: list[VideoJob] = []
        title = info.get("title") or target
        for pos, e in enumerate(entries[: self.max_videos]):
            if not e or not e.get("id"):
                continue
            jobs.append(
                VideoJob(
                    video_id=e["id"],
                    course_id="",
                    title=e.get("title") or "",
                    duration_sec=int(e.get("duration") or 0),
                    position=pos,
                )
            )
        if not jobs:
            raise CatalogUnavailableError("URL returned no usable metadata")
        return {"title": title, "source_url": target}, jobs
