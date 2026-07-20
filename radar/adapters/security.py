from __future__ import annotations
import logging
import os
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


# GitHub's advisory API uses its own ecosystem slugs, which differ from OSV's
# (e.g. GitHub says "pip" where OSV says "PyPI"). Only these are valid GHSA values.
_GHSA_ECOSYSTEM = {
    "npm": "npm", "pypi": "pip", "pip": "pip", "rubygems": "rubygems",
    "go": "go", "maven": "maven", "nuget": "nuget",
    "packagist": "composer", "composer": "composer",
    "cargo": "rust", "crates.io": "rust", "rust": "rust",
    "hex": "erlang", "erlang": "erlang", "pub": "pub", "swift": "swift",
    "actions": "actions",
}


def _ghsa_ecosystem(name: str) -> str | None:
    """Map a configured ecosystem name to GitHub's slug, or None if GHSA has no
    equivalent (that ecosystem is then skipped)."""
    return _GHSA_ECOSYSTEM.get(name.strip().lower())


def _ghsa_severity(label: str | None) -> str:
    """GHSA severity is one of critical/high/medium/low/unknown. Map "unknown"
    to "medium" so an advisory still surfaces (matching OSV's default)."""
    label = (label or "").lower()
    return label if label in ("critical", "high", "medium", "low") else "medium"


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
        if feed == "osv":
            return self._fetch_osv(source, cfg, client=client, now=now)
        if feed == "ghsa":
            return self._fetch_ghsa(source, cfg, client=client, now=now)
        raise NotImplementedError(f"security feed {feed!r} not implemented (osv, ghsa only)")

    def _fetch_osv(self, source: dict, cfg, *, client: httpx.Client, now: datetime) -> list[Item]:
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

    def _fetch_ghsa(self, source: dict, cfg, *, client: httpx.Client, now: datetime) -> list[Item]:
        packages = cfg.stack.get("packages", [])
        ecosystems = [e for e in (_ghsa_ecosystem(x) for x in cfg.stack.get("ecosystems", [])) if e]
        if not packages or not ecosystems:
            # GHSA filters by (affects, ecosystem); without both, a query would be
            # unscoped and return the entire advisory database.
            log.warning("security(ghsa): needs both [stack].packages and mappable [stack].ecosystems; skipping")
            return []
        headers = {"Accept": "application/vnd.github+json"}
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        affects = ",".join(packages)
        pkg_lower = {p.lower(): p for p in packages}
        items: list[Item] = []
        seen_ids: set[str] = set()
        # One request per ecosystem, OR-ing all packages via `affects` — fewer
        # calls than OSV's package×ecosystem cross product.
        for eco in ecosystems:
            try:
                resp = client.get(
                    "https://api.github.com/advisories",
                    params={"ecosystem": eco, "affects": affects, "per_page": 100},
                    headers=headers, timeout=20.0, follow_redirects=True)
                resp.raise_for_status()
                advisories = resp.json()
            except Exception as e:  # per-ecosystem isolation: one bad request must not kill the source
                log.warning("GHSA query ecosystem=%s failed: %s", eco, e)
                continue
            for adv in advisories:
                gid = adv.get("ghsa_id")
                if gid and gid in seen_ids:
                    continue
                try:
                    names = [(v.get("package") or {}).get("name")
                             for v in (adv.get("vulnerabilities") or [])]
                    matched = [pkg_lower[n.lower()] for n in names
                               if n and n.lower() in pkg_lower]
                    label = matched[0] if matched else \
                        (next((n for n in names if n), None) or "advisory")
                    url = adv.get("html_url") or f"https://github.com/advisories/{gid}"
                    raw = adv.get("published_at") or now.isoformat()
                    try:
                        published = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                    except (TypeError, ValueError):
                        published = now
                    items.append(Item(
                        id=item_id(gid or url),
                        title=f"{gid or 'advisory'}: {label}",
                        url=url,
                        source_type="security",
                        category=source["category"],
                        published=published,
                        summary=(adv.get("summary") or adv.get("description") or "")[:500],
                        severity=_ghsa_severity(adv.get("severity")),
                        stack_match=matched,
                    ))
                    if gid:
                        seen_ids.add(gid)
                except Exception as e:  # per-record isolation: one bad advisory must not kill the fetch
                    log.warning("skipping malformed GHSA advisory: %s", e)
                    continue
        return items
