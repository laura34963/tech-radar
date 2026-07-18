from __future__ import annotations
import os
from datetime import datetime
import httpx
from radar.item import Item, item_id


class GithubAdapter:
    type = "github"

    def fetch(self, source: dict, cfg, *, client: httpx.Client, now: datetime) -> list[Item]:
        repo = source["repo"]
        headers = {"Accept": "application/vnd.github+json"}
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        resp = client.get(f"https://api.github.com/repos/{repo}/releases",
                          params={"per_page": 10}, headers=headers, timeout=20.0,
                          follow_redirects=True)  # GitHub 301s renamed/canonical repo paths
        resp.raise_for_status()
        items: list[Item] = []
        for rel in resp.json():
            url = rel.get("html_url", "")
            items.append(Item(
                id=item_id(url or f"{repo}:{rel.get('tag_name')}"),
                title=f"{repo} {rel.get('tag_name', '')}".strip(),
                url=url,
                source_type="github",
                category=source["category"],
                published=datetime.fromisoformat(rel["published_at"].replace("Z", "+00:00")),
                summary=(rel.get("body") or "")[:500],
            ))
        return items
