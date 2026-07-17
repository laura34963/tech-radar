from datetime import datetime, timezone
from radar.item import Item
from radar.store import (atomic_write_text, new_snapshot, load_snapshot,
                         item_to_dict, item_from_dict)


def test_atomic_write_leaves_no_tmp(tmp_path):
    p = tmp_path / "sub" / "f.txt"
    atomic_write_text(p, "hi")
    assert p.read_text() == "hi"
    assert not list((tmp_path / "sub").glob("*.tmp"))


def test_new_snapshot_shape():
    s = new_snapshot("2026-07-17")
    assert s["meta"]["schema_version"] == 1
    assert s["meta"]["date"] == "2026-07-17"
    assert s["meta"]["sources"] == {} and s["items"] == []


def test_load_missing_returns_empty(tmp_path):
    assert load_snapshot(tmp_path / "nope.json") == {}


def test_item_roundtrip():
    it = Item(id="1", title="t", url="u", source_type="rss", category="backend",
              published=datetime(2026, 7, 17, 9, tzinfo=timezone.utc), summary="s",
              importance="high", tags=["lambda"], stack_match=["rails"])
    back = item_from_dict(item_to_dict(it))
    assert back == it
