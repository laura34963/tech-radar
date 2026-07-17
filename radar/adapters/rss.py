from __future__ import annotations
from datetime import datetime
import httpx
from radar.item import Item
from radar.adapters._feed import parse_feed


class RssAdapter:
    type = "rss"

    def fetch(self, source: dict, cfg, *, client: httpx.Client, now: datetime) -> list[Item]:
        resp = client.get(source["url"], timeout=20.0, follow_redirects=True)
        resp.raise_for_status()
        return parse_feed(resp.text, source["category"], source_type="rss")
