from datetime import datetime, timezone
from pathlib import Path
import httpx
from radar.adapters.cloud import CloudAdapter

FIX = (Path(__file__).parent / "fixtures" / "sample_rss.xml").read_text()
NOW = datetime(2026, 7, 17, tzinfo=timezone.utc)


def _client():
    return httpx.Client(transport=httpx.MockTransport(
        lambda req: httpx.Response(200, text=FIX)))


def test_cloud_sets_provider_and_no_service_filter_keeps_all():
    src = {"url": "https://x", "category": "cloud", "provider": "aws", "services": []}
    items = CloudAdapter().fetch(src, None, client=_client(), now=NOW)
    assert items and items[0].provider == "aws"


def test_cloud_service_filter_drops_non_matching():
    src = {"url": "https://x", "category": "cloud", "provider": "aws",
           "services": ["lambda"]}
    items = CloudAdapter().fetch(src, None, client=_client(), now=NOW)
    assert items == []  # "Widget 2.0" mentions no lambda


def test_cloud_service_filter_keeps_and_tags_match():
    src = {"url": "https://x", "category": "cloud", "provider": "aws",
           "services": ["widget"]}
    items = CloudAdapter().fetch(src, None, client=_client(), now=NOW)
    assert len(items) == 1 and "widget" in items[0].tags
