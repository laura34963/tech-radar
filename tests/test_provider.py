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


def test_gemini_completes(monkeypatch):
    monkeypatch.setenv("RADAR_LLM_API_KEY", "g-key")
    cap = {}

    def handler(req):
        import json
        cap["url"] = str(req.url)
        cap["key"] = req.headers.get("x-goog-api-key")
        cap["body"] = json.loads(req.content)
        return httpx.Response(200, json={"candidates": [
            {"content": {"parts": [{"text": "hi from gemini"}]}}]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    cfg = {"enabled": True, "provider": "gemini",
           "api_key_env": "RADAR_LLM_API_KEY",
           "base_url": "https://generativelanguage.googleapis.com",
           "model": "gemini-2.5-flash"}
    provider = make_provider(cfg, client=client)
    assert provider.complete("sys", "usr") == "hi from gemini"
    assert cap["url"].endswith("/v1beta/models/gemini-2.5-flash:generateContent")
    assert cap["key"] == "g-key"                       # key in header, not URL
    assert "key=" not in cap["url"]
    assert cap["body"]["systemInstruction"]["parts"][0]["text"] == "sys"
    assert cap["body"]["contents"][0]["parts"][0]["text"] == "usr"


def test_gemini_defaults_base_url(monkeypatch):
    monkeypatch.setenv("RADAR_LLM_API_KEY", "g-key")
    cap = {}
    client = httpx.Client(transport=httpx.MockTransport(
        lambda req: (cap.__setitem__("url", str(req.url)),
                     httpx.Response(200, json={"candidates": [
                         {"content": {"parts": [{"text": "x"}]}}]}))[1]))
    cfg = {"enabled": True, "provider": "gemini", "api_key_env": "RADAR_LLM_API_KEY",
           "base_url": "", "model": "gemini-2.5-flash"}
    make_provider(cfg, client=client).complete("s", "u")
    assert cap["url"].startswith("https://generativelanguage.googleapis.com/v1beta/models/")


def test_gemini_missing_key_returns_none(monkeypatch):
    monkeypatch.delenv("RADAR_LLM_API_KEY", raising=False)
    cfg = {"enabled": True, "provider": "gemini", "api_key_env": "RADAR_LLM_API_KEY",
           "base_url": "", "model": "gemini-2.5-flash"}
    assert make_provider(cfg, client=httpx.Client()) is None


def test_cli_provider_pipes_prompt_via_stdin():
    # `cat` echoes stdin back -> verifies prompt is piped and stdout captured
    from radar.llm.provider import CliProvider
    out = CliProvider("cat").complete("SYS", "USR")
    assert out == "SYS\n\nUSR"


def test_cli_provider_prompt_placeholder():
    # `printf %s {prompt}` substitutes the prompt as an argv element (no shell)
    from radar.llm.provider import CliProvider
    out = CliProvider("printf %s {prompt}").complete("", "hello")
    assert out == "hello"


def test_cli_provider_nonzero_exit_raises():
    from radar.llm.provider import CliProvider
    import pytest
    with pytest.raises(RuntimeError, match="cli llm"):
        CliProvider("false").complete("s", "u")


def test_make_provider_cli_needs_no_key(monkeypatch):
    monkeypatch.delenv("RADAR_LLM_API_KEY", raising=False)
    cfg = {"enabled": True, "provider": "cli", "command": "cat"}
    provider = make_provider(cfg, client=httpx.Client())
    assert provider is not None
    assert provider.complete("s", "u") == "s\n\nu"


def test_make_provider_cli_without_command_returns_none():
    cfg = {"enabled": True, "provider": "cli", "command": ""}
    assert make_provider(cfg, client=httpx.Client()) is None
