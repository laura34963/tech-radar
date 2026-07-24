# Chunked + concurrent LLM enrichment

**Date:** 2026-07-24
**Status:** Approved, pending implementation

## Problem

When a category has many high/critical items, the enrich stage packs *all* of them
into a single LLM call. That one prompt (and its large JSON response) grows past the
provider timeout — `command_timeout = 120s` for the active `cli` provider — and the
call fails. The failure is caught per-category (`radar/pipeline/enrich.py:86`), so the
whole category silently falls back to rule-based content.

Root cause: **per-category batch size is unbounded.** More items in a category → larger
prompt/response → longer generation → timeout.

## Goal

- **Bound each call** so it can never grow large enough to time out, regardless of how
  many items a category has.
- **Run calls concurrently** so the now-more-numerous (but smaller) calls don't make the
  total run slower.

Reliability and speed together.

## Current architecture (for reference)

`radar/pipeline/enrich.py` — `run_enrich(cfg, snapshot_path, *, provider, force=False)`:

1. Load snapshot; select eligible items: `importance >= high`, not already enriched
   (unless `force`), capped at `max_items_to_enrich` (default 40).
2. Group eligible items by category (`by_cat`).
3. For each category: `build_batch_prompt` → `provider.complete(system, user)` →
   `parse_enrich_response` → `_dedupe_fields`; write fields into `by_id[iid]["llm"]`;
   atomically checkpoint the snapshot to disk after each category.

Fully synchronous, one category at a time. No chunking, concurrency, retry, or rate
limiting. `httpx.Client` (HTTP providers) and `subprocess.run` (CLI provider) are both
thread-safe.

## Design

### 1. Config — two new keys under `[llm]`

```toml
enrich_chunk_size  = 8   # max items packed into one LLM call
enrich_concurrency = 4   # max calls running at once
```

- Read via `cfg.llm.get(...)` with the defaults above.
- Validated/clamped to `>= 1` at use: a zero or negative value falls back to `1`.
- Existing configs without these keys work unchanged (defaults apply).

### 2. Build a flat work-list of chunks

Keep the existing category grouping — one category per prompt preserves prompt
semantics. Split each category's item list into consecutive chunks of at most
`enrich_chunk_size`. Flatten across all categories into one list of tasks:

```
tasks = [(category, chunk_items), ...]
```

A category with 20 items and `enrich_chunk_size = 8` → chunks of [8, 8, 4] → 3 tasks.
Every eligible item appears in exactly one chunk; none duplicated.

### 3. Run chunks in a bounded thread pool

`concurrent.futures.ThreadPoolExecutor(max_workers=enrich_concurrency)`.

Each worker performs only the **pure, stateless** part of the pipeline and returns the
parsed result:

```
build_batch_prompt(category, chunk_items, stack)
  -> provider.complete(system, user)
  -> parse_enrich_response(text)
  -> _dedupe_fields(fields)
  -> return fields   (dict keyed by item id)
```

No shared mutable state and no disk I/O inside workers.

### 4. Merge + checkpoint on the main thread

Iterate `concurrent.futures.as_completed(...)`. For each completed future, the **main
thread** merges the returned fields into `by_id[iid]["llm"]` and performs the atomic
snapshot write. All disk I/O stays single-threaded (no concurrent-write races), and the
current "partial progress survives a crash" guarantee is preserved — now at chunk
granularity instead of category granularity.

### 5. Failure handling — skip immediately (no retry)

A worker that times out or raises has its exception surfaced when the future is read.
The main thread catches it, logs a warning identifying the category and chunk, and moves
on — those items keep their rule-based content. One bad chunk never fails the run or
blocks other chunks. This matches today's behavior, just per-chunk instead of
per-category.

## Why this fixes the timeout

The timeout came from unbounded per-category prompts. Capping each call at
`enrich_chunk_size` items means no single call can grow large enough to hit the timeout,
no matter how many items a category has. Concurrency keeps the increased call count from
slowing the overall run.

## Out of scope / flagged

- **`max_items_to_enrich` (default 40) is unchanged.** It caps *how many* items get
  enriched in total; this change alters *how* those items are processed, not the total.
  If the real constraint is that 40 is too few (or that raising it is what triggers the
  timeouts), that is a separate knob to revisit later.
- No retry/backoff logic (explicitly decided: skip immediately).
- Provider timeouts remain as they are; this design removes the need to raise them.

## Testing (deterministic, mocked provider)

- **Chunking:** 20 items with `enrich_chunk_size = 8` → exactly 3 chunks; every item
  covered once, none duplicated.
- **Concurrency merge:** all chunk results merged into `by_id` correctly regardless of
  future completion order (use a mock provider that returns per-chunk fields).
- **Failure isolation:** one chunk's provider raises → its items stay unenriched, all
  other items are still enriched, and the run completes without error.
- **Checkpoint:** snapshot is written and re-readable after completions (partial progress
  persists).
- **Config clamping:** `enrich_chunk_size = 0` (and `enrich_concurrency = 0`) are treated
  as `1`.