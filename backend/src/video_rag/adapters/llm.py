"""Answer wording. One class, direct POST. Key required, fails fast.

Streaming is a method (stream_complete), not a separate file or endpoint.
"""

from __future__ import annotations

import json

import httpx

from ..errors import LlmError


class LanguageModel:
    def __init__(
        self, base_url: str, model: str, api_key: str, timeout: float = 30.0, max_tokens: int = 600
    ):
        if not api_key:
            raise LlmError("VIDEO_RAG_LLM_API_KEY is required")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self.max_tokens = max_tokens

    def _headers(self, accept: str = "application/json") -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": accept,
        }

    def complete(self, messages: list[dict], max_tokens: int | None = None) -> str:
        try:
            r = httpx.post(
                f"{self.base_url}/chat/completions",
                headers=self._headers(),
                json={
                    "model": self.model,
                    "messages": messages,
                    "max_tokens": max_tokens or self.max_tokens,
                    "temperature": 0.2,
                    "stream": False,
                },
                timeout=self.timeout,
            )
            r.raise_for_status()
            return (r.json()["choices"][0]["message"]["content"] or "").strip()
        except Exception as e:
            raise LlmError(f"LLM call failed: {e}") from e

    def complete_json(self, messages: list[dict]) -> dict:
        raw = self.complete(messages, max_tokens=200)
        try:
            start, end = raw.index("{"), raw.rindex("}") + 1
            return json.loads(raw[start:end])
        except Exception as e:
            raise LlmError(f"LLM JSON parse failed: {raw[:200]}") from e

    def stream_complete(self, messages: list[dict], max_tokens: int | None = None):
        """Yield content deltas. Put stream=True here, nowhere else."""
        try:
            with httpx.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers=self._headers("text/event-stream"),
                json={
                    "model": self.model,
                    "messages": messages,
                    "max_tokens": max_tokens or self.max_tokens,
                    "temperature": 0.2,
                    "stream": True,
                },
                timeout=90,
            ) as r:
                r.raise_for_status()
                for line in r.iter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        evt = json.loads(data)
                        delta = (
                            ((evt.get("choices") or [{}])[0].get("delta") or {}).get("content")
                        ) or ""
                    except Exception:
                        continue
                    if delta:
                        yield delta
        except Exception as e:
            raise LlmError(f"LLM stream failed: {e}") from e


class StubLanguageModel(LanguageModel):
    """Offline double for tests/smoke. No network, no key."""

    def __init__(self):
        self.model = "stub"

    def complete(self, messages: list[dict], max_tokens: int | None = None) -> str:
        text = json.dumps(messages)[-2000:]
        return f"Stub answer grounded in: {text[:300]}"

    def complete_json(self, messages: list[dict]) -> dict:
        return {"explains": True, "confidence": 0.9}

    def stream_complete(self, messages: list[dict], max_tokens: int | None = None):
        for word in self.complete(messages, max_tokens).split(" "):
            yield word + " "


def build_language_model(settings) -> LanguageModel:
    if not settings.llm_api_key:
        raise LlmError("VIDEO_RAG_LLM_API_KEY is required")
    return LanguageModel(
        settings.llm_base_url,
        settings.llm_model,
        settings.llm_api_key,
        settings.llm_timeout_seconds,
        settings.llm_max_output_tokens,
    )
