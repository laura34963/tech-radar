from __future__ import annotations
import re
from datetime import datetime, timezone
from time import mktime
import feedparser
from radar.item import Item, item_id

_TAGS = re.compile(r"<[^>]+>")


def _clean(text: str, limit: int = 500) -> str:
    text = _TAGS.sub("", text or "").strip()
    return text[:limit]


def _published(entry) -> datetime:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if parsed:
        return datetime.fromtimestamp(mktime(parsed), tz=timezone.utc)
    return datetime.now(tz=timezone.utc)  # NOTE: only reached for undated entries


def parse_feed(content: str, category: str, *, source_type: str,
               provider: str | None = None) -> list[Item]:
    feed = feedparser.parse(content)
    items: list[Item] = []
    for e in feed.entries:
        url = e.get("link", "")
        key = e.get("id") or url or e.get("title", "")
        items.append(Item(
            id=item_id(key),
            title=e.get("title", "").strip(),
            url=url,
            source_type=source_type,
            category=category,
            published=_published(e),
            summary=_clean(e.get("summary", "")),
            provider=provider,
        ))
    return items
