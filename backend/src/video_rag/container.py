"""Builds the objects and picks adapters from the settings."""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def _build_catalog_provider(settings):
    if settings.catalog_backend == "youtube_data_api":
        from .adapters.youtube_data_api import build_data_api_catalog

        log.info("Catalog backend: youtube_data_api")
        return build_data_api_catalog(settings)
    from .adapters.youtube_catalog import YtDlpCatalogProvider

    log.info("Catalog backend: ytdlp")
    return YtDlpCatalogProvider(settings.max_videos_per_course, settings.http_timeout_seconds)


def _build_embedder(settings):
    if settings.jina_api_key:
        from .adapters.embeddings import JinaEmbedder

        return JinaEmbedder(
            settings.jina_api_key, settings.jina_model, settings.embedding_batch_size
        )
    from .adapters.embeddings import HashingEmbedder

    log.warning("no JINA key: toy hashing embedder dim=%s", settings.embedding_dimension)
    return HashingEmbedder(dim=settings.embedding_dimension)


def _build_store(settings):
    if settings.vector_backend not in ("qdrant", "memory"):
        from .errors import ConfigurationError

        raise ConfigurationError("VECTOR_BACKEND must be qdrant or memory")
    if settings.vector_backend == "qdrant":
        from .adapters.vector_store_qdrant import QdrantVectorStore

        return QdrantVectorStore(
            settings.qdrant_url,
            settings.qdrant_api_key,
            settings.qdrant_collection,
            settings.pipeline_version,
        )
    from pathlib import Path

    from .adapters.vector_store_memory import MemoryVectorStore

    return MemoryVectorStore(Path(settings.data_dir) / "memory_store.json")


class Container:
    def __init__(self, settings):
        from pathlib import Path

        from .adapters.llm import build_language_model
        from .adapters.reranker import JinaReranker, LexicalReranker
        from .adapters.sqlite_registry import SqliteRegistry
        from .adapters.youtube_captions import CaptionTranscriptProvider

        self.settings = settings
        self.catalog = _build_catalog_provider(settings)
        self.transcripts = CaptionTranscriptProvider(
            settings.caption_languages,
            settings.caption_any_language,
            settings.transcript_retry_attempts,
            settings.transcript_retry_base_delay,
        )
        self.embedder = _build_embedder(settings)
        self.store = _build_store(settings)
        if settings.jina_api_key:
            self.reranker: object = JinaReranker(
                settings.jina_api_key, settings.jina_reranker_model
            )
            reranker_name = settings.jina_reranker_model
        else:
            self.reranker = LexicalReranker()
            reranker_name = "lexical"
        self.llm = build_language_model(settings)
        self.registry = SqliteRegistry(Path(settings.data_dir) / "registry.db")
        log.info("Container ready (reranker: %s, transcripts: captions)", reranker_name)


def build_container(settings) -> Container:
    return Container(settings)
