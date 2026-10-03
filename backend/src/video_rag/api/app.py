"""FastAPI factory. Serves API + frontend from one origin."""

from __future__ import annotations

import time
import uuid
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ..config import Settings, from_env
from ..container import build_container
from ..logging_config import setup as setup_logging
from .routes import router


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or from_env()
    setup_logging(settings.log_level, settings.log_json)
    app = FastAPI(
        title="Video RAG",
        docs_url=None if settings.environment == "production" else "/docs",
        openapi_url=None if settings.environment == "production" else "/openapi.json",
    )
    app.state.container = build_container(settings)
    app.include_router(router)

    @app.middleware("http")
    async def add_id(request: Request, call_next):
        rid = uuid.uuid4().hex[:8]
        start = time.time()
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    front = Path(settings.frontend_dir)
    if not front.is_absolute():
        front = Path.cwd() / front
    if front.exists():
        app.mount("/static", StaticFiles(directory=str(front)), name="static")

        @app.get("/", include_in_schema=False)
        def index():
            return FileResponse(str(front / "index.html"))

        @app.get("/app.js", include_in_schema=False)
        def appjs():
            return FileResponse(str(front / "app.js"), media_type="application/javascript")

        @app.get("/styles.css", include_in_schema=False)
        def css():
            return FileResponse(str(front / "styles.css"), media_type="text/css")

    return app
