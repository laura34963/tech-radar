from datetime import datetime, timezone
from pathlib import Path
import httpx
import pytest
from radar.adapters.social import SocialAdapter

FIX = (Path(__file__).parent / "fixtures" / "hn.json").read_text()
NOW = datetime(2026, 7, 17, tzinfo=timezone.utc)


def test_hn_applies_min_points():
    client = httpx.Client(transport=httpx.MockTransport(
        lambda req: httpx.Response(200, text=FIX)))
    src = {"source": "hn", "category": "frontend", "query": "react", "min_points": 100}
    items = SocialAdapter().fetch(src, None, client=client, now=NOW)
    assert len(items) == 1
    assert items[0].title == "React 20 announced"


def test_reddit_not_implemented_in_v1():
    with pytest.raises(NotImplementedError, match="reddit"):
        SocialAdapter().fetch({"source": "reddit", "category": "frontend"},
                              None, client=httpx.Client(), now=NOW)
