from __future__ import annotations
from datetime import datetime, timezone
import httpx
from radar.item import Item, item_id


def severity_from_score(score: float) -> str:
    if score >= 9.0:
        return "critical"
    if score >= 7.0:
        return "high"
    if score >= 4.0:
        return "medium"
    return "low"


def _cvss_score(vuln: dict) -> float:
    ds = vuln.get("database_specific", {}).get("cvss", {})
    return float(ds.get("score", 0.0))


class SecurityAdapter:
    type = "security"

    def fetch(self, source: dict, cfg, *, client: httpx.Client, now: datetime) -> list[Item]:
        feed = source["feed"]
        if feed != "osv":
            raise NotImplementedError(f"security feed {feed!r} not implemented in v1 (osv only)")
        items: list[Item] = []
        for pkg in cfg.stack.get("packages", []):
            resp = client.post("https://api.osv.dev/v1/query",
                               json={"package": {"name": pkg}}, timeout=20.0)
            resp.raise_for_status()
            for v in resp.json().get("vulns", []):
                refs = v.get("references", [])
                url = refs[0]["url"] if refs else f"https://osv.dev/vulnerability/{v.get('id')}"
                modified = v.get("modified", now.isoformat()).replace("Z", "+00:00")
                items.append(Item(
                    id=item_id(v.get("id") or url),
                    title=f"{v.get('id', 'advisory')}: {pkg}",
                    url=url,
                    source_type="security",
                    category=source["category"],
                    published=datetime.fromisoformat(modified),
                    summary=(v.get("summary") or v.get("details") or "")[:500],
                    severity=severity_from_score(_cvss_score(v)),
                    stack_match=[pkg],
                ))
        return items
