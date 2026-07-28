from __future__ import annotations
import hashlib
from dataclasses import dataclass, field
from datetime import datetime

IMPORTANCE_ORDER: dict[str, int] = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def item_id(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class Item:
    id: str
    title: str
    url: str
    source_type: str
    category: str
    published: datetime
    summary: str
    importance: str = "low"
    provider: str | None = None
    tags: list[str] = field(default_factory=list)
    severity: str | None = None
    stack_match: list[str] = field(default_factory=list)
    # which category_keywords terms admitted this item; the counterpart to
    # stack_match, so an item's boost is always explainable.
    keyword_match: list[str] = field(default_factory=list)
    board: str | None = None
    # Why this item lost its card, or None. Set by the source-fairness pass and
    # read by render (routes to "also noted") and enrich (skips it). A reason
    # string rather than a bool so the logs can say which rule fired.
    demoted: str | None = None
    llm: dict | None = None
