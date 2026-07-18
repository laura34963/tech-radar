from __future__ import annotations
import logging
from datetime import datetime, timezone
import httpx
from radar.item import Item, item_id

log = logging.getLogger("radar.security")

_LABEL_TO_SEVERITY = {
    "CRITICAL": "critical",
    "HIGH": "high",
    "MODERATE": "medium",
    "MEDIUM": "medium",
    "LOW": "low",
}


_ECOSYSTEM_CANONICAL = {
    "rubygems": "RubyGems", "npm": "npm", "pypi": "PyPI", "go": "Go",
    "crates.io": "crates.io", "cargo": "crates.io", "maven": "Maven",
    "packagist": "Packagist", "composer": "Packagist", "nuget": "NuGet",
    "hex": "Hex", "pub": "Pub",
}


def _canonical_ecosystem(name: str) -> str:
    """OSV ecosystem names are case-sensitive (e.g. "RubyGems"). Normalize common
    spellings; pass through anything unrecognized so an exact OSV name still works."""
    return _ECOSYSTEM_CANONICAL.get(name.strip().lower(), name)


def severity_from_score(score: float) -> str:
    if score >= 9.0:
        return "critical"
    if score >= 7.0:
        return "high"
    if score >= 4.0:
        return "medium"
    return "low"


def _severity(vuln: dict) -> str:
    """Resolve an OSV record's severity. Most real-world OSV records lack a
    numeric CVSS score, so this tries, in order: (1) database_specific.cvss.score,
    (2) the database_specific.severity label, (3) a "medium" default so an
    unparseable record still surfaces instead of being silently dropped as low.

    # TODO(v2): compute a precise CVSS base score by parsing the CVSS vector
    # string found in vuln["severity"][*]["score"] (e.g. "CVSS:3.1/AV:N/AC:L/...")
    # instead of relying on database_specific.
    """
    ds = vuln.get("database_specific") or {}
    cvss = ds.get("cvss") or {}
    score = cvss.get("score")
    if score is not None:
        try:
            return severity_from_score(float(score))
        except (TypeError, ValueError):
            pass
    label = ds.get("severity")
    if isinstance(label, str) and label.upper() in _LABEL_TO_SEVERITY:
        return _LABEL_TO_SEVERITY[label.upper()]
    return "medium"


class SecurityAdapter:
    type = "security"

    def fetch(self, source: dict, cfg, *, client: httpx.Client, now: datetime) -> list[Item]:
        feed = source["feed"]
        if feed != "osv":
            raise NotImplementedError(f"security feed {feed!r} not implemented in v1 (osv only)")
        packages = cfg.stack.get("packages", [])
        ecosystems = [_canonical_ecosystem(e) for e in cfg.stack.get("ecosystems", [])]
        if not packages or not ecosystems:
            # OSV rejects a package query without an ecosystem (400), so both are required.
            log.warning("security(osv): needs both [stack].packages and [stack].ecosystems; skipping")
            return []
        items: list[Item] = []
        seen_ids: set[str] = set()
        # OSV requires (name, ecosystem). We don't track which package lives in
        # which ecosystem, so query the cross product; wrong combos just return
        # no vulns. (v2: pair packages with their ecosystem to cut request count.)
        for pkg in packages:
            for eco in ecosystems:
                try:
                    resp = client.post(
                        "https://api.osv.dev/v1/query",
                        json={"package": {"name": pkg, "ecosystem": eco}}, timeout=20.0)
                    resp.raise_for_status()
                    vulns = resp.json().get("vulns", [])
                except Exception as e:  # per-query isolation: one bad combo must not kill the source
                    log.warning("OSV query %s/%s failed: %s", eco, pkg, e)
                    continue
                for v in vulns:
                    vid = v.get("id")
                    if vid and vid in seen_ids:
                        continue
                    try:
                        refs = v.get("references", [])
                        url = refs[0]["url"] if refs else f"https://osv.dev/vulnerability/{v.get('id')}"
                        raw = v.get("modified") or now.isoformat()
                        try:
                            published = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                        except ValueError:
                            published = now
                        items.append(Item(
                            id=item_id(v.get("id") or url),
                            title=f"{v.get('id', 'advisory')}: {pkg}",
                            url=url,
                            source_type="security",
                            category=source["category"],
                            published=published,
                            summary=(v.get("summary") or v.get("details") or "")[:500],
                            severity=_severity(v),
                            stack_match=[pkg],
                        ))
                        if vid:
                            seen_ids.add(vid)
                    except Exception as e:  # per-record isolation: one bad OSV record must not kill the fetch
                        log.warning("skipping malformed OSV record for %s: %s", pkg, e)
                        continue
        return items
