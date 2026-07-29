from __future__ import annotations
import logging
from collections import Counter
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from radar.item import Item, IMPORTANCE_ORDER
from radar.match import (apply_source_fairness, category_matches, exclusion_hit,
                         relevance_key, score_importance, stack_matches)
from radar.adapters import ADAPTERS
from radar.store import (new_snapshot, load_snapshot, atomic_write_json,
                         item_to_dict, item_from_dict)

log = logging.getLogger("radar.fetch")


def importance_ge(a: str, b: str) -> bool:
    return IMPORTANCE_ORDER[a] >= IMPORTANCE_ORDER[b]


def within_lookback(published: datetime, now: datetime, days: int) -> bool:
    return published >= now - timedelta(days=days)


def dedupe(items: list[Item]) -> list[Item]:
    by_id: dict[str, Item] = {}
    for it in items:
        cur = by_id.get(it.id)
        if cur is None or len(it.summary) > len(cur.summary):
            by_id[it.id] = it
    return list(by_id.values())


def _rank_key(it: Item):
    """One notion of rank across the pipeline; see match.relevance_key."""
    return relevance_key(it)


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


def _source_name(source: dict, i: int) -> str:
    """Stable, unique resume/label key for a source. repo/url/feed are already
    unique, but 'social' (source='hn'/'reddit') and 'registry' (registry='npm'…)
    repeat across categories; keyed by that field alone they collapse onto one
    entry and all but the first get skipped as 'cached'. Append the field that
    actually distinguishes them (query / subreddit / packages)."""
    base = (source.get("repo") or source.get("url") or source.get("feed")
            or source.get("source") or source.get("registry") or f"source{i}")
    disc = source.get("query") or source.get("subreddit") \
        or ",".join(source.get("packages", []))
    return f"{base}:{source.get('category', '')}:{disc}" if disc else base


def run_fetch(cfg, snapshot_path: Path, *, now: datetime, client,
              force: bool = False, fresh: bool = False) -> dict:
    date = now.date().isoformat()
    snap = new_snapshot(date) if fresh else (load_snapshot(snapshot_path) or new_snapshot(date))
    items = [item_from_dict(d) for d in snap["items"]]
    by_id = {it.id: it for it in items}

    total = len(cfg.sources)
    log.info("fetch: %d source(s)", total)
    for i, source in enumerate(cfg.sources):
        name = _source_name(source, i)
        prev = snap["meta"]["sources"].get(name, {})
        if prev.get("status") == "ok" and not force:
            log.info("  [%d/%d] %s (%s): cached, skipping", i + 1, total, name, source["type"])
            continue
        log.info("  [%d/%d] %s (%s): fetching…", i + 1, total, name, source["type"])
        try:
            adapter = ADAPTERS[source["type"]]
            fetched = adapter.fetch(source, cfg, client=client, now=now)
            board = source.get("board")
            for it in fetched:
                if board is not None:
                    it = replace(it, board=board)
                cur = by_id.get(it.id)
                if cur is None or len(it.summary) > len(cur.summary):
                    by_id[it.id] = it
            snap["meta"]["sources"][name] = {"status": "ok", "count": len(fetched)}
            log.info("  [%d/%d] %s: %d item(s)", i + 1, total, name, len(fetched))
        except Exception as e:  # per-source isolation (includes unknown adapter type)
            log.warning("  [%d/%d] %s: FAILED — %s", i + 1, total, name, e)
            snap["meta"]["sources"][name] = {"status": "failed", "error": str(e)[:200]}
        snap["items"] = [item_to_dict(it) for it in by_id.values()]
        atomic_write_json(snapshot_path, snap)  # checkpoint

    # finalize: exclude -> score -> lookback + min_keep -> dedupe -> fairness -> truncate
    lookback = int(cfg.general.get("lookback_days", 7))
    min_keep = cfg.general.get("min_keep_importance", "medium")
    max_per = int(cfg.general.get("max_items_per_category", 15))
    budget = max(0, int(cfg.general.get("max_card_items", 30)))
    floor = max(0, int(cfg.general.get("per_source_limit", 3)))

    excluded: Counter = Counter()
    stale = below_keep = 0
    scored: list[Item] = []
    for it in by_id.values():
        term = exclusion_hit(it, cfg.exclude)
        if term is not None:
            excluded[term] += 1
            continue  # excluded items are never scored
        it = replace(it,
                     importance=score_importance(it, cfg.stack, cfg.category_keywords),
                     stack_match=stack_matches(it, cfg.stack),
                     keyword_match=category_matches(it, cfg.category_keywords),
                     demoted=None)
        if not within_lookback(it.published, now, lookback):
            stale += 1
            continue
        if not importance_ge(it.importance, min_keep):
            below_keep += 1
            continue
        scored.append(it)

    if excluded:
        log.info("excluded %d item(s) by keyword; most frequent: %s",
                 sum(excluded.values()), excluded.most_common(5))

    # by_id already merges richest-per-id across sources; dedupe() here is a
    # defensive safety net (not dead code) in case future callers feed it items
    # that weren't merged through the by_id path. It also guarantees the unique
    # ids apply_source_fairness relies on.
    fair, demoted = apply_source_fairness(dedupe(scored), budget, floor)
    if demoted:
        log.info("source fairness: demoted %d item(s) to also-noted across %d source(s): %s",
                 sum(demoted.values()), len(demoted),
                 sorted(demoted.items(), key=lambda kv: (-kv[1], kv[0])))
    else:
        log.info("source fairness: card tier within budget (%d), nothing demoted", budget)

    final = rank_and_truncate(fair, cfg.categories, max_per)
    snap["items"] = [item_to_dict(it) for it in final]
    atomic_write_json(snapshot_path, snap)
    log.info("fetched %d item(s) from %d source(s), kept %d "
             "(dropped %d excluded, %d outside lookback, %d below %s)",
             len(by_id), len(cfg.sources), len(final),
             sum(excluded.values()), stale, below_keep, min_keep)
    return snap
