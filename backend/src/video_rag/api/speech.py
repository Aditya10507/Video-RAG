"""Voice endpoints: Gnani Prisma STT + Timbre TTS proxies.

The browser records a question, the server forwards it to Gnani's
speech-to-text; answers are read aloud through Gnani's text-to-speech.
Both endpoints only exist when GNANI_API_KEY is configured (500 otherwise)
so the app degrades to typed questions with a clear error, never a crash.
"""

from __future__ import annotations

import asyncio
import hmac
import json

import httpx
import websockets
from fastapi import APIRouter, Depends, File, Request, UploadFile, WebSocket
from fastapi.responses import JSONResponse, Response

from ..config import Settings
from .schemas import SpeakRequest
from .security import api_key_header, check_key

router = APIRouter()

_STT_EXTENSIONS = {"wav", "mp3", "ogg", "flac", "aac", "m4a"}

# Gnani advertises MP3 for TTS; sample rate 24 kHz matches Timbre's output.
_TTS_CONTAINER = "mp3"
_TTS_SAMPLE_RATE = 24000

_TTS_MEDIA_TYPES = {
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "ogg": "audio/ogg",
    "raw": "audio/wav",
}

# Wrap the whole upstream call with some headroom for synthesis time; config
# http_timeout is tuned for the local pipeline (embedding/LLM), not TTS.
_CLIENT_TIMEOUT = httpx.Timeout(60.0, connect=10.0)


def _settings(request: Request) -> Settings:
    return request.app.state.container.settings


def err(code: str, message: str, status: int = 400):
    return JSONResponse({"code": code, "message": message}, status_code=status)


def _upstream_error(response: httpx.Response) -> JSONResponse:
    """Map Gnani's error shapes onto the app's single error envelope.

    Gnani returns a mix: auth/rate-limit errors use
    ``{"detail": {"error_code": ..., "message": ...}}``; STT failures use
    ``{"success": false, "error": {"type", "message"}}``; TTS failures use a
    flat ``{"success": false, "message", "status_code"}``.
    """
    status = response.status_code
    try:
        body = response.json()
    except (json.JSONDecodeError, ValueError):
        body = {}

    detail = body.get("detail") if isinstance(body, dict) else None
    code_map = {
        "MISSING_API_KEY": "gnani_auth",
        "INVALID_API_KEY": "gnani_auth",
        "SCOPE_NOT_ALLOWED": "gnani_scope",
        "RATE_LIMITED": "gnani_rate_limited",
    }

    if isinstance(detail, dict):
        code = code_map.get(detail.get("error_code"), "gnani_error")
        if status == 402:
            code = "gnani_out_of_credit"
        return err(code, str(detail.get("message") or body), status)

    if isinstance(body, dict):
        if isinstance(body.get("error"), dict):
            return err("gnani_error", str(body["error"].get("message") or body), status)
        if body.get("message"):
            code = "gnani_out_of_credit" if status == 402 else "gnani_error"
            return err(code, str(body["message"]), status)

    if status == 402:
        return err("gnani_out_of_credit", "Gnani credits exhausted", status)
    return err("gnani_error", f"upstream returned {status}", status)


async def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=_CLIENT_TIMEOUT, follow_redirects=True)


@router.post("/voice/transcribe")
async def transcribe(
    request: Request,
    x_api_key=Depends(api_key_header),
    audio_file: UploadFile = File(...),
    language: str | None = None,
):
    """Speech-to-text: multipart audio in, ``{"text", "language"}`` out."""
    check_key(_settings(request), x_api_key)
    s = _settings(request)
    if not s.gnani_api_key:
        return err("not_configured", "voice is not configured (missing GNANI key)", 503)

    filename = audio_file.filename or ""
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if extension not in _STT_EXTENSIONS:
        return err(
            "bad_audio",
            "unsupported audio type (need one of: " + ", ".join(sorted(_STT_EXTENSIONS)) + ")",
            415,
        )

    payload = await audio_file.read()
    if not payload:
        return err("bad_audio", "empty audio file", 400)
    if len(payload) > s.gnani_max_audio_bytes:
        return err("audio_too_large", f"audio exceeds {s.gnani_max_audio_bytes} bytes", 413)

    # Prisma's REST endpoint takes clips of up to 30 s; the browser converts
    # recordings to 16 kHz mono WAV before upload.
    form = {
        "model": s.gnani_stt_model,
        "language_code": (language or s.gnani_stt_languages) or "indic-auto",
        "format": s.gnani_stt_format,
    }
    files = {"audio_file": (filename or "recording.wav", payload, audio_file.content_type)}

    client = await _client()
    try:
        response = await client.post(
            f"{s.gnani_base_url}/stt/v3",
            headers={"X-API-Key-ID": s.gnani_api_key},
            data=form,
            files=files,
        )
    except httpx.HTTPError as e:
        return err("gnani_unreachable", f"Gnani STT unreachable: {e}", 502)
    finally:
        await client.aclose()

    if response.status_code != 200:
        return _upstream_error(response)

    try:
        body = response.json()
    except (json.JSONDecodeError, ValueError):
        return err("gnani_error", "STT returned an unparsable response", 502)

    transcript = str(body.get("transcript") or "").strip()
    return {"text": transcript, "language": body.get("detected_language_code") or ""}


def _truncate_at_sentence(text: str, limit: int) -> str:
    """Trim to ``limit`` characters, preferring a sentence end that fits."""
    if len(text) <= limit:
        return text
    window = text[:limit]
    for marker in ("।", ".", "!", "?", "\n", "。"):
        cut = window.rfind(marker)
        if cut > limit * 0.4:  # never cut more than half away
            return window[: cut + 1]
    # Last resort: word boundary.
    space = window.rfind(" ")
    return window[:space] if space > 0 else window


@router.post("/voice/speak")
async def speak(
    request: Request,
    body: SpeakRequest,
    x_api_key=Depends(api_key_header),
):
    """Text-to-speech: JSON text in, synthesized audio bytes out."""
    check_key(_settings(request), x_api_key)
    s = _settings(request)
    if not s.gnani_api_key:
        return err("not_configured", "voice is not configured (missing GNANI key)", 503)

    text = _truncate_at_sentence(body.text.strip(), s.gnani_max_tts_characters)
    if not text:
        return err("bad_text", "nothing to speak", 400)

    payload = {
        "text": text,
        "model": s.gnani_tts_model,
        "voice": s.gnani_tts_voice,
        # "auto" makes Timbre detect the displayed language, so the spoken
        # answer always matches what the bubble shows (incl. translations).
        "language": body.language or "auto",
        "speed": s.gnani_tts_speed,
        "audio_config": {"sample_rate": _TTS_SAMPLE_RATE, "container": _TTS_CONTAINER},
    }

    client = await _client()
    try:
        response = await client.post(
            f"{s.gnani_base_url}/api/v1/tts/inference",
            headers={"X-API-Key-ID": s.gnani_api_key},
            json=payload,
        )
    except httpx.HTTPError as e:
        return err("gnani_unreachable", f"Gnani TTS unreachable: {e}", 502)
    finally:
        await client.aclose()

    if response.status_code != 200:
        return _upstream_error(response)

    audio = response.content
    if not audio:
        return err("gnani_error", "TTS returned no audio", 502)

    media = response.headers.get("content-type", "").split(";")[0].strip()
    if not media.startswith("audio/"):
        media = _TTS_MEDIA_TYPES.get(_TTS_CONTAINER, "application/octet-stream")
    return Response(content=audio, media_type=media, headers={"Cache-Control": "no-store"})


# --- Realtime streaming (live mic, word-by-word questions) -------------------
#
# Browsers cannot set WebSocket headers, so the client streams its raw PCM to
# this proxy and the proxy attaches the Gnani key + config headers. Audio
# frames pass through untouched; transcript JSON frames come back the same
# way, so the browser's live word-by-word display is just Gnani's own
# progressive transcripts, in the language/scripts the user speaks.


def _allowed_sample_rate(name: str | None) -> int:
    allowed = {8000, 16000, 44100, 48000}
    try:
        rate = int(name or "")
    except (TypeError, ValueError):
        return 16000
    return rate if rate in allowed else 16000


@router.websocket("/voice/stream")
async def stream_transcribe(ws: WebSocket):
    """PCMic frames in (binary), Gnani transcript JSON out."""
    await ws.accept()
    s = _settings(ws)
    if not s.gnani_api_key:
        await ws.close(code=1011, reason="voice is not configured (missing GNANI key)")
        return

    # Same X-API-Key contract as the HTTP endpoints, via ?api_key= (WS has no
    # custom-header support from browsers without this).
    provided = ws.query_params.get("api_key")
    if s.api_keys:
        ok = any(provided and hmac.compare_digest(provided, valid) for valid in s.api_keys)
        if not ok:
            await ws.close(code=1008, reason="invalid API key")
            return

    url = s.gnani_base_url.replace("https://", "wss://").replace("http://", "ws://")
    url = url.rstrip("/") + "/stt/v3/stream"
    headers = {
        "x-api-key-id": s.gnani_api_key,
        "model": s.gnani_stt_model,
        "language_code": s.gnani_stt_languages or "indic-auto",
        "format": s.gnani_stt_format or "transcribe",
        # Segments split on ~0.7 s pauses: enough for complete phrases but
        # quick enough that the chat bar fills in near real time.
        "x-min-silence-ms": "700",
        # Word-by-word display: partial_transcript frames every 250 ms while
        # the user is still speaking (verified against the live API).
        "x-partials": "true",
        "x-partial-interval-ms": "250",
        # The browser's own silence timer (client VAD) ends the session;
        # keep the server idle timeout out of the way.
        "x-idle-timeout-sec": "900",
        "x-sample-rate": str(_allowed_sample_rate(ws.query_params.get("sample_rate"))),
    }
    try:
        upstream = await websockets.connect(url, additional_headers=headers, open_timeout=10.0)
    except Exception as e:
        await ws.send_text(json.dumps({"type": "error", "message": f"STT upstream unreachable: {e}"}))
        await ws.close()
        return

    async def client_to_gnani():
        try:
            while True:
                message = await ws.receive()
                if message["type"] == "websocket.disconnect":
                    break
                if message.get("bytes"):
                    await upstream.send(message["bytes"])
                elif message.get("text"):
                    await upstream.send(message["text"])  # e.g. {"type":"commit"}
        except Exception:
            pass

    async def gnani_to_client():
        try:
            async for message in upstream:
                if isinstance(message, bytes):
                    await ws.send_text(message.decode("utf-8", "replace"))
                else:
                    await ws.send_text(message)
        except Exception:
            pass

    pump_in = asyncio.create_task(client_to_gnani())
    pump_out = asyncio.create_task(gnani_to_client())
    done, pending = await asyncio.wait({pump_in, pump_out}, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    for task in done:
        task.exception()  # retrieve to avoid "exception never retrieved" warnings
    try:
        await upstream.close()
    except Exception:
        pass
    try:
        await ws.close()
    except Exception:
        pass
