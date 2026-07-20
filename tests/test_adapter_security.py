from datetime import datetime, timezone
from pathlib import Path
import httpx
import pytest
from radar.adapters.security import SecurityAdapter, severity_from_score, _severity

FIX = (Path(__file__).parent / "fixtures" / "osv.json").read_text()
MALFORMED_FIX = (Path(__file__).parent / "fixtures" / "osv_malformed.json").read_text()
GHSA_FIX = (Path(__file__).parent / "fixtures" / "ghsa.json").read_text()
NOW = datetime(2026, 7, 17, tzinfo=timezone.utc)


def test_severity_buckets():
    assert severity_from_score(9.8) == "critical"
    assert severity_from_score(7.5) == "high"
    assert severity_from_score(5.0) == "medium"
    assert severity_from_score(1.0) == "low"


def test_severity_falls_back_to_label_when_no_score():
    vuln = {"database_specific": {"severity": "HIGH"}}
    assert _severity(vuln) == "high"


def test_severity_defaults_to_medium_and_is_not_dropped():
    vuln = {"id": "GHSA-nothing", "summary": "no score or label"}
    assert _severity(vuln) == "medium"


def test_osv_maps_vuln_with_severity():
    client = httpx.Client(transport=httpx.MockTransport(
        lambda req: httpx.Response(200, text=FIX)))
    cfg = type("C", (), {"stack": {"packages": ["widget"], "ecosystems": ["npm"]}})()
    src = {"feed": "osv", "category": "security"}
    items = SecurityAdapter().fetch(src, cfg, client=client, now=NOW)
    assert len(items) == 1
    assert items[0].severity == "critical"
    assert items[0].source_type == "security"


def test_nvd_not_implemented():
    with pytest.raises(NotImplementedError, match="nvd"):
        SecurityAdapter().fetch({"feed": "nvd", "category": "security"},
                                type("C", (), {"stack": {}})(),
                                client=httpx.Client(), now=NOW)


def _ghsa_cfg(packages, ecosystems):
    return type("C", (), {"stack": {"packages": packages, "ecosystems": ecosystems}})()


def test_ghsa_maps_advisories():
    client = httpx.Client(transport=httpx.MockTransport(
        lambda req: httpx.Response(200, text=GHSA_FIX)))
    items = SecurityAdapter().fetch(
        {"feed": "ghsa", "category": "security"},
        _ghsa_cfg(["widget"], ["npm"]), client=client, now=NOW)
    by_title = {it.title: it for it in items}
    hit = by_title["GHSA-aaaa-bbbb-cccc: widget"]
    assert hit.severity == "high"
    assert hit.source_type == "security"
    assert hit.stack_match == ["widget"]
    assert hit.url == "https://github.com/advisories/GHSA-aaaa-bbbb-cccc"
    assert hit.published.astimezone(timezone.utc) == datetime(2026, 7, 15, 12, tzinfo=timezone.utc)
    # severity "unknown" -> medium so it still surfaces; unmatched pkg -> empty stack_match
    other = by_title["GHSA-dddd-eeee-ffff: gadget"]
    assert other.severity == "medium"
    assert other.stack_match == []


def test_ghsa_maps_ecosystem_name_to_github():
    cap = {}

    def handler(req):
        cap["ecosystem"] = req.url.params.get("ecosystem")
        cap["affects"] = req.url.params.get("affects")
        return httpx.Response(200, json=[])

    client = httpx.Client(transport=httpx.MockTransport(handler))
    SecurityAdapter().fetch({"feed": "ghsa", "category": "security"},
                            _ghsa_cfg(["httpx"], ["pypi"]), client=client, now=NOW)
    assert cap["ecosystem"] == "pip"   # GitHub calls PyPI "pip"
    assert cap["affects"] == "httpx"


def test_ghsa_isolates_failure_across_ecosystems():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(500, text="boom")
        return httpx.Response(200, text=GHSA_FIX)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    items = SecurityAdapter().fetch({"feed": "ghsa", "category": "security"},
                                    _ghsa_cfg(["widget"], ["npm", "pypi"]),
                                    client=client, now=NOW)
    assert calls["n"] == 2
    assert any(it.title.startswith("GHSA-aaaa") for it in items)


def test_ghsa_without_packages_skips(caplog):
    import logging
    client = httpx.Client(transport=httpx.MockTransport(
        lambda req: httpx.Response(200, text=GHSA_FIX)))
    with caplog.at_level(logging.WARNING):
        items = SecurityAdapter().fetch({"feed": "ghsa", "category": "security"},
                                        _ghsa_cfg([], ["npm"]), client=client, now=NOW)
    assert items == []
    assert any("packages" in r.message for r in caplog.records)


def test_osv_skips_malformed_record_keeps_good_one(caplog):
    client = httpx.Client(transport=httpx.MockTransport(
        lambda req: httpx.Response(200, text=MALFORMED_FIX)))
    cfg = type("C", (), {"stack": {"packages": ["widget"], "ecosystems": ["npm"]}})()
    src = {"feed": "osv", "category": "security"}
    items = SecurityAdapter().fetch(src, cfg, client=client, now=NOW)
    assert len(items) == 1
    assert "GHSA-good" in items[0].title
    assert items[0].severity == "critical"


def test_osv_query_includes_canonical_ecosystem():
    cap = {}

    def handler(req):
        import json
        cap.setdefault("bodies", []).append(json.loads(req.content))
        return httpx.Response(200, json={"vulns": []})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    cfg = type("C", (), {"stack": {"packages": ["rails"], "ecosystems": ["rubygems"]}})()
    SecurityAdapter().fetch({"feed": "osv", "category": "security"},
                            cfg, client=client, now=NOW)
    assert cap["bodies"] == [{"package": {"name": "rails", "ecosystem": "RubyGems"}}]


def test_osv_without_ecosystems_skips(caplog):
    import logging
    client = httpx.Client(transport=httpx.MockTransport(
        lambda req: httpx.Response(200, text=FIX)))
    cfg = type("C", (), {"stack": {"packages": ["rails"], "ecosystems": []}})()
    with caplog.at_level(logging.WARNING):
        items = SecurityAdapter().fetch({"feed": "osv", "category": "security"},
                                        cfg, client=client, now=NOW)
    assert items == []
    assert any("ecosystems" in r.message for r in caplog.records)


def test_osv_query_isolates_400_across_combos():
    # first combo 400s, second returns a vuln -> source still yields the good one
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(400, text="bad request")
        return httpx.Response(200, text=FIX)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    cfg = type("C", (), {"stack": {"packages": ["widget"], "ecosystems": ["PyPI", "npm"]}})()
    items = SecurityAdapter().fetch({"feed": "osv", "category": "security"},
                                    cfg, client=client, now=NOW)
    assert calls["n"] == 2 and len(items) == 1 and items[0].severity == "critical"
