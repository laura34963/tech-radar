import json
from datetime import datetime, timezone
from pathlib import Path
from radar.config import Config
from radar.item import Item
from radar.store import atomic_write_json, new_snapshot, item_to_dict, load_snapshot
from radar.pipeline.enrich import parse_enrich_response, run_enrich

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
