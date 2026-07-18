from __future__ import annotations
import logging
import os
import shlex
import subprocess
from typing import Protocol
import httpx

log = logging.getLogger("radar.llm")


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


class Gemini:
    """Google Gemini via the Generative Language REST API. Key passed in the
    x-goog-api-key header (kept out of the URL)."""
    _DEFAULT_BASE = "https://generativelanguage.googleapis.com"

    def __init__(self, base_url, model, api_key, client):
        self._base = (base_url or self._DEFAULT_BASE).rstrip("/")
        self._model, self._key, self._client = model, api_key, client

    def complete(self, system: str, user: str) -> str:
        url = f"{self._base}/v1beta/models/{self._model}:generateContent"
        r = self._client.post(url, headers={"x-goog-api-key": self._key},
            json={"systemInstruction": {"parts": [{"text": system}]},
                  "contents": [{"parts": [{"text": user}]}]}, timeout=60.0)
        r.raise_for_status()
        return r.json()["candidates"][0]["content"]["parts"][0]["text"]


class CliProvider:
    """Shell out to a local LLM CLI (e.g. `claude -p`, `gemini`) and read the
    completion from stdout. The command is operator-supplied config; it is run
    as an argv list (no shell, so no injection) and the prompt is passed on
    stdin by default. If the command contains a `{prompt}` token, the prompt is
    substituted into that argument instead (still no shell). No API key needed —
    the CLI handles its own auth."""

    def __init__(self, command, timeout: int = 120):
        self._argv = shlex.split(command) if isinstance(command, str) else list(command)
        self._timeout = timeout

    def complete(self, system: str, user: str) -> str:
        prompt = f"{system}\n\n{user}" if system else user
        if any("{prompt}" in a for a in self._argv):
            argv = [a.replace("{prompt}", prompt) for a in self._argv]
            stdin = None
        else:
            argv = self._argv
            stdin = prompt
        # echo the command template (never the prompt itself) so a slow CLI shows progress
        log.info("    llm cli: %s", " ".join(self._argv))
        proc = subprocess.run(argv, input=stdin, capture_output=True,
                              text=True, timeout=self._timeout)
        if proc.returncode != 0:
            raise RuntimeError("cli llm %r failed (exit %d): %s"
                               % (argv[0], proc.returncode, proc.stderr.strip()[:300]))
        return proc.stdout.strip()


def make_provider(llm_cfg: dict, *, client: httpx.Client) -> LLMProvider | None:
    if not llm_cfg.get("enabled"):
        return None
    provider = llm_cfg.get("provider")
    base_url, model = llm_cfg.get("base_url", ""), llm_cfg.get("model", "")
    if provider == "ollama":
        return Ollama(base_url, model, client)
    if provider == "cli":
        command = llm_cfg.get("command", "")
        if not command:
            return None
        return CliProvider(command, timeout=int(llm_cfg.get("command_timeout", 120)))
    key = os.environ.get(llm_cfg.get("api_key_env", ""))
    if not key:
        return None
    if provider == "anthropic":
        return Anthropic(base_url, model, key, client)
    if provider == "gemini":
        return Gemini(base_url, model, key, client)
    return OpenAICompatible(base_url, model, key, client)
