import json
from datetime import datetime, timezone
from pathlib import Path
import httpx
import pytest
from radar.adapters.registry import RegistryAdapter

FIXDIR = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 7, 17, tzinfo=timezone.utc)


def _client(routes):
    """routes: {url_fragment: (status, body_text)}; anything unmatched -> 404."""
    def handler(req):
        u = str(req.url)
        for frag, (status, text) in routes.items():
            if frag in u:
                return httpx.Response(status, text=text)
        return httpx.Response(404, text="{}")
    return httpx.Client(transport=httpx.MockTransport(handler))


def _fetch(registry, packages, routes, category="backend"):
    src = {"registry": registry, "packages": packages, "category": category}
    return RegistryAdapter().fetch(src, None, client=_client(routes), now=NOW)


def test_npm_maps_versions():
    fix = (FIXDIR / "npm_react.json").read_text()
    items = _fetch("npm", ["react"], {"registry.npmjs.org/react": (200, fix)},
                   category="frontend")
    # only real versions become items — "created"/"modified" keys are not versions
    assert len(items) == 2
    it = next(i for i in items if i.title == "react 18.3.1")
    assert it.url == "https://www.npmjs.com/package/react/v/18.3.1"
    assert it.source_type == "registry"
    assert it.category == "frontend"
    assert it.stack_match == ["react"]
    assert it.published.astimezone(timezone.utc) == datetime(2026, 7, 15, 10, tzinfo=timezone.utc)


def test_pypi_maps_versions_and_skips_fileless():
    fix = (FIXDIR / "pypi_httpx.json").read_text()
    items = _fetch("pypi", ["httpx"], {"pypi.org/pypi/httpx/json": (200, fix)})
    titles = {i.title for i in items}
    assert titles == {"httpx 0.27.1", "httpx 0.27.2"}  # 0.27.0 has no files -> skipped
    it = next(i for i in items if i.title == "httpx 0.27.2")
    assert it.url == "https://pypi.org/project/httpx/0.27.2/"
    assert it.published.astimezone(timezone.utc) == datetime(2026, 7, 16, 8, tzinfo=timezone.utc)


def test_rubygems_maps_versions():
    fix = (FIXDIR / "rubygems_rails.json").read_text()
    items = _fetch("rubygems", ["rails"],
                   {"rubygems.org/api/v1/versions/rails.json": (200, fix)})
    it = next(i for i in items if i.title == "rails 7.3.0")
    assert it.url == "https://rubygems.org/gems/rails/versions/7.3.0"
    assert it.published.astimezone(timezone.utc) == datetime(2026, 7, 14, 9, tzinfo=timezone.utc)


def test_caps_at_10_most_recent_versions():
    times = {"created": "2020-01-01T00:00:00Z"}
    for n in range(15):
        times[f"1.0.{n}"] = f"2026-07-{n + 1:02d}T00:00:00Z"
    doc = json.dumps({"name": "x", "time": times})
    items = _fetch("npm", ["x"], {"registry.npmjs.org/x": (200, doc)})
    assert len(items) == 10
    assert any(i.title == "x 1.0.14" for i in items)   # newest kept
    assert all(i.title != "x 1.0.0" for i in items)    # oldest dropped


def test_per_package_isolation():
    fix = (FIXDIR / "npm_react.json").read_text()
    # react resolves; axios 404s and must not kill the source
    items = _fetch("npm", ["react", "axios"],
                   {"registry.npmjs.org/react": (200, fix)}, category="frontend")
    assert any(i.title.startswith("react ") for i in items)
    assert not any(i.title.startswith("axios ") for i in items)


def test_unknown_registry_not_implemented():
    with pytest.raises(NotImplementedError, match="conda"):
        RegistryAdapter().fetch({"registry": "conda", "packages": ["x"], "category": "backend"},
                                None, client=httpx.Client(), now=NOW)
