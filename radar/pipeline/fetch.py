from __future__ import annotations
import logging
from datetime import datetime, timedelta
from pathlib import Path
from radar.item import Item, IMPORTANCE_ORDER
from radar.adapters import ADAPTERS
from radar.store import (new_snapshot, load_snapshot, atomic_write_json,
                         item_to_dict, item_from_dict)

log = logging.getLogger("radar.fetch")


def importance_ge(a: str, b: str) -> bool:
    return IMPORTANCE_ORDER[a] >= IMPORTANCE_ORDER[b]


def within_lookback(published: datetime, now: datetime, days: int) -> bool:
    return published >= now - timedelta(days=days)


def stack_matches(it: Item, stack: dict) -> list[str]:
    hay = f"{it.title} {it.summary} {it.url}".lower()
    terms = stack.get("packages", []) + stack.get("frameworks", []) + stack.get("languages", [])
    return [t for t in terms if t.lower() in hay]


def score_importance(it: Item, stack: dict) -> str:
    if it.severity:
        return it.severity
    if stack_matches(it, stack):
        return "high"
    if it.source_type in ("github", "cloud", "social"):
        return "medium"
    return "low"


def dedupe(items: list[Item]) -> list[Item]:
    by_id: dict[str, Item] = {}
    for it in items:
        cur = by_id.get(it.id)
        if cur is None or len(it.summary) > len(cur.summary):
            by_id[it.id] = it
    return list(by_id.values())


def _rank_key(it: Item):
    return (IMPORTANCE_ORDER[it.importance], it.published)


def rank_and_truncate(items: list[Item], categories: list[str], max_per: int) -> list[Item]:
    out: list[Item] = []
    for cat in categories:
        group = [it for it in items if it.category == cat]
        group.sort(key=_rank_key, reverse=True)
        out.extend(group[:max_per])
    # include items in unknown categories, untruncated, appended after known ones
    known = set(categories)
    out.extend(it for it in items if it.category not in known)
    return out


def run_fetch(cfg, snapshot_path: Path, *, now: datetime, client,
              force: bool = False, fresh: bool = False) -> dict:
    date = now.date().isoformat()
    snap = new_snapshot(date) if fresh else (load_snapshot(snapshot_path) or new_snapshot(date))
    items = [item_from_dict(d) for d in snap["items"]]
    by_id = {it.id: it for it in items}

    for i, source in enumerate(cfg.sources):
        name = source.get("repo") or source.get("url") or source.get("feed") or \
            source.get("source") or f"source{i}"
        prev = snap["meta"]["sources"].get(name, {})
        if prev.get("status") == "ok" and not force:
            continue
        adapter = ADAPTERS[source["type"]]
        try:
            fetched = adapter.fetch(source, cfg, client=client, now=now)
            for it in fetched:
                by_id[it.id] = it
            snap["meta"]["sources"][name] = {"status": "ok", "count": len(fetched)}
        except Exception as e:  # per-source isolation
            log.warning("source %s failed: %s", name, e)
            snap["meta"]["sources"][name] = {"status": "failed", "error": str(e)[:200]}
        snap["items"] = [item_to_dict(it) for it in by_id.values()]
        atomic_write_json(snapshot_path, snap)  # checkpoint

    # finalize: importance, lookback, hard-cut, rank
    from dataclasses import replace
    lookback = int(cfg.general.get("lookback_days", 7))
    min_keep = cfg.general.get("min_keep_importance", "medium")
    max_per = int(cfg.general.get("max_items_per_category", 15))

    scored = []
    for it in by_id.values():
        it = replace(it, importance=score_importance(it, cfg.stack),
                     stack_match=stack_matches(it, cfg.stack))
        if within_lookback(it.published, now, lookback) and importance_ge(it.importance, min_keep):
            scored.append(it)
    dropped = len(by_id) - len(scored)
    final = rank_and_truncate(dedupe(scored), cfg.categories, max_per)
    snap["items"] = [item_to_dict(it) for it in final]
    atomic_write_json(snapshot_path, snap)
    log.info("fetched %d items from %d sources, kept %d, dropped %d",
             len(by_id), len(cfg.sources), len(final), dropped)
    return snap
