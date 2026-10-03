"""Every setting, read from the environment in one place.

Prefix: VIDEO_RAG_. See .env.example for docs. Defaults run locally.
LLM is required (no disabled fallback): build_language_model fails fast.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _get(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _get_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _get_int(name: str, default: int) -> int:
    try:
        return int(float(os.environ.get(name, str(default))))
    except ValueError:
        return default


def _get_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, str(default)).strip().lower()
    return raw in ("1", "true", "yes", "on")


@dataclass
class Settings:
    environment: str = "development"
    log_level: str = "INFO"
    log_json: bool = True
    data_dir: str = "data"
    pipeline_version: str = "v1"

    api_host: str = "127.0.0.1"
    api_port: int = 8000
    api_keys: tuple = ()
    allow_anonymous: bool = False
    cors_origins: tuple = ()
    rate_limit_per_minute: int = 60
    max_question_characters: int = 500
    job_retention_seconds: int = 3600
    frontend_dir: str = "frontend"

    ingest_concurrency: int = 6
    max_videos_per_course: int = 500
    max_video_duration_seconds: int = 21600
    transcript_retry_attempts: int = 3
    transcript_retry_base_delay: float = 1.0
    caption_languages: tuple = ("en",)
    caption_any_language: bool = True
    http_timeout_seconds: float = 30.0
    catalog_backend: str = "ytdlp"
    youtube_api_key: str | None = None

    chunk_window_seconds: int = 60
    chunk_overlap_seconds: int = 15
    min_chunk_characters: int = 80

    embedding_dimension: int = 1024
    embedding_batch_size: int = 32
    jina_api_key: str | None = None
    jina_model: str = "jina-embeddings-v3"
    jina_reranker_model: str = "jina-reranker-v2-base-multilingual"

    vector_backend: str = "memory"
    qdrant_url: str = ""
    qdrant_api_key: str | None = None
    qdrant_collection: str = "chunks"

    retrieve_top_k: int = 30
    rerank_top_k: int = 8
    fusion_k: int = 60
    threshold_high: float = 0.55
    threshold_low: float = 0.15
    lambda_coverage: float = 0.3
    lead_in_seconds: int = 3

    punctuation_enabled: bool = False
    punctuation_model: str = "kredor/punctuate-all"
    terminology_correction_enabled: bool = False

    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o-mini"
    llm_api_key: str | None = None
    llm_timeout_seconds: float = 30.0
    llm_max_output_tokens: int = 600

    refusal_message: str = "The topic is not covered in the given course"

    def __post_init__(self) -> None:
        from .errors import ConfigurationError

        if self.chunk_overlap_seconds >= self.chunk_window_seconds:
            raise ConfigurationError("CHUNK_OVERLAP must be smaller than CHUNK_WINDOW")
        if self.rerank_top_k > self.retrieve_top_k:
            raise ConfigurationError("RERANK_TOP_K cannot exceed RETRIEVE_TOP_K")
        if self.catalog_backend not in ("ytdlp", "youtube_data_api"):
            raise ConfigurationError("CATALOG_BACKEND must be ytdlp or youtube_data_api")
        if self.catalog_backend == "youtube_data_api" and not self.youtube_api_key:
            raise ConfigurationError("youtube_data_api needs VIDEO_RAG_YOUTUBE_API_KEY")
        if self.environment == "production" and not self.api_keys and not self.allow_anonymous:
            raise ConfigurationError("production needs VIDEO_RAG_API_KEYS or ALLOW_ANONYMOUS=true")
        if "*" in self.cors_origins and self.environment == "production":
            raise ConfigurationError("wildcard CORS is rejected in production")
        if not (0 <= self.threshold_low <= self.threshold_high <= 1):
            raise ConfigurationError("thresholds must satisfy 0 <= LOW <= HIGH <= 1")
        for name in (
            "retrieve_top_k",
            "rerank_top_k",
            "chunk_window_seconds",
            "embedding_dimension",
            "max_question_characters",
        ):
            if getattr(self, name) <= 0:
                raise ConfigurationError(f"{name} must be positive")
        if self.http_timeout_seconds <= 0 or self.llm_timeout_seconds <= 0:
            raise ConfigurationError("timeouts must be positive")


def _split_csv(raw: str) -> tuple:
    return tuple(p.strip() for p in raw.split(",") if p.strip())


def from_env() -> Settings:
    # Load the repo .env (repo root + cwd) so keys work without exporting.
    try:
        from dotenv import load_dotenv
        from pathlib import Path

        here = Path(__file__).resolve()
        for cand in (here.parents[3] / ".env", Path.cwd() / ".env"):
            try:
                if cand.exists():
                    load_dotenv(cand, override=False)
            except Exception:
                continue
    except ImportError:
        pass
    api_keys = _split_csv(_get("VIDEO_RAG_API_KEYS", ""))
    s = Settings(
        environment=_get("VIDEO_RAG_ENVIRONMENT", "development"),
        log_level=_get("VIDEO_RAG_LOG_LEVEL", "INFO"),
        log_json=_get_bool("VIDEO_RAG_LOG_JSON", True),
        data_dir=_get("VIDEO_RAG_DATA_DIR", "data"),
        pipeline_version=_get("VIDEO_RAG_PIPELINE_VERSION", "v1"),
        api_host=_get("VIDEO_RAG_API_HOST", "127.0.0.1"),
        api_port=_get_int("VIDEO_RAG_API_PORT", 8000),
        api_keys=api_keys,
        allow_anonymous=_get_bool("VIDEO_RAG_ALLOW_ANONYMOUS", False),
        cors_origins=_split_csv(_get("VIDEO_RAG_CORS_ORIGINS", "")),
        rate_limit_per_minute=_get_int("VIDEO_RAG_RATE_LIMIT_PER_MINUTE", 60),
        max_question_characters=_get_int("VIDEO_RAG_MAX_QUESTION_CHARACTERS", 500),
        job_retention_seconds=_get_int("VIDEO_RAG_JOB_RETENTION_SECONDS", 3600),
        frontend_dir=_get("VIDEO_RAG_FRONTEND_DIR", "frontend"),
        ingest_concurrency=_get_int("VIDEO_RAG_INGEST_CONCURRENCY", 6),
        max_videos_per_course=_get_int("VIDEO_RAG_MAX_VIDEOS_PER_COURSE", 500),
        max_video_duration_seconds=_get_int("VIDEO_RAG_MAX_VIDEO_DURATION_SECONDS", 21600),
        transcript_retry_attempts=_get_int("VIDEO_RAG_TRANSCRIPT_RETRY_ATTEMPTS", 3),
        transcript_retry_base_delay=_get_float("VIDEO_RAG_TRANSCRIPT_RETRY_BASE_DELAY", 1.0),
        caption_languages=_split_csv(_get("VIDEO_RAG_CAPTION_LANGUAGES", "en")) or ("en",),
        caption_any_language=_get_bool("VIDEO_RAG_CAPTION_ANY_LANGUAGE", True),
        http_timeout_seconds=_get_float("VIDEO_RAG_HTTP_TIMEOUT_SECONDS", 30.0),
        catalog_backend=_get("VIDEO_RAG_CATALOG_BACKEND", "ytdlp"),
        youtube_api_key=_get("VIDEO_RAG_YOUTUBE_API_KEY", "") or None,
        chunk_window_seconds=_get_int("VIDEO_RAG_CHUNK_WINDOW_SECONDS", 60),
        chunk_overlap_seconds=_get_int("VIDEO_RAG_CHUNK_OVERLAP_SECONDS", 15),
        min_chunk_characters=_get_int("VIDEO_RAG_MIN_CHUNK_CHARACTERS", 80),
        embedding_dimension=_get_int("VIDEO_RAG_EMBEDDING_DIMENSION", 1024),
        embedding_batch_size=_get_int("VIDEO_RAG_EMBEDDING_BATCH_SIZE", 32),
        jina_api_key=_get("VIDEO_RAG_JINA_API_KEY", "") or None,
        jina_model=_get("VIDEO_RAG_JINA_MODEL", "jina-embeddings-v3"),
        jina_reranker_model=_get(
            "VIDEO_RAG_JINA_RERANKER_MODEL", "jina-reranker-v2-base-multilingual"
        ),
        vector_backend=_get("VIDEO_RAG_VECTOR_BACKEND", "memory"),
        qdrant_url=_get("VIDEO_RAG_QDRANT_URL", ""),
        qdrant_api_key=_get("VIDEO_RAG_QDRANT_API_KEY", "") or None,
        qdrant_collection=_get("VIDEO_RAG_QDRANT_COLLECTION", "chunks"),
        retrieve_top_k=_get_int("VIDEO_RAG_RETRIEVE_TOP_K", 30),
        rerank_top_k=_get_int("VIDEO_RAG_RERANK_TOP_K", 8),
        fusion_k=_get_int("VIDEO_RAG_FUSION_K", 60),
        threshold_high=_get_float("VIDEO_RAG_THRESHOLD_HIGH", 0.55),
        threshold_low=_get_float("VIDEO_RAG_THRESHOLD_LOW", 0.15),
        lambda_coverage=_get_float("VIDEO_RAG_LAMBDA_COVERAGE", 0.3),
        lead_in_seconds=_get_int("VIDEO_RAG_LEAD_IN_SECONDS", 3),
        punctuation_enabled=_get_bool("VIDEO_RAG_PUNCTUATION_ENABLED", False),
        punctuation_model=_get("VIDEO_RAG_PUNCTUATION_MODEL", "kredor/punctuate-all"),
        terminology_correction_enabled=_get_bool("VIDEO_RAG_TERMINOLOGY_CORRECTION_ENABLED", False),
        llm_base_url=_get("VIDEO_RAG_LLM_BASE_URL", "https://api.openai.com/v1"),
        llm_model=_get("VIDEO_RAG_LLM_MODEL", "gpt-4o-mini"),
        llm_api_key=_get("VIDEO_RAG_LLM_API_KEY", "") or None,
        llm_timeout_seconds=_get_float("VIDEO_RAG_LLM_TIMEOUT_SECONDS", 30.0),
        llm_max_output_tokens=_get_int("VIDEO_RAG_LLM_MAX_OUTPUT_TOKENS", 600),
        refusal_message=_get(
            "VIDEO_RAG_REFUSAL_MESSAGE", "The topic is not covered in the given course"
        ),
    )
    s.__post_init__()
    return s


MASKED = {"api_keys", "youtube_api_key", "jina_api_key", "qdrant_api_key", "llm_api_key"}


def masked_dict(settings: Settings) -> dict:
    out = {}
    for k, v in settings.__dict__.items():
        out[k] = "***" if k in MASKED and v else v
    return out
