from datetime import timezone
from pathlib import Path
from radar.adapters._feed import parse_feed

FIX = Path(__file__).parent / "fixtures" / "sample_rss.xml"


def test_parse_feed_maps_entry_to_item():
    items = parse_feed(FIX.read_text(), "backend", source_type="rss")
    assert len(items) == 1
    it = items[0]
    assert it.title == "Widget 2.0 released"
    assert it.url == "https://example.com/widget-2"
    assert it.source_type == "rss" and it.category == "backend"
    assert it.published.tzinfo is not None
    assert it.published.astimezone(timezone.utc).year == 2026
    assert it.id  # stable id present


def test_parse_feed_sets_provider_when_given():
    items = parse_feed(FIX.read_text(), "cloud", source_type="cloud", provider="aws")
    assert items[0].provider == "aws" and items[0].source_type == "cloud"
