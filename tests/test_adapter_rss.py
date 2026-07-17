from datetime import datetime, timezone
from pathlib import Path
from radar.adapters._feed import parse_feed

FIX = Path(__file__).parent / "fixtures" / "sample_rss.xml"
NOW = datetime(2026, 7, 17, tzinfo=timezone.utc)


def test_parse_feed_maps_entry_to_item():
    items = parse_feed(FIX.read_text(), "backend", source_type="rss", now=NOW)
    assert len(items) == 1
    it = items[0]
    assert it.title == "Widget 2.0 released"
    assert it.url == "https://example.com/widget-2"
    assert it.source_type == "rss" and it.category == "backend"
    assert it.published.tzinfo is not None
    assert it.published.astimezone(timezone.utc).year == 2026
    assert it.id  # stable id present


def test_parse_feed_sets_provider_when_given():
    items = parse_feed(FIX.read_text(), "cloud", source_type="cloud", provider="aws", now=NOW)
    assert items[0].provider == "aws" and items[0].source_type == "cloud"


def test_parse_feed_exact_utc_instant():
    """Fixture's pubDate 'Wed, 16 Jul 2026 10:00:00 GMT' must parse to exact UTC instant."""
    items = parse_feed(FIX.read_text(), "backend", source_type="rss", now=NOW)
    assert items[0].published == datetime(2026, 7, 16, 10, 0, 0, tzinfo=timezone.utc)


def test_parse_feed_undated_entry_uses_injected_now():
    """Entry with no pubDate should use injected now parameter."""
    rss_no_date = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <title>Sample</title>
  <item>
    <title>Undated Item</title>
    <link>https://example.com/undated</link>
    <guid>https://example.com/undated</guid>
  </item>
</channel></rss>"""
    items = parse_feed(rss_no_date, "backend", source_type="rss", now=NOW)
    assert len(items) == 1
    assert items[0].published == NOW
