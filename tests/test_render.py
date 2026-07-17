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
