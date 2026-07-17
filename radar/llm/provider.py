from __future__ import annotations
import os
from typing import Protocol
import httpx


class LLMProvider(Protocol):
    def complete(self, system: str, user: str) -> str: ...


class OpenAICompatible:
    def __init__(self, base_url, model, api_key, client):
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._model, self._key, self._client = model, api_key, client

    def complete(self, system: str, user: str) -> str:
        r = self._client.post(self._url, headers={"Authorization": f"Bearer {self._key}"},
                              json={"model": self._model, "messages": [
                                  {"role": "system", "content": system},
                                  {"role": "user", "content": user}]}, timeout=60.0)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]


class Anthropic:
    def __init__(self, base_url, model, api_key, client):
        self._url = base_url.rstrip("/") + "/v1/messages"
        self._model, self._key, self._client = model, api_key, client

    def complete(self, system: str, user: str) -> str:
        r = self._client.post(self._url, headers={
            "x-api-key": self._key, "anthropic-version": "2023-06-01"},
            json={"model": self._model, "max_tokens": 2048, "system": system,
                  "messages": [{"role": "user", "content": user}]}, timeout=60.0)
        r.raise_for_status()
        return r.json()["content"][0]["text"]


class Ollama:
    def __init__(self, base_url, model, client):
        self._url = base_url.rstrip("/") + "/api/chat"
        self._model, self._client = model, client

    def complete(self, system: str, user: str) -> str:
        r = self._client.post(self._url, json={"model": self._model, "stream": False,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}]}, timeout=120.0)
        r.raise_for_status()
        return r.json()["message"]["content"]


def make_provider(llm_cfg: dict, *, client: httpx.Client) -> LLMProvider | None:
    if not llm_cfg.get("enabled"):
        return None
    provider = llm_cfg.get("provider")
    base_url, model = llm_cfg.get("base_url", ""), llm_cfg.get("model", "")
    if provider == "ollama":
        return Ollama(base_url, model, client)
    key = os.environ.get(llm_cfg.get("api_key_env", ""))
    if not key:
        return None
    if provider == "anthropic":
        return Anthropic(base_url, model, key, client)
    return OpenAICompatible(base_url, model, key, client)
