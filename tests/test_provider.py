import httpx
from radar.llm.provider import make_provider


def test_disabled_returns_none():
    assert make_provider({"enabled": False}, client=httpx.Client()) is None


def test_missing_key_returns_none(monkeypatch):
    monkeypatch.delenv("RADAR_LLM_API_KEY", raising=False)
    cfg = {"enabled": True, "provider": "openai_compatible",
           "api_key_env": "RADAR_LLM_API_KEY", "base_url": "https://api/v1", "model": "m"}
    assert make_provider(cfg, client=httpx.Client()) is None


def test_openai_compatible_completes(monkeypatch):
    monkeypatch.setenv("RADAR_LLM_API_KEY", "sk-x")
    captured = {}

    def handler(req):
        captured["url"] = str(req.url)
        captured["auth"] = req.headers.get("authorization")
        return httpx.Response(200, json={"choices": [{"message": {"content": "hello"}}]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    cfg = {"enabled": True, "provider": "openai_compatible",
           "api_key_env": "RADAR_LLM_API_KEY",
           "base_url": "https://api.example.com/v1", "model": "gpt-x"}
    provider = make_provider(cfg, client=client)
    assert provider.complete("sys", "usr") == "hello"
    assert captured["url"].endswith("/chat/completions")
    assert captured["auth"] == "Bearer sk-x"


def test_ollama_needs_no_key(monkeypatch):
    monkeypatch.delenv("RADAR_LLM_API_KEY", raising=False)
    client = httpx.Client(transport=httpx.MockTransport(
        lambda req: httpx.Response(200, json={"message": {"content": "hi"}})))
    cfg = {"enabled": True, "provider": "ollama",
           "base_url": "http://localhost:11434", "model": "llama3"}
    provider = make_provider(cfg, client=client)
    assert provider.complete("s", "u") == "hi"
