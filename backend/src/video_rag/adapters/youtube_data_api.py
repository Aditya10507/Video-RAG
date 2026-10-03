"""YouTube Data API v3 catalog provider (opt-in). Catalog only.

Transcripts still use captions/ASR; audio still uses yt-dlp. No new dependency.
"""

from __future__ import annotations

import re

import httpx

from ..core.models import VideoJob
from ..errors import CatalogUnavailableError

_ISO = re.compile(r"P(?:(\d+)W)?(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")


def parse_iso8601_duration(raw: str) -> int:
    if not raw:
        return 0
    m = _ISO.fullmatch(raw.strip())
    if not m:
        return 0
    w, d, h, mi, s = (int(x or 0) for x in m.groups())
    return w * 604800 + d * 86400 + h * 3600 + mi * 60 + s


def _is_unavailable_title(title: str) -> bool:
    low = (title or "").strip().lower()
    return low in ("deleted video", "private video") or low.startswith("deleted ")


class YoutubeDataApiCatalogProvider:
    def __init__(self, api_key: str, max_videos: int = 500, timeout_seconds: float = 30.0):
        if not api_key:
            raise CatalogUnavailableError("YOUTUBE_API_KEY is required")
        self.api_key = api_key
        self.max_videos = max_videos
        self.timeout = timeout_seconds
        self.base = "https://www.googleapis.com/youtube/v3"

    def _get(self, path: str, params: dict) -> dict:
        params = {**params, "key": self.api_key}
        try:
            r = httpx.get(f"{self.base}/{path}", params=params, timeout=self.timeout)
        except Exception as e:
            raise CatalogUnavailableError(f"YouTube API unreachable: {e}") from e
        if r.status_code in (400, 403):
            raise CatalogUnavailableError(
                f"YouTube API key error ({r.status_code}): {r.text[:200]}"
            )
        if r.status_code == 429:
            raise CatalogUnavailableError("YouTube quota exceeded, retry later")
        if r.status_code != 200:
            raise CatalogUnavailableError(f"YouTube API {r.status_code}: {r.text[:200]}")
        return r.json()

    def expand(self, parsed_url: dict) -> tuple[dict, list[VideoJob]]:
        kind = parsed_url.get("kind")
        if kind == "video":
            return self._video(parsed_url["video_id"])
        if kind == "playlist":
            return self._playlist(parsed_url["playlist_id"])
        if kind == "channel":
            return self._channel(parsed_url)
        raise CatalogUnavailableError("ambiguous URL needs a user choice first")

    def _video(self, vid: str) -> tuple[dict, list[VideoJob]]:
        d = self._get("videos", {"part": "snippet,contentDetails,status", "id": vid})
        items = d.get("items") or []
        if not items:
            raise CatalogUnavailableError("video not found/private/deleted")
        sn = items[0]["snippet"]
        dur = parse_iso8601_duration((items[0].get("contentDetails") or {}).get("duration", ""))
        job = VideoJob(video_id=vid, course_id="", title=sn.get("title", ""), duration_sec=dur)
        return {"title": sn.get("title", vid), "source_url": parsed_url_source(vid)}, [job]

    def _playlist(self, pid: str) -> tuple[dict, list[VideoJob]]:
        meta = self._get("playlists", {"part": "snippet", "id": pid})
        title = ((meta.get("items") or [{}])[0].get("snippet") or {}).get("title", pid)
        ids: list[str] = []
        token = None
        while len(ids) < self.max_videos:
            p = {"part": "contentDetails", "playlistId": pid, "maxResults": 50}
            if token:
                p["pageToken"] = token
            d = self._get("playlistItems", p)
            for it in d.get("items") or []:
                vid = (it.get("contentDetails") or {}).get("videoId") or ""
                title = str((it.get("snippet") or {}).get("title") or "")
                if vid and not _is_unavailable_title(title):
                    ids.append(vid)
            token = d.get("nextPageToken")
            if not token:
                break
        jobs = self._fill(ids)
        return {"title": title, "source_url": f"https://www.youtube.com/playlist?list={pid}"}, jobs

    def _channel(self, parsed: dict) -> tuple[dict, list[VideoJob]]:
        ref = parsed.get("channel_ref", "")
        src = parsed.get("source_url", "")
        channel_id = None
        if "/channel/" in src:
            channel_id = src.split("/channel/")[1].split("/")[0].split("?")[0]
        if not channel_id and ref and not src.startswith("http"):
            channel_id = ref
        if not channel_id and ref.startswith("UC"):
            channel_id = ref
        if not channel_id:
            # forHandle lookup for @handles
            handle = ref if ref.startswith("@") else f"@{ref}"
            try:
                d = self._get("channels", {"part": "id", "forHandle": handle})
                if d.get("items"):
                    channel_id = d["items"][0]["id"]
            except CatalogUnavailableError:
                channel_id = None
        if not channel_id:
            raise CatalogUnavailableError("channel lookup failed")
        d = self._get("channels", {"part": "contentDetails,snippet", "id": channel_id})
        items = d.get("items") or []
        if not items:
            raise CatalogUnavailableError("channel not found")
        uploads = ((items[0].get("contentDetails") or {}).get("relatedPlaylists") or {}).get(
            "uploads", ""
        )
        title = (items[0].get("snippet") or {}).get("title", channel_id)
        info, jobs = self._playlist(uploads)
        info["title"] = title
        return info, jobs

    def _fill(self, ids: list[str]) -> list[VideoJob]:
        jobs: list[VideoJob] = []
        for i in range(0, len(ids[: self.max_videos]), 50):
            batch = ids[i : i + 50]
            d = self._get("videos", {"part": "snippet,contentDetails", "id": ",".join(batch)})
            for it in d.get("items") or []:
                sn = it.get("snippet", {})
                dur = parse_iso8601_duration((it.get("contentDetails") or {}).get("duration", ""))
                jobs.append(
                    VideoJob(
                        video_id=it["id"],
                        course_id="",
                        title=sn.get("title", ""),
                        duration_sec=dur,
                        position=len(jobs),
                    )
                )
        if not jobs:
            raise CatalogUnavailableError("URL returned no usable metadata")
        return jobs


def parsed_url_source(vid: str) -> str:
    return f"https://www.youtube.com/watch?v={vid}"


def build_data_api_catalog(settings) -> YoutubeDataApiCatalogProvider:
    if not settings.youtube_api_key:
        raise CatalogUnavailableError("VIDEO_RAG_YOUTUBE_API_KEY is required")
    return YoutubeDataApiCatalogProvider(
        settings.youtube_api_key, settings.max_videos_per_course, settings.http_timeout_seconds
    )
