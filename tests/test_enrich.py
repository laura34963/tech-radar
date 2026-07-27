import json
import re
from datetime import datetime, timezone
from pathlib import Path
from radar.config import Config
from radar.item import Item
from radar.store import atomic_write_json, new_snapshot, item_to_dict, load_snapshot
from radar.pipeline.enrich import parse_enrich_response, run_enrich, _chunk, _enrich_chunk

NOW = datetime(2026, 7, 17, tzinfo=timezone.utc)


class FakeProvider:
    def __init__(self, payload, fail=False):
        self.payload, self.fail, self.calls = payload, fail, 0

    def complete(self, system, user):
        self.calls += 1
        if self.fail:
            raise RuntimeError("boom")
        return self.payload


def _cfg():
    return Config(general={}, stack={"packages": ["rails"]},
                  categories=["backend"], sources=[],
                  llm={"enabled": True, "max_items_to_enrich": 40})


def _snap(items):
    s = new_snapshot("2026-07-17")
    s["items"] = [item_to_dict(i) for i in items]
    return s


def _high(id):
    return Item(id=id, title="t", url="u", source_type="rss", category="backend",
                published=NOW, summary="s", importance="high")


def test_parse_enrich_response_tolerates_fences():
    text = '```json\n{"1": {"summary": "s"}}\n```'
    assert parse_enrich_response(text) == {"1": {"summary": "s"}}


def test_run_enrich_none_provider_is_noop(tmp_path):
    p = tmp_path / "s.json"
    atomic_write_json(p, _snap([_high("1")]))
    out = run_enrich(_cfg(), p, provider=None)
    assert out["items"][0].get("llm") is None


def test_run_enrich_populates_llm_fields(tmp_path):
    p = tmp_path / "s.json"
    atomic_write_json(p, _snap([_high("1")]))
    payload = json.dumps({"1": {"summary": "S", "detail": "D",
                                "why_it_matters": "W", "recommended_action": "A"}})
    run_enrich(_cfg(), p, provider=FakeProvider(payload))
    saved = load_snapshot(p)
    assert saved["items"][0]["llm"]["recommended_action"] == "A"
    assert saved["meta"]["enriched"]["backend"] is True


def test_run_enrich_dedupes_repeated_fields(tmp_path):
    p = tmp_path / "s.json"
    atomic_write_json(p, _snap([_high("1")]))
    # detail repeats summary, why repeats it again; action is distinct
    payload = json.dumps({"1": {"summary": "同一段文字", "detail": "同一段文字",
                                "why_it_matters": " 同一段文字 ",
                                "recommended_action": "升級到 1.2.3"}})
    run_enrich(_cfg(), p, provider=FakeProvider(payload))
    llm = load_snapshot(p)["items"][0]["llm"]
    assert llm["summary"] == "同一段文字"
    assert llm["detail"] == ""              # duplicate of summary -> blanked
    assert llm["why_it_matters"] == ""      # duplicate (whitespace-insensitive)
    assert llm["recommended_action"] == "升級到 1.2.3"  # distinct -> kept


def test_run_enrich_degrades_on_provider_error(tmp_path):
    p = tmp_path / "s.json"
    atomic_write_json(p, _snap([_high("1")]))
    run_enrich(_cfg(), p, provider=FakeProvider("", fail=True))
    saved = load_snapshot(p)
    assert saved["items"][0].get("llm") is None  # left unenriched, no crash


def test_run_enrich_missing_snapshot_is_noop(tmp_path):
    p = tmp_path / "does-not-exist.json"
    fp = FakeProvider("")
    out = run_enrich(_cfg(), p, provider=fp)
    assert out == {}
    assert fp.calls == 0  # returned before ever touching the provider


def test_run_enrich_skips_already_enriched(tmp_path):
    p = tmp_path / "s.json"
    snap = _snap([_high("1")])
    snap["items"][0]["llm"] = {"summary": "old"}
    atomic_write_json(p, snap)
    fp = FakeProvider(json.dumps({"1": {"summary": "new"}}))
    run_enrich(_cfg(), p, provider=fp)
    assert fp.calls == 0  # nothing to do


def test_chunk_splits_into_bounded_groups():
    assert _chunk(list(range(20)), 8) == [
        [0, 1, 2, 3, 4, 5, 6, 7],
        [8, 9, 10, 11, 12, 13, 14, 15],
        [16, 17, 18, 19],
    ]


def test_chunk_covers_every_item_once():
    items = list(range(23))
    chunks = _chunk(items, 5)
    assert [x for c in chunks for x in c] == items  # order preserved, no dupes


def test_chunk_empty_is_empty():
    assert _chunk([], 8) == []


def test_enrich_chunk_returns_deduped_fields_for_known_ids():
    chunk = [{"id": "1", "title": "t", "summary": "orig"}]
    payload = json.dumps({
        "1": {"summary": "S", "detail": "D", "why_it_matters": "W",
              "recommended_action": "A"},
        "999": {"summary": "ignored"},  # not in chunk -> dropped
    })
    out = _enrich_chunk("backend", chunk, {}, FakeProvider(payload))
    assert set(out) == {"1"}
    assert out["1"] == {"summary": "S", "detail": "D",
                        "why_it_matters": "W", "recommended_action": "A"}


def test_enrich_chunk_falls_back_to_item_summary():
    chunk = [{"id": "1", "title": "t", "summary": "orig"}]
    payload = json.dumps({"1": {"detail": "D"}})  # no summary in response
    out = _enrich_chunk("backend", chunk, {}, FakeProvider(payload))
    assert out["1"]["summary"] == "orig"


def test_enrich_chunk_raises_on_provider_error():
    chunk = [{"id": "1", "title": "t", "summary": "orig"}]
    import pytest
    with pytest.raises(RuntimeError):
        _enrich_chunk("backend", chunk, {}, FakeProvider("", fail=True))


class PerItemProvider:
    """Returns a summary for each id it sees in the prompt (`id=<id>`). Optionally
    raises when a given id appears, to simulate a single failing chunk."""
    def __init__(self, fail_id=None):
        self.fail_id, self.calls = fail_id, 0

    def complete(self, system, user):
        self.calls += 1
        ids = re.findall(r"id=(\S+)", user)
        if self.fail_id and self.fail_id in ids:
            raise RuntimeError("boom")
        return json.dumps({i: {"summary": f"S-{i}"} for i in ids})


def _cfg_chunked(chunk_size, concurrency):
    return Config(general={}, stack={"packages": ["rails"]},
                  categories=["backend"], sources=[],
                  llm={"enabled": True, "max_items_to_enrich": 40,
                       "enrich_chunk_size": chunk_size,
                       "enrich_concurrency": concurrency})


def test_run_enrich_chunks_and_merges_all_items(tmp_path):
    p = tmp_path / "s.json"
    atomic_write_json(p, _snap([_high(str(i)) for i in range(20)]))
    fp = PerItemProvider()
    run_enrich(_cfg_chunked(8, 4), p, provider=fp)
    saved = load_snapshot(p)
    assert fp.calls == 3  # 20 items / chunk 8 -> 3 chunks
    for i in range(20):
        assert saved["items"][i]["llm"]["summary"] == f"S-{i}"


def test_run_enrich_isolates_failed_chunk(tmp_path):
    p = tmp_path / "s.json"
    atomic_write_json(p, _snap([_high(str(i)) for i in range(20)]))
    # chunk 8 -> ids 0-7 / 8-15 / 16-19; fail the chunk containing id "10"
    run_enrich(_cfg_chunked(8, 4), p, provider=PerItemProvider(fail_id="10"))
    saved = {it["id"]: it for it in load_snapshot(p)["items"]}
    assert saved["10"].get("llm") is None       # its whole chunk skipped
    assert saved["8"].get("llm") is None         # same failed chunk
    assert saved["0"]["llm"]["summary"] == "S-0"  # other chunks enriched
    assert saved["19"]["llm"]["summary"] == "S-19"


def test_run_enrich_clamps_zero_chunk_size(tmp_path):
    p = tmp_path / "s.json"
    atomic_write_json(p, _snap([_high(str(i)) for i in range(3)]))
    fp = PerItemProvider()
    run_enrich(_cfg_chunked(0, 0), p, provider=fp)  # 0 -> clamp to 1
    assert fp.calls == 3  # one item per chunk
    assert load_snapshot(p)["items"][0]["llm"]["summary"] == "S-0"
