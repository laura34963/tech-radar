from __future__ import annotations
from datetime import datetime
import httpx
from radar.item import Item, item_id


class SocialAdapter:
    type = "social"

    def fetch(self, source: dict, cfg, *, client: httpx.Client, now: datetime) -> list[Item]:
        src = source["source"]
        if src != "hn":
            raise NotImplementedError(f"social source {src!r} not implemented in v1 (hn only)")
        resp = client.get("https://hn.algolia.com/api/v1/search_by_date",
                          params={"query": source.get("query", ""), "tags": "story"},
                          timeout=20.0, follow_redirects=True)
        resp.raise_for_status()
        min_points = source.get("min_points", 0)
        items: list[Item] = []
        for hit in resp.json().get("hits", []):
            if hit.get("points", 0) < min_points:
                continue
            url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
            items.append(Item(
                id=item_id(hit.get("objectID") or url),
                title=hit.get("title", "").strip(),
                url=url,
                source_type="social",
                category=source["category"],
                published=datetime.fromisoformat(hit["created_at"].replace("Z", "+00:00")),
                summary=f"{hit.get('points', 0)} points on Hacker News",
            ))
        return items
