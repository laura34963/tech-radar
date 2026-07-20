from datetime import datetime, timezone
from pathlib import Path
import httpx
import pytest
from radar.adapters.social import SocialAdapter

FIX = (Path(__file__).parent / "fixtures" / "hn.json").read_text()
REDDIT_FIX = (Path(__file__).parent / "fixtures" / "reddit.json").read_text()
NOW = datetime(2026, 7, 17, tzinfo=timezone.utc)


def test_hn_applies_min_points():
    client = httpx.Client(transport=httpx.MockTransport(
        lambda req: httpx.Response(200, text=FIX)))
    src = {"source": "hn", "category": "frontend", "query": "react", "min_points": 100}
    items = SocialAdapter().fetch(src, None, client=client, now=NOW)
    assert len(items) == 1
    assert items[0].title == "React 20 announced"


def test_reddit_applies_min_points_and_maps():
    client = httpx.Client(transport=httpx.MockTransport(
        lambda req: httpx.Response(200, text=REDDIT_FIX)))
    src = {"source": "reddit", "category": "frontend", "subreddit": "reactjs", "min_points": 100}
    items = SocialAdapter().fetch(src, None, client=client, now=NOW)
    assert len(items) == 1
    it = items[0]
    assert it.title == "React 20 is out"
    assert it.url == "https://react.dev/blog/react-20"
    assert it.source_type == "social"
    assert it.summary == "350 points on r/reactjs"
    assert it.published.astimezone(timezone.utc) == datetime(2026, 7, 16, 8, tzinfo=timezone.utc)


def test_reddit_uses_new_endpoint_without_query():
    cap = {}

    def handler(req):
        cap["url"] = str(req.url)
        return httpx.Response(200, text=REDDIT_FIX)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    SocialAdapter().fetch({"source": "reddit", "category": "frontend", "subreddit": "reactjs"},
                          None, client=client, now=NOW)
    assert "/r/reactjs/new.json" in cap["url"]


def test_reddit_uses_search_endpoint_with_query():
    cap = {}

    def handler(req):
        cap["url"] = str(req.url)
        return httpx.Response(200, text=REDDIT_FIX)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    SocialAdapter().fetch(
        {"source": "reddit", "category": "frontend", "subreddit": "reactjs", "query": "hooks"},
        None, client=client, now=NOW)
    assert "/r/reactjs/search.json" in cap["url"]
    assert "q=hooks" in cap["url"]
    assert "restrict_sr=1" in cap["url"]
    assert "sort=new" in cap["url"]


def test_reddit_sends_user_agent():
    cap = {}

    def handler(req):
        cap["ua"] = req.headers.get("user-agent")
        return httpx.Response(200, text=REDDIT_FIX)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    SocialAdapter().fetch({"source": "reddit", "category": "frontend", "subreddit": "reactjs"},
                          None, client=client, now=NOW)
    assert cap["ua"] and "tech-radar" in cap["ua"]


def test_reddit_requires_subreddit():
    with pytest.raises(ValueError, match="subreddit"):
        SocialAdapter().fetch({"source": "reddit", "category": "frontend"},
                              None, client=httpx.Client(), now=NOW)


def test_unknown_social_source_not_implemented():
    with pytest.raises(NotImplementedError, match="mastodon"):
        SocialAdapter().fetch({"source": "mastodon", "category": "frontend"},
                              None, client=httpx.Client(), now=NOW)
