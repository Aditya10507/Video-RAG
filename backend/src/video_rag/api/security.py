"""X-API-Key guard. Open locally, enforced in production."""

from __future__ import annotations

from fastapi import Header, HTTPException


def check_key(settings, x_api_key: str | None) -> None:
    import hmac

    if not settings.api_keys:
        if settings.environment == "production" and not settings.allow_anonymous:
            raise HTTPException(500, "server misconfigured")
        return
    for valid in settings.api_keys:
        if x_api_key and hmac.compare_digest(x_api_key, valid):
            return
    raise HTTPException(401, "invalid API key")


def api_key_header(x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    return x_api_key
