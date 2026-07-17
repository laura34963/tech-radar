import json
from datetime import datetime, timezone
from pathlib import Path
import httpx
from radar.adapters.github import GithubAdapter

FIX = (Path(__file__).parent / "fixtures" / "github_releases.json").read_text()
NOW = datetime(2026, 7, 17, tzinfo=timezone.utc)


def _client(capture):
    def handler(req):
        capture["auth"] = req.headers.get("authorization")
        return httpx.Response(200, text=FIX)
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_github_maps_releases(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    cap = {}
    src = {"repo": "rails/rails", "category": "backend"}
    items = GithubAdapter().fetch(src, None, client=_client(cap), now=NOW)
    assert len(items) == 2
    assert items[0].title == "rails/rails v7.2.0"
    assert items[0].url.endswith("v7.2.0")
    assert items[0].published.astimezone(timezone.utc) == datetime(2026, 7, 15, 12, tzinfo=timezone.utc)
    assert cap["auth"] is None


def test_github_sends_token_when_present(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "tok123")
    cap = {}
    GithubAdapter().fetch({"repo": "r/r", "category": "backend"}, None,
                          client=_client(cap), now=NOW)
    assert cap["auth"] == "Bearer tok123"
