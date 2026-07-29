from datetime import datetime, timezone, timedelta
from radar.item import Item
from radar.pipeline.fetch import (importance_ge, within_lookback, dedupe,
                                  rank_and_truncate)

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


def test_run_fetch_runs_every_same_type_social_source(tmp_path):
    # Two HN sources differ only by query/category. They must NOT collapse onto
    # a single resume key ("hn"), which would skip all but the first.
    seen = []

    def handler(req):
        seen.append(req.url.params.get("query"))
        return httpx.Response(200, text='{"hits": []}')

    client = httpx.Client(transport=httpx.MockTransport(handler))
    cfg = _cfg([
        {"type": "social", "category": "backend", "source": "hn", "query": "ruby"},
        {"type": "social", "category": "ai", "source": "hn", "query": "llm"},
    ])
    run_fetch(cfg, tmp_path / "social.json",
              now=datetime(2026, 7, 17, tzinfo=timezone.utc), client=client, fresh=True)
    assert "ruby" in seen and "llm" in seen  # both queried, neither skipped


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


_BOARD_FEED = """<?xml version="1.0"?>
<rss version="2.0"><channel><title>S</title>
  <item>
    <title>{title}</title>
    <link>{link}</link>
    <description>d</description>
    <pubDate>Wed, 16 Jul 2026 10:00:00 GMT</pubDate>
    <guid>{link}</guid>
  </item>
</channel></rss>"""


def test_run_fetch_stamps_declared_board(tmp_path):
    def handler(req):
        if "newsfeed" in str(req.url):
            return httpx.Response(200, text=_BOARD_FEED.format(title="N", link="https://example.com/n"))
        return httpx.Response(200, text=_BOARD_FEED.format(title="P", link="https://example.com/p"))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    cfg = _cfg([
        {"type": "rss", "category": "backend", "url": "https://newsfeed/feed", "board": "news"},
        {"type": "rss", "category": "backend", "url": "https://plainfeed/feed"},
    ])
    snap = run_fetch(cfg, tmp_path / "board.json",
                     now=datetime(2026, 7, 17, tzinfo=timezone.utc),
                     client=client, fresh=True)
    boards = {it["title"]: it["board"] for it in snap["items"]}
    assert boards == {"N": "news", "P": None}  # declared stamped, undeclared stays None


_TWO_ITEM_FEED = """<?xml version="1.0"?>
<rss version="2.0"><channel><title>S</title>
  <item>
    <title>{t1}</title><link>{l1}</link><description>d</description>
    <pubDate>Wed, 16 Jul 2026 10:00:00 GMT</pubDate><guid>{l1}</guid>
  </item>
  <item>
    <title>{t2}</title><link>{l2}</link><description>d</description>
    <pubDate>Wed, 16 Jul 2026 09:00:00 GMT</pubDate><guid>{l2}</guid>
  </item>
</channel></rss>"""


def _cfg_ex(sources, exclude=None, general=None):
    base = {"lookback_days": 3650, "min_keep_importance": "low"}
    base.update(general or {})
    return Config(general=base, stack={}, categories=["backend"], sources=sources,
                  llm={}, exclude=exclude or {})


def test_run_fetch_drops_excluded_items(tmp_path):
    feed = _TWO_ITEM_FEED.format(t1="next.js v16.3.0-canary.97", l1="https://x/1",
                                 t2="next.js v16.2.12", l2="https://x/2")

    def handler(req):
        return httpx.Response(200, text=feed)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    cfg = _cfg_ex([{"type": "rss", "category": "backend", "url": "https://f/feed"}],
                  exclude={"backend": ["canary"]})
    snap = run_fetch(cfg, tmp_path / "ex.json",
                     now=datetime(2026, 7, 17, tzinfo=timezone.utc),
                     client=client, fresh=True)
    titles = [it["title"] for it in snap["items"]]
    assert titles == ["next.js v16.2.12"]   # the canary release never reached disk


def test_run_fetch_records_keyword_match(tmp_path):
    feed = _TWO_ITEM_FEED.format(t1="New Claude model", l1="https://x/1",
                                 t2="unrelated musings", l2="https://x/2")

    def handler(req):
        return httpx.Response(200, text=feed)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    cfg = Config(general={"lookback_days": 3650, "min_keep_importance": "low"},
                 stack={}, categories=["backend"], sources=[
                     {"type": "rss", "category": "backend", "url": "https://f/feed"}],
                 llm={}, category_keywords={"backend": ["claude"]})
    snap = run_fetch(cfg, tmp_path / "kw.json",
                     now=datetime(2026, 7, 17, tzinfo=timezone.utc),
                     client=client, fresh=True)
    by_title = {it["title"]: it for it in snap["items"]}
    assert by_title["New Claude model"]["keyword_match"] == ["claude"]
    assert by_title["New Claude model"]["importance"] == "high"
    assert by_title["unrelated musings"]["keyword_match"] == []


def test_run_fetch_demotes_over_budget_without_dropping(tmp_path):
    feed = _TWO_ITEM_FEED.format(t1="Claude one", l1="https://only.example/1",
                                 t2="Claude two", l2="https://only.example/2")

    def handler(req):
        return httpx.Response(200, text=feed)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    # both items score high via the keyword; budget 1 forces one demotion
    cfg = Config(general={"lookback_days": 3650, "min_keep_importance": "low",
                          "max_card_items": 1, "per_source_limit": 1},
                 stack={}, categories=["backend"], sources=[
                     {"type": "rss", "category": "backend", "url": "https://f/feed"}],
                 llm={}, category_keywords={"backend": ["claude"]})
    snap = run_fetch(cfg, tmp_path / "fair.json",
                     now=datetime(2026, 7, 17, tzinfo=timezone.utc),
                     client=client, fresh=True)
    assert len(snap["items"]) == 2                                  # nothing dropped
    demoted = [it for it in snap["items"] if it["demoted"]]
    assert len(demoted) == 1
    assert demoted[0]["demoted"] == "source_fairness"
    assert demoted[0]["importance"] == "high"                       # importance intact


def test_run_fetch_clears_stale_demotion_when_within_budget(tmp_path):
    from radar.item import Item
    from radar.store import new_snapshot, item_to_dict, atomic_write_json

    snap_path = tmp_path / "stale.json"
    snap = new_snapshot("2026-07-17")
    stale_item = Item(id="stale-1", title="Claude one", url="https://only.example/1",
                      source_type="rss", category="backend",
                      published=datetime(2026, 7, 16, tzinfo=timezone.utc),
                      summary="s", importance="high", keyword_match=["claude"],
                      demoted="source_fairness")
    snap["items"] = [item_to_dict(stale_item)]
    # mark the source as already fetched this run so run_fetch skips re-fetching
    # and drives finalize straight from the rehydrated (stale-demoted) item.
    snap["meta"]["sources"]["https://f/feed"] = {"status": "ok", "count": 1}
    atomic_write_json(snap_path, snap)

    client = httpx.Client(transport=httpx.MockTransport(
        lambda req: httpx.Response(500)))  # must not be called; source is cached
    cfg = Config(general={"lookback_days": 3650, "min_keep_importance": "low",
                          "max_card_items": 30, "per_source_limit": 3},
                 stack={}, categories=["backend"], sources=[
                     {"type": "rss", "category": "backend", "url": "https://f/feed"}],
                 llm={}, category_keywords={"backend": ["claude"]})
    snap = run_fetch(cfg, snap_path, now=datetime(2026, 7, 17, tzinfo=timezone.utc),
                     client=client)

    assert len(snap["items"]) == 1
    assert snap["items"][0]["demoted"] is None  # fairness recomputed, no longer stale


def test_run_fetch_does_not_demote_when_within_budget(tmp_path):
    feed = _TWO_ITEM_FEED.format(t1="Claude one", l1="https://only.example/1",
                                 t2="Claude two", l2="https://only.example/2")

    def handler(req):
        return httpx.Response(200, text=feed)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    cfg = Config(general={"lookback_days": 3650, "min_keep_importance": "low",
                          "max_card_items": 30, "per_source_limit": 3},
                 stack={}, categories=["backend"], sources=[
                     {"type": "rss", "category": "backend", "url": "https://f/feed"}],
                 llm={}, category_keywords={"backend": ["claude"]})
    snap = run_fetch(cfg, tmp_path / "under.json",
                     now=datetime(2026, 7, 17, tzinfo=timezone.utc),
                     client=client, fresh=True)
    # one source holds both cards, but the tier is under budget -> untouched
    assert all(it["demoted"] is None for it in snap["items"])
