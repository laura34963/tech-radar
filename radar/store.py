from __future__ import annotations
import json
import os
from datetime import datetime
from pathlib import Path
from radar.item import Item


def atomic_write_text(path: Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def atomic_write_json(path: Path, data: dict) -> None:
    atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True))


def new_snapshot(date: str) -> dict:
    return {
        "meta": {"schema_version": 1, "date": date,
                 "sources": {}, "enriched": {}, "rendered": {}},
        "digest_summary": None,
        "items": [],
    }


def load_snapshot(path: Path) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def item_to_dict(it: Item) -> dict:
    d = it.__dict__.copy()
    d["published"] = it.published.isoformat()
    return d


def item_from_dict(d: dict) -> Item:
    d = dict(d)
    d["published"] = datetime.fromisoformat(d["published"])
    return Item(**d)
