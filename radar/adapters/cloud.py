from __future__ import annotations
from dataclasses import replace
from datetime import datetime
import httpx
from radar.item import Item
from radar.adapters._feed import parse_feed


class CloudAdapter:
    type = "cloud"

    def fetch(self, source: dict, cfg, *, client: httpx.Client, now: datetime) -> list[Item]:
        resp = client.get(source["url"], timeout=20.0, follow_redirects=True)
        resp.raise_for_status()
        items = parse_feed(resp.text, source["category"],
                           source_type="cloud", provider=source.get("provider"), now=now)
        services = [s.lower() for s in source.get("services", [])]
        if not services:
            return items
        kept: list[Item] = []
        for it in items:
            haystack = f"{it.title} {it.summary}".lower()
            matched = [s for s in services if s in haystack]
            if matched:
                kept.append(replace(it, tags=[*it.tags, *matched]))
        return kept
