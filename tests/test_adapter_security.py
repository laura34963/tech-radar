from datetime import datetime, timezone
from pathlib import Path
import httpx
import pytest
from radar.adapters.security import SecurityAdapter, severity_from_score

FIX = (Path(__file__).parent / "fixtures" / "osv.json").read_text()
NOW = datetime(2026, 7, 17, tzinfo=timezone.utc)


def test_severity_buckets():
    assert severity_from_score(9.8) == "critical"
    assert severity_from_score(7.5) == "high"
    assert severity_from_score(5.0) == "medium"
    assert severity_from_score(1.0) == "low"


def test_osv_maps_vuln_with_severity():
    client = httpx.Client(transport=httpx.MockTransport(
        lambda req: httpx.Response(200, text=FIX)))
    cfg = type("C", (), {"stack": {"packages": ["widget"], "ecosystems": ["npm"]}})()
    src = {"feed": "osv", "category": "security"}
    items = SecurityAdapter().fetch(src, cfg, client=client, now=NOW)
    assert len(items) == 1
    assert items[0].severity == "critical"
    assert items[0].source_type == "security"


def test_ghsa_not_implemented_in_v1():
    with pytest.raises(NotImplementedError, match="ghsa"):
        SecurityAdapter().fetch({"feed": "ghsa", "category": "security"},
                                type("C", (), {"stack": {}})(),
                                client=httpx.Client(), now=NOW)
