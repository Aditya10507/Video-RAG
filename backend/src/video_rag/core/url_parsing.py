"""Classify a pasted link. watch?v=X&list=Y is ambiguous: ask the user."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse


def _norm(url: str) -> str:
    url = url.strip()
    if url.startswith("//"):
        return "https:" + url
    if "://" not in url:
        return "https://" + url
    return url


def _is_host(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def _require(value: str, message: str) -> str:
    if not (value or "").strip():
        raise ValueError(message)
    return value


def parse_url(url: str) -> dict:
    raw = _norm(url)
    u = urlparse(raw)
    host = (u.hostname or "").lower()
    path = u.path or ""
    qs = parse_qs(u.query)
    if _is_host(host, "youtu.be"):
        vid = path.strip("/").split("/")[0] if path.strip("/") else ""
        _require(vid, "could not classify URL")
        return {"kind": "video", "video_id": vid, "source_url": raw}
    if not (_is_host(host, "youtube.com") or _is_host(host, "youtube-nocookie.com")):
        raise ValueError("not a YouTube URL")
    if path.startswith("/shorts/"):
        parts = path.split("/")
        vid = parts[2] if len(parts) > 2 else ""
        _require(vid, "could not classify URL")
        return {"kind": "video", "video_id": vid, "source_url": raw}
    if path.startswith("/watch"):
        vid = (qs.get("v") or [""])[0]
        lst = (qs.get("list") or [""])[0]
        if vid and lst:
            return {"kind": "ambiguous", "video_id": vid, "playlist_id": lst, "source_url": raw}
        if lst:
            _require(lst, "could not classify URL")
            return {"kind": "playlist", "playlist_id": lst, "source_url": raw}
        _require(vid, "could not classify URL")
        return {"kind": "video", "video_id": vid, "source_url": raw}
    if path.startswith("/playlist"):
        pid = (qs.get("list") or [""])[0]
        _require(pid, "could not classify URL")
        return {"kind": "playlist", "playlist_id": pid, "source_url": raw}
    # Channels: keep stable course_id by stripping @ and ignoring the tab.
    for prefix in ("/channel/", "/c/", "/user/"):
        if path.startswith(prefix):
            ref = path[len(prefix) :].split("/")[0]
            return {"kind": "channel", "channel_ref": ref, "source_url": raw}
    if path.startswith("/@"):
        ref = path[2:].split("/")[0]
        return {"kind": "channel", "channel_ref": ref, "source_url": raw}
    if "/@" in path:
        ref = path.split("/@")[-1].split("/")[0].lstrip("@")
        return {"kind": "channel", "channel_ref": ref, "source_url": raw}
    vid = (qs.get("v") or [""])[0]
    if vid:
        return {"kind": "video", "video_id": vid, "source_url": raw}
    raise ValueError("could not classify URL")


def course_id_for(parsed: dict) -> str:
    kind = parsed.get("kind")
    if kind == "video":
        return f"video:{parsed['video_id']}"
    if kind == "playlist":
        return parsed["playlist_id"]
    if kind == "channel":
        return f"channel:{parsed['channel_ref']}"
    raise ValueError("ambiguous URL needs a user choice first")
