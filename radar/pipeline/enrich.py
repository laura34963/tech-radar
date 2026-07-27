from __future__ import annotations
import json
import logging
import re
from pathlib import Path
from radar.pipeline.fetch import importance_ge
from radar.store import load_snapshot, atomic_write_json

log = logging.getLogger("radar.enrich")
_PROMPT = (Path(__file__).resolve().parent.parent / "llm" / "prompts" / "enrich.txt").read_text()
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def build_batch_prompt(category: str, items: list[dict], stack: dict) -> tuple[str, str]:
    system = f"Technical digest writer for category: {category}."
    listing = "\n".join(
        f'- id={it["id"]} | {it["title"]} | {it["summary"]}' for it in items)
    user = _PROMPT.replace("{stack}", json.dumps(stack)).replace("{items}", listing)
    return system, user


def parse_enrich_response(text: str) -> dict:
    cleaned = _FENCE.sub("", text).strip()
    data = json.loads(cleaned)
    if not isinstance(data, dict):
        raise ValueError("expected JSON object")
    return data


def _dedupe_fields(fields: dict) -> dict:
    """Blank any enriched field whose text merely repeats an earlier one, so the
    digest never shows the same content twice within a card. Priority order:
    summary > detail > why_it_matters > recommended_action (the first occurrence
    wins, later duplicates become "")."""
    seen: set[str] = set()
    out: dict[str, str] = {}
    for key in ("summary", "detail", "why_it_matters", "recommended_action"):
        val = (fields.get(key) or "").strip()
        norm = re.sub(r"\s+", " ", val).casefold()
        if norm and norm in seen:
            val = ""
        elif norm:
            seen.add(norm)
        out[key] = val
    return out


def _chunk(items: list, size: int) -> list[list]:
    """Split items into consecutive sublists of at most `size`. The last sublist
    may be shorter. Every element appears exactly once; empty input -> []."""
    return [items[i:i + size] for i in range(0, len(items), size)]


def _enrich_chunk(cat: str, chunk_items: list[dict], stack: dict, provider) -> dict[str, dict]:
    """Enrich one bounded chunk: build the prompt, call the provider, parse, and
    dedupe. Pure and stateless — no shared state, no disk I/O — so it is safe to
    run in a thread pool. Returns {item_id: deduped_fields} for ids present in
    both the response and this chunk. Raises on provider or parse error."""
    system, user = build_batch_prompt(cat, chunk_items, stack)
    result = parse_enrich_response(provider.complete(system, user))
    fallback = {it["id"]: it["summary"] for it in chunk_items}
    out: dict[str, dict] = {}
    for iid, fields in result.items():
        if iid in fallback and isinstance(fields, dict):
            out[iid] = _dedupe_fields({
                "summary": fields.get("summary") or fallback[iid],
                "detail": fields.get("detail") or "",
                "why_it_matters": fields.get("why_it_matters") or "",
                "recommended_action": fields.get("recommended_action") or "",
            })
    return out


def run_enrich(cfg, snapshot_path: Path, *, provider, force: bool = False) -> dict:
    snap = load_snapshot(snapshot_path)
    if not snap or "meta" not in snap:
        log.info("no snapshot at %s; run fetch first", snapshot_path)
        return snap
    if provider is None:
        return snap
    cap = int(cfg.llm.get("max_items_to_enrich", 40))
    items = snap["items"]
    by_id = {it["id"]: it for it in items}

    eligible = [it for it in items if importance_ge(it["importance"], "high")
                and (force or not it.get("llm"))][:cap]
    if not eligible:
        log.info("enrich: nothing to do (no high/critical items pending)")
        return snap

    by_cat: dict[str, list[dict]] = {}
    for it in eligible:
        by_cat.setdefault(it["category"], []).append(it)

    log.info("enrich: %d item(s) across %d categor(ies) via LLM",
             len(eligible), len(by_cat))
    for n, (cat, cat_items) in enumerate(by_cat.items(), 1):
        log.info("  [%d/%d] %s (%d item(s))…", n, len(by_cat), cat, len(cat_items))
        system, user = build_batch_prompt(cat, cat_items, cfg.stack)
        try:
            result = parse_enrich_response(provider.complete(system, user))
            for iid, fields in result.items():
                if iid in by_id and isinstance(fields, dict):
                    by_id[iid]["llm"] = _dedupe_fields({
                        "summary": fields.get("summary") or by_id[iid]["summary"],
                        "detail": fields.get("detail") or "",
                        "why_it_matters": fields.get("why_it_matters") or "",
                        "recommended_action": fields.get("recommended_action") or "",
                    })
            snap["meta"].setdefault("enriched", {})[cat] = True
            log.info("  [%d/%d] %s: enriched", n, len(by_cat), cat)
        except Exception as e:
            log.warning("  [%d/%d] %s: enrich failed (kept rule-based) — %s", n, len(by_cat), cat, e)
        atomic_write_json(snapshot_path, snap)  # checkpoint per category
    return snap
