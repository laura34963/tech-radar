from datetime import datetime, timezone
from pathlib import Path
from radar.config import Config
from radar.store import atomic_write_json, new_snapshot, item_to_dict, load_snapshot
from radar.item import Item
from radar.pipeline.render import run_render, snapshot_hash

NOW = datetime(2026, 7, 17, tzinfo=timezone.utc)


def _cfg():
    return Config(general={"title": "Radar", "min_display_importance": "high"},
                  stack={}, categories=["backend"], sources=[], llm={})


def _snap_with(items):
    s = new_snapshot("2026-07-17")
    s["items"] = [item_to_dict(i) for i in items]
    return s


def _write_snap(tmp_path, snap):
    p = tmp_path / "data" / "2026-07-17.json"
    atomic_write_json(p, snap)
    return p


def test_render_produces_digest_and_index(tmp_path):
    out = tmp_path / "output"
    high = Item(id="1", title="Big News", url="https://x/1", source_type="rss",
                category="backend", published=NOW, summary="sum", importance="high")
    med = Item(id="2", title="Minor", url="https://x/2", source_type="rss",
               category="backend", published=NOW, summary="m", importance="medium")
    snap_path = _write_snap(tmp_path, _snap_with([high, med]))
    run_render(_cfg(), snap_path, out, force=True)
    digest = (out / "digests" / "2026-07-17.html").read_text()
    assert "Big News" in digest
    assert "Minor" in digest and "Also noted" in digest  # medium demoted
    assert (out / "index.html").exists()
    assert "2026-07-17" in (out / "index.html").read_text()


def test_render_dedupes_same_story_across_sources(tmp_path):
    out = tmp_path / "output"
    # same headline from two sources; the critical copy should win and show once (but appears in priority + cards)
    osv = Item(id="1", title="CVE-2026-1 in rails", url="https://osv/1",
               source_type="security", category="backend", published=NOW,
               summary="from osv", importance="critical", severity="critical")
    ghsa = Item(id="2", title="CVE-2026-1 in rails", url="https://ghsa/2",
                source_type="security", category="backend", published=NOW,
                summary="from ghsa", importance="high", severity="high")
    snap_path = _write_snap(tmp_path, _snap_with([ghsa, osv]))
    run_render(_cfg(), snap_path, out, force=True)
    digest = (out / "digests" / "2026-07-17.html").read_text()
    assert digest.count("CVE-2026-1 in rails") == 2   # shown twice: priority section + cards section
    assert "https://osv/1" in digest                  # critical copy kept
    assert "https://ghsa/2" not in digest             # duplicate dropped


def test_render_escapes_html_in_title(tmp_path):
    out = tmp_path / "output"
    evil = Item(id="1", title="<script>alert(1)</script>", url="https://x", source_type="rss",
                category="backend", published=NOW, summary="s", importance="high")
    snap_path = _write_snap(tmp_path, _snap_with([evil]))
    run_render(_cfg(), snap_path, out, force=True)
    digest = (out / "digests" / "2026-07-17.html").read_text()
    assert "<script>alert(1)</script>" not in digest
    assert "&lt;script&gt;" in digest


def test_render_skips_unchanged_snapshot(tmp_path):
    out = tmp_path / "output"
    it = Item(id="1", title="X", url="u", source_type="rss", category="backend",
              published=NOW, summary="s", importance="high")
    snap_path = _write_snap(tmp_path, _snap_with([it]))
    run_render(_cfg(), snap_path, out)
    page = out / "digests" / "2026-07-17.html"
    first_mtime = page.stat().st_mtime_ns
    run_render(_cfg(), snap_path, out)  # unchanged -> skipped
    assert page.stat().st_mtime_ns == first_mtime


def test_render_refreshes_neighbor_nav_on_new_date(tmp_path):
    out = tmp_path / "output"
    cfg = _cfg()
    it17 = Item(id="1", title="Day17", url="https://x/1", source_type="rss",
                category="backend", published=NOW, summary="s", importance="high")
    snap17_path = _write_snap(tmp_path, _snap_with([it17]))
    run_render(cfg, snap17_path, out, force=True)

    day18 = datetime(2026, 7, 18, tzinfo=timezone.utc)
    it18 = Item(id="2", title="Day18", url="https://x/2", source_type="rss",
                category="backend", published=day18, summary="s", importance="high")
    snap18 = new_snapshot("2026-07-18")
    snap18["items"] = [item_to_dict(it18)]
    snap18_path = tmp_path / "data" / "2026-07-18.json"
    atomic_write_json(snap18_path, snap18)
    run_render(cfg, snap18_path, out, force=True)

    digest17 = (out / "digests" / "2026-07-17.html").read_text()
    assert "2026-07-18.html" in digest17  # earlier page's Next link was refreshed


def test_run_render_missing_snapshot_is_noop(tmp_path):
    out = tmp_path / "output"
    missing = tmp_path / "data" / "does-not-exist.json"
    run_render(_cfg(), missing, out)  # should not raise
    assert not (out / "index.html").exists()  # clean no-op, nothing written


def test_render_sanitizes_dangerous_url_scheme(tmp_path):
    out = tmp_path / "output"
    evil = Item(id="1", title="Evil", url="javascript:alert(1)", source_type="rss",
                category="backend", published=NOW, summary="s", importance="high")
    safe = Item(id="2", title="Safe", url="https://good.example/x", source_type="rss",
                category="backend", published=NOW, summary="s", importance="high")
    snap_path = _write_snap(tmp_path, _snap_with([evil, safe]))
    run_render(_cfg(), snap_path, out, force=True)
    digest = (out / "digests" / "2026-07-17.html").read_text()
    assert 'href="javascript:' not in digest
    assert 'href="#"' in digest
    assert 'href="https://good.example/x"' in digest


# --- markdown filter (safe subset) -------------------------------------------
from radar.pipeline.render import _md


def test_md_renders_subset():
    html = str(_md("Use **bold**, `code`, *em*, and [docs](https://ok.example)."))
    assert "<strong>bold</strong>" in html
    assert "<code>code</code>" in html
    assert "<em>em</em>" in html
    assert '<a href="https://ok.example">docs</a>' in html
    assert html.startswith("<p>") and html.rstrip().endswith("</p>")


def test_md_renders_lists():
    html = str(_md("- one\n- two\n- three"))
    assert html.count("<li>") == 3 and "<ul>" in html


def test_md_escapes_html_and_blocks_bad_links():
    html = str(_md("<script>alert(1)</script> see [x](javascript:alert(1))"))
    assert "<script>" not in html          # raw tag neutralized
    assert "&lt;script&gt;" in html         # escaped form present
    assert 'href="javascript:' not in html  # dangerous scheme dropped
    assert 'href="#"' in html


def test_md_empty_is_blank():
    assert str(_md(None)) == "" and str(_md("")) == ""


def test_render_title_shows_week_and_range(tmp_path):
    from radar.pipeline.render import _env, render_digest
    from radar.store import new_snapshot
    from radar.item import Item
    cfg = Config(general={"title": "Radar", "lookback_days": 7,
                          "min_display_importance": "high"},
                 stack={}, categories=["backend"], sources=[], llm={})
    snap = new_snapshot("2026-07-18")
    snap["items"] = [Item(id="1", title="X", url="https://x", source_type="rss",
                          category="backend", published=NOW, summary="s",
                          importance="high").__dict__ | {"published": "2026-07-18T00:00:00+00:00"}]
    html = render_digest(snap, cfg, _env())
    assert "Week 29" in html
    assert "2026-07-11 ~ 2026-07-18" in html      # end − 7 days


# --- tally and priority helpers ---

from radar.pipeline.render import _tally, _priority, _group


def _grouped_from(cfg, items):
    return _group(_snap_with(items), cfg)


def test_tally_counts_cards_by_effective_level():
    cfg = _cfg()
    crit = Item(id="1", title="c", url="https://x/1", source_type="security",
                category="backend", published=NOW, summary="s",
                importance="critical", severity="critical")
    high = Item(id="2", title="h", url="https://x/2", source_type="rss",
                category="backend", published=NOW, summary="s", importance="high")
    med = Item(id="3", title="m", url="https://x/3", source_type="rss",
               category="backend", published=NOW, summary="s", importance="medium")
    t = _tally(_grouped_from(cfg, [crit, high, med]))
    # medium is below min_display_importance="high" -> "also noted", not a card
    assert t == {"total": 2, "critical": 1, "high": 1, "medium": 0, "low": 0}


def test_priority_is_critical_and_high_sorted():
    cfg = _cfg()
    high = Item(id="1", title="H", url="https://x/1", source_type="rss",
                category="backend", published=NOW, summary="s", importance="high")
    crit = Item(id="2", title="C", url="https://x/2", source_type="security",
                category="backend", published=NOW, summary="s",
                importance="critical", severity="critical")
    pri = _priority(_grouped_from(cfg, [high, crit]))
    assert [p["title"] for p in pri] == ["C", "H"]      # critical before high
    assert all(p["category"] == "backend" for p in pri)


def test_priority_empty_when_no_high_or_critical():
    cfg = _cfg()
    high = Item(id="1", title="H", url="https://x/1", source_type="rss",
                category="backend", published=NOW, summary="s", importance="high")
    # only cards at/above threshold reach grouped["cards"]; drop the high one
    med_only = Item(id="2", title="M", url="https://x/2", source_type="rss",
                    category="backend", published=NOW, summary="s", importance="medium")
    assert _priority(_grouped_from(cfg, [med_only])) == []


def test_digest_shows_kpi_tiles(tmp_path):
    out = tmp_path / "output"
    high = Item(id="1", title="Big News", url="https://x/1", source_type="rss",
                category="backend", published=NOW, summary="s", importance="high")
    snap_path = _write_snap(tmp_path, _snap_with([high]))
    run_render(_cfg(), snap_path, out, force=True)
    digest = (out / "digests" / "2026-07-17.html").read_text()
    assert 'class="kpi-row"' in digest
    assert "TOTAL" in digest and "HIGH" in digest


def test_digest_priority_block_lists_high_items(tmp_path):
    out = tmp_path / "output"
    high = Item(id="1", title="Urgent Advisory", url="https://x/1", source_type="rss",
                category="backend", published=NOW, summary="s", importance="high")
    snap_path = _write_snap(tmp_path, _snap_with([high]))
    run_render(_cfg(), snap_path, out, force=True)
    digest = (out / "digests" / "2026-07-17.html").read_text()
    assert 'class="priority"' in digest
    assert digest.count("Urgent Advisory") == 2   # priority row + full card


def test_digest_priority_block_omitted_when_none(tmp_path):
    out = tmp_path / "output"
    # a card at threshold "high" but importance exactly "high" is priority;
    # use a lower cfg threshold so a medium card shows but is NOT priority.
    cfg = Config(general={"title": "Radar", "min_display_importance": "medium"},
                 stack={}, categories=["backend"], sources=[], llm={})
    med = Item(id="1", title="Routine", url="https://x/1", source_type="rss",
               category="backend", published=NOW, summary="s", importance="medium")
    snap_path = _write_snap(tmp_path, _snap_with([med]))
    run_render(cfg, snap_path, out, force=True)
    digest = (out / "digests" / "2026-07-17.html").read_text()
    assert 'class="priority"' not in digest
    assert "Routine" in digest    # still shown as a card


def test_index_uses_archive_layout(tmp_path):
    out = tmp_path / "output"
    high = Item(id="1", title="Big News", url="https://x/1", source_type="rss",
                category="backend", published=NOW, summary="s", importance="high")
    snap_path = _write_snap(tmp_path, _snap_with([high]))
    run_render(_cfg(), snap_path, out, force=True)
    index = (out / "index.html").read_text()
    assert 'class="latest-card"' in index
    assert "Intelligence Archive" in index
    assert 'id="filter"' in index          # search retained


def test_stylesheet_drops_serif_and_styles_new_hooks(tmp_path):
    out = tmp_path / "output"
    it = Item(id="1", title="X", url="https://x", source_type="rss",
              category="backend", published=NOW, summary="s", importance="high")
    snap_path = _write_snap(tmp_path, _snap_with([it]))
    run_render(_cfg(), snap_path, out, force=True)
    css = (out / "styles.css").read_text()
    # magazine serif display font is gone (spec: sans everywhere)
    assert "Iowan Old Style" not in css and "Palatino" not in css
    # new structural hooks are styled
    for hook in (".kpi-row", ".kpi--critical", ".priority-row", ".section-head",
                 ".latest-card"):
        assert hook in css
    # dark theme retained
    assert "prefers-color-scheme: dark" in css
