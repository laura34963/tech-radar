from __future__ import annotations
import logging
from datetime import datetime
import httpx
from radar.item import Item, item_id

log = logging.getLogger("radar.registry")

# Recent versions kept per package before lookback filtering; mirrors github's per_page=10.
_MAX_VERSIONS = 10

_DISPLAY = {"npm": "npm", "pypi": "PyPI", "rubygems": "RubyGems"}


def _parse_iso(raw: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (TypeError, ValueError, AttributeError):
        return None


class RegistryAdapter:
    type = "registry"

    def fetch(self, source: dict, cfg, *, client: httpx.Client, now: datetime) -> list[Item]:
        registry = source["registry"]
        if registry not in _DISPLAY:
            raise NotImplementedError(
                f"registry {registry!r} not implemented (npm, pypi, rubygems only)")
        items: list[Item] = []
        for pkg in source["packages"]:
            try:
                releases = self._fetch_package(registry, pkg, client=client)
            except Exception as e:  # per-package isolation: one bad package must not kill the source
                log.warning("registry(%s) %s failed: %s", registry, pkg, e)
                continue
            for version, published in releases[:_MAX_VERSIONS]:
                url = self._url(registry, pkg, version)
                items.append(Item(
                    id=item_id(url),
                    title=f"{pkg} {version}",
                    url=url,
                    source_type="registry",
                    category=source["category"],
                    published=published,
                    summary=f"New {_DISPLAY[registry]} release: {pkg} {version}",
                    stack_match=[pkg],
                ))
        return items

    def _fetch_package(self, registry: str, pkg: str, *,
                       client: httpx.Client) -> list[tuple[str, datetime]]:
        """Return [(version, published)] for one package, sorted newest-first."""
        if registry == "npm":
            resp = client.get(f"https://registry.npmjs.org/{pkg}",
                              timeout=20.0, follow_redirects=True)
            resp.raise_for_status()
            times = resp.json().get("time", {})
            # the "time" map mixes version keys with "created"/"modified" metadata
            pairs = [(v, _parse_iso(t)) for v, t in times.items()
                     if v not in ("created", "modified")]
        elif registry == "pypi":
            resp = client.get(f"https://pypi.org/pypi/{pkg}/json",
                              timeout=20.0, follow_redirects=True)
            resp.raise_for_status()
            pairs = []
            for v, files in resp.json().get("releases", {}).items():
                if not files:  # yanked or no distribution uploaded -> no publish date
                    continue
                pairs.append((v, _parse_iso(files[0].get("upload_time_iso_8601"))))
        else:  # rubygems
            resp = client.get(f"https://rubygems.org/api/v1/versions/{pkg}.json",
                              timeout=20.0, follow_redirects=True)
            resp.raise_for_status()
            pairs = [(r.get("number"), _parse_iso(r.get("created_at")))
                     for r in resp.json()]

        clean = [(v, p) for v, p in pairs if v and p is not None]
        clean.sort(key=lambda vp: vp[1], reverse=True)
        return clean

    @staticmethod
    def _url(registry: str, pkg: str, version: str) -> str:
        if registry == "npm":
            return f"https://www.npmjs.com/package/{pkg}/v/{version}"
        if registry == "pypi":
            return f"https://pypi.org/project/{pkg}/{version}/"
        return f"https://rubygems.org/gems/{pkg}/versions/{version}"
