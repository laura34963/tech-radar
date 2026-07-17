from datetime import datetime, timezone, timedelta
from radar.item import Item
from radar.pipeline.fetch import (importance_ge, within_lookback, score_importance,
                                   stack_matches, dedupe, rank_and_truncate)

NOW = datetime(2026, 7, 17, tzinfo=timezone.utc)


def _item(**kw):
    base = dict(id="i", title="t", url="u", source_type="rss", category="backend",
                published=NOW, summary="s")
    base.update(kw)
    return Item(**base)


def test_importance_ge():
    assert importance_ge("high", "medium")
    assert importance_ge("medium", "medium")
    assert not importance_ge("low", "high")


def test_within_lookback():
    assert within_lookback(NOW - timedelta(days=3), NOW, 7)
    assert not within_lookback(NOW - timedelta(days=8), NOW, 7)


def test_stack_matches_by_substring():
    it = _item(title="Rails 7.2 released", summary="")
    assert stack_matches(it, {"packages": ["rails", "sidekiq"]}) == ["rails"]


def test_score_importance_precedence():
    assert score_importance(_item(severity="critical"), {}) == "critical"
    assert score_importance(_item(title="rails x"), {"packages": ["rails"]}) == "high"
    assert score_importance(_item(source_type="rss"), {}) == "low"
    assert score_importance(_item(source_type="github"), {}) == "medium"


def test_dedupe_keeps_one_per_id():
    a = _item(id="dup", summary="short")
    b = _item(id="dup", summary="a much longer and richer summary")
    out = dedupe([a, b])
    assert len(out) == 1 and out[0].summary.startswith("a much longer")


def test_rank_and_truncate_limits_per_category():
    items = [_item(id=str(n), importance="high", published=NOW - timedelta(hours=n))
             for n in range(5)]
    out = rank_and_truncate(items, ["backend"], max_per=3)
    assert len(out) == 3
    assert out[0].id == "0"  # most recent first


import httpx
from pathlib import Path
from radar.config import Config
from radar.pipeline.fetch import run_fetch

RSS = (Path(__file__).parent / "fixtures" / "sample_rss.xml").read_text()


def _cfg(sources):
    return Config(general={"lookback_days": 3650, "min_keep_importance": "low"},
                  stack={}, categories=["backend"], sources=sources, llm={})


def test_run_fetch_isolates_failing_source_and_resumes(tmp_path):
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        if "bad" in str(req.url):
            return httpx.Response(500)
        return httpx.Response(200, text=RSS)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    cfg = _cfg([
        {"type": "rss", "category": "backend", "url": "https://good/feed"},
        {"type": "rss", "category": "backend", "url": "https://bad/feed"},
    ])
    snap_path = tmp_path / "2026-07-17.json"
    snap = run_fetch(cfg, snap_path, now=datetime(2026, 7, 17, tzinfo=timezone.utc),
                     client=client, fresh=True)
    assert snap["meta"]["sources"]["https://good/feed"]["status"] == "ok"
    assert snap["meta"]["sources"]["https://bad/feed"]["status"] == "failed"
    assert len(snap["items"]) == 1  # only the good feed's item survived

    # re-run: good source is skipped (not re-fetched), bad source retried
    before = calls["n"]
    run_fetch(cfg, snap_path, now=datetime(2026, 7, 17, tzinfo=timezone.utc), client=client)
    assert calls["n"] == before + 1  # only the failed source hit again


_DUP_FEED_TEMPLATE = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <title>Sample</title>
  <item>
    <title>Widget update</title>
    <link>https://example.com/dup-item</link>
    <description>{desc}</description>
    <pubDate>Wed, 16 Jul 2026 10:00:00 GMT</pubDate>
    <guid>https://example.com/dup-item</guid>
  </item>
</channel></rss>"""

_SHORT_DESC = "short summary"
_LONG_DESC = "a much longer and richer summary with considerably more detail in it"
_FEED_SHORT = _DUP_FEED_TEMPLATE.format(desc=_SHORT_DESC)
_FEED_LONG = _DUP_FEED_TEMPLATE.format(desc=_LONG_DESC)


def test_run_fetch_merge_keeps_richest_duplicate_regardless_of_source_order(tmp_path):
    def handler(req):
        url = str(req.url)
        if "short" in url:
            return httpx.Response(200, text=_FEED_SHORT)
        return httpx.Response(200, text=_FEED_LONG)

    client = httpx.Client(transport=httpx.MockTransport(handler))

    # order 1: short-summary source fetched first, long-summary source second
    cfg1 = _cfg([
        {"type": "rss", "category": "backend", "url": "https://short/feed"},
        {"type": "rss", "category": "backend", "url": "https://long/feed"},
    ])
    snap1 = run_fetch(cfg1, tmp_path / "order1.json",
                       now=datetime(2026, 7, 17, tzinfo=timezone.utc),
                       client=client, fresh=True)
    assert len(snap1["items"]) == 1
    assert snap1["items"][0]["summary"] == _LONG_DESC

    # order 2: long-summary source fetched first, short-summary source second —
    # same winner regardless of fetch order
    cfg2 = _cfg([
        {"type": "rss", "category": "backend", "url": "https://long/feed"},
        {"type": "rss", "category": "backend", "url": "https://short/feed"},
    ])
    snap2 = run_fetch(cfg2, tmp_path / "order2.json",
                       now=datetime(2026, 7, 17, tzinfo=timezone.utc),
                       client=client, fresh=True)
    assert len(snap2["items"]) == 1
    assert snap2["items"][0]["summary"] == _LONG_DESC


def test_run_fetch_isolates_unknown_adapter_type(tmp_path):
    def handler(req):
        return httpx.Response(200, text=RSS)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    # Config built directly (bypassing load_config's type validation) so an
    # unknown adapter type can reach run_fetch.
    cfg = _cfg([
        {"type": "rss", "category": "backend", "url": "https://good/feed"},
        {"type": "nope", "category": "backend", "url": "https://mystery/feed"},
    ])
    snap_path = tmp_path / "unknown-type.json"
    snap = run_fetch(cfg, snap_path, now=datetime(2026, 7, 17, tzinfo=timezone.utc),
                     client=client, fresh=True)
    assert snap["meta"]["sources"]["https://good/feed"]["status"] == "ok"
    assert snap["meta"]["sources"]["https://mystery/feed"]["status"] == "failed"
    assert len(snap["items"]) == 1  # only the good feed's item survived
