"""Error hierarchy. One shape maps to one HTTP status in api/routes.py."""


class VideoRagError(Exception):
    code = "internal_error"


class ConfigurationError(VideoRagError):
    code = "configuration_error"


class CatalogUnavailableError(VideoRagError):
    code = "catalog_unavailable"


class TranscriptUnavailableError(VideoRagError):
    code = "transcript_unavailable"


class EmbeddingError(VideoRagError):
    code = "embedding_failed"


class RetrievalError(VideoRagError):
    code = "retrieval_failed"


class LlmError(VideoRagError):
    code = "llm_failed"
