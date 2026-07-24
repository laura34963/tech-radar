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
    board: str | None = None
    llm: dict | None = None
