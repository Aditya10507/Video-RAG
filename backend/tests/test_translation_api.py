from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from video_rag.api.routes import router
from video_rag.config import Settings


class RecordingLanguageModel:
    model = "test-model"

    def __init__(self, response="Respuesta traducida"):
        self.response = response
        self.messages = None
        self.max_tokens = None

    def complete(self, messages, max_tokens=None):
        self.messages = messages
        self.max_tokens = max_tokens
        return self.response


def _client(language_model):
    app = FastAPI()
    app.state.container = SimpleNamespace(
        settings=Settings(llm_api_key="test-llm-key", api_keys=("shared-api-key",)),
        llm=language_model,
    )
    app.include_router(router)
    return TestClient(app)


def test_translate_uses_shared_api_key_and_language_model():
    language_model = RecordingLanguageModel()
    with _client(language_model) as client:
        response = client.post(
            "/translate",
            headers={"X-API-Key": "shared-api-key"},
            json={"answer": "A hashmap provides fast lookups.", "target_language": "Spanish"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "translated_answer": "Respuesta traducida",
        "target_language": "Spanish",
    }
    assert language_model.messages[1]["content"] == "A hashmap provides fast lookups."
    assert "Spanish" in language_model.messages[0]["content"]


def test_translate_rejects_invalid_shared_api_key():
    with _client(RecordingLanguageModel()) as client:
        response = client.post(
            "/translate",
            headers={"X-API-Key": "wrong-key"},
            json={"answer": "Answer text", "target_language": "Hindi"},
        )

    assert response.status_code == 401


def test_translate_rejects_blank_answer():
    with _client(RecordingLanguageModel()) as client:
        response = client.post(
            "/translate",
            headers={"X-API-Key": "shared-api-key"},
            json={"answer": "   ", "target_language": "Hindi"},
        )

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_translation"


def test_translate_reports_language_model_failure():
    class FailedLanguageModel(RecordingLanguageModel):
        def complete(self, messages, max_tokens=None):
            raise RuntimeError("provider unavailable")

    with _client(FailedLanguageModel()) as client:
        response = client.post(
            "/translate",
            headers={"X-API-Key": "shared-api-key"},
            json={"answer": "Answer text", "target_language": "Hindi"},
        )

    assert response.status_code == 500
    assert response.json()["code"] == "translation_failed"
