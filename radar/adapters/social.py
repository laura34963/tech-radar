from __future__ import annotations
from datetime import datetime, timezone
import httpx
from radar.item import Item, item_id

# Reddit rejects the default httpx User-Agent (429/403); it wants a descriptive one.
_REDDIT_UA = "tech-radar/1.0 (kdan tech radar)"


class SocialAdapter:
    type = "social"

    def fetch(self, source: dict, cfg, *, client: httpx.Client, now: datetime) -> list[Item]:
        src = source["source"]
        if src == "hn":
            return self._fetch_hn(source, client=client, now=now)
        if src == "reddit":
            return self._fetch_reddit(source, client=client, now=now)
        raise NotImplementedError(f"social source {src!r} not implemented (hn, reddit only)")

    def _fetch_hn(self, source: dict, *, client: httpx.Client, now: datetime) -> list[Item]:
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

    def _fetch_reddit(self, source: dict, *, client: httpx.Client, now: datetime) -> list[Item]:
        subreddit = source.get("subreddit")
        if not subreddit:
            raise ValueError("social source 'reddit' requires 'subreddit'")
        query = source.get("query", "")
        if query:
            url = f"https://www.reddit.com/r/{subreddit}/search.json"
            params = {"q": query, "restrict_sr": 1, "sort": "new", "limit": 50}
        else:
            url = f"https://www.reddit.com/r/{subreddit}/new.json"
            params = {"limit": 50}
        resp = client.get(url, params=params, headers={"User-Agent": _REDDIT_UA},
                          timeout=20.0, follow_redirects=True)
        resp.raise_for_status()
        min_points = source.get("min_points", 0)
        items: list[Item] = []
        for child in resp.json().get("data", {}).get("children", []):
            d = child.get("data", {})
            if d.get("score", 0) < min_points:
                continue
            permalink = d.get("permalink", "")
            link = d.get("url") or (f"https://www.reddit.com{permalink}" if permalink else "")
            items.append(Item(
                id=item_id(d.get("id") or link),
                title=(d.get("title") or "").strip(),
                url=link,
                source_type="social",
                category=source["category"],
                published=datetime.fromtimestamp(d["created_utc"], tz=timezone.utc),
                summary=f"{d.get('score', 0)} points on r/{subreddit}",
            ))
        return items
