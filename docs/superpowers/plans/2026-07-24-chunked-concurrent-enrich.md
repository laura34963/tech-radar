# Chunked + Concurrent LLM Enrichment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bound each LLM enrich call by a fixed item count and run chunks concurrently in a bounded thread pool, so large per-category batches can no longer hit the provider timeout.

**Architecture:** Split each category's eligible items into fixed-size chunks, flatten into a work-list, run each chunk's `provider.complete()` in a `ThreadPoolExecutor`. Workers are pure (prompt → complete → parse → dedupe, no shared state, no I/O). The main thread merges each completed chunk's fields into `by_id` and atomically checkpoints the snapshot, keeping all disk writes single-threaded. A chunk that times out or errors is logged and skipped; its items keep rule-based content.

**Tech Stack:** Python 3.13, stdlib `concurrent.futures`, `httpx` (thread-safe), `pytest`.

## Global Constraints

- Two new `[llm]` config keys, read via `cfg.llm.get(...)`: `enrich_chunk_size` (default `8`), `enrich_concurrency` (default `4`). No `config.py` change — `llm` is a passthrough dict.
- Both values clamped to `>= 1` at use: `max(1, int(...))`. Zero/negative falls back to `1`.
- Existing configs without the new keys must work unchanged.
- All disk I/O (`atomic_write_json`) happens only on the main thread. Workers do no I/O.
- Failure handling is skip-immediately (no retry): a failed chunk logs a warning and leaves its items unenriched.
- `max_items_to_enrich` (default 40) is unchanged and out of scope.
- Preserve existing behavior: only `importance >= high` items, skip already-`llm` items unless `force`, one category per prompt.

---

### Task 1: `_chunk` list-splitting helper

**Files:**
- Modify: `radar/pipeline/enrich.py` (add helper near top, after `_dedupe_fields`)
- Test: `tests/test_enrich.py`

**Interfaces:**
- Produces: `_chunk(items: list, size: int) -> list[list]` — splits `items` into consecutive sublists of at most `size`; the final sublist may be shorter; every element appears exactly once; empty input → `[]`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_enrich.py`:

```python
from radar.pipeline.enrich import _chunk


def test_chunk_splits_into_bounded_groups():
    assert _chunk(list(range(20)), 8) == [
        [0, 1, 2, 3, 4, 5, 6, 7],
        [8, 9, 10, 11, 12, 13, 14, 15],
        [16, 17, 18, 19],
    ]


def test_chunk_covers_every_item_once():
    items = list(range(23))
    chunks = _chunk(items, 5)
    assert [x for c in chunks for x in c] == items  # order preserved, no dupes


def test_chunk_empty_is_empty():
    assert _chunk([], 8) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_enrich.py::test_chunk_splits_into_bounded_groups -v`
Expected: FAIL with `ImportError: cannot import name '_chunk'`

- [ ] **Step 3: Write minimal implementation**

Add to `radar/pipeline/enrich.py` after `_dedupe_fields` (after line 45):

```python
def _chunk(items: list, size: int) -> list[list]:
    """Split items into consecutive sublists of at most `size`. The last sublist
    may be shorter. Every element appears exactly once; empty input -> []."""
    return [items[i:i + size] for i in range(0, len(items), size)]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_enrich.py -k chunk -v`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add radar/pipeline/enrich.py tests/test_enrich.py
git commit -m "feat: add _chunk helper for bounding enrich batch size"
```

---

### Task 2: `_enrich_chunk` pure worker

**Files:**
- Modify: `radar/pipeline/enrich.py` (add worker after `_chunk`)
- Test: `tests/test_enrich.py`

**Interfaces:**
- Consumes: `build_batch_prompt`, `parse_enrich_response`, `_dedupe_fields` (existing, `enrich.py:14,22,30`).
- Produces: `_enrich_chunk(cat: str, chunk_items: list[dict], stack: dict, provider) -> dict[str, dict]` — returns `{item_id: deduped_fields}` only for ids present in both the LLM response and `chunk_items`. Uses each chunk item's own `summary` as the summary fallback. Raises on provider error or unparseable response (caller catches).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_enrich.py`:

```python
from radar.pipeline.enrich import _enrich_chunk


def test_enrich_chunk_returns_deduped_fields_for_known_ids():
    chunk = [{"id": "1", "title": "t", "summary": "orig"}]
    payload = json.dumps({
        "1": {"summary": "S", "detail": "D", "why_it_matters": "W",
              "recommended_action": "A"},
        "999": {"summary": "ignored"},  # not in chunk -> dropped
    })
    out = _enrich_chunk("backend", chunk, {}, FakeProvider(payload))
    assert set(out) == {"1"}
    assert out["1"] == {"summary": "S", "detail": "D",
                        "why_it_matters": "W", "recommended_action": "A"}


def test_enrich_chunk_falls_back_to_item_summary():
    chunk = [{"id": "1", "title": "t", "summary": "orig"}]
    payload = json.dumps({"1": {"detail": "D"}})  # no summary in response
    out = _enrich_chunk("backend", chunk, {}, FakeProvider(payload))
    assert out["1"]["summary"] == "orig"


def test_enrich_chunk_raises_on_provider_error():
    chunk = [{"id": "1", "title": "t", "summary": "orig"}]
    import pytest
    with pytest.raises(RuntimeError):
        _enrich_chunk("backend", chunk, {}, FakeProvider("", fail=True))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_enrich.py -k enrich_chunk -v`
Expected: FAIL with `ImportError: cannot import name '_enrich_chunk'`

- [ ] **Step 3: Write minimal implementation**

Add to `radar/pipeline/enrich.py` after `_chunk`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_enrich.py -k enrich_chunk -v`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add radar/pipeline/enrich.py tests/test_enrich.py
git commit -m "feat: add _enrich_chunk pure worker for one bounded batch"
```

---

### Task 3: Concurrent orchestration in `run_enrich`

**Files:**
- Modify: `radar/pipeline/enrich.py:71-89` (replace the per-category loop), `radar/pipeline/enrich.py:1-7` (imports)
- Test: `tests/test_enrich.py`
- Docs: `config/radar.toml` (document the two new keys near the existing `[llm]` block)

**Interfaces:**
- Consumes: `_chunk` (Task 1), `_enrich_chunk` (Task 2), `atomic_write_json`, `load_snapshot` (`radar/store.py`), `importance_ge` (`radar/pipeline/fetch.py`).
- Produces: `run_enrich(cfg, snapshot_path, *, provider, force=False) -> dict` — unchanged signature; now chunks eligible items (`enrich_chunk_size`), runs chunks concurrently (`enrich_concurrency`), merges each completed chunk into `by_id["...]["llm"]`, checkpoints the snapshot after each completion, and marks `snap["meta"]["enriched"][cat] = True` when a chunk of that category succeeds. Failed chunks are logged and skipped.

- [ ] **Step 1: Write the failing tests**

Add a per-item test provider and three tests to `tests/test_enrich.py`:

```python
import re


class PerItemProvider:
    """Returns a summary for each id it sees in the prompt (`id=<id>`). Optionally
    raises when a given id appears, to simulate a single failing chunk."""
    def __init__(self, fail_id=None):
        self.fail_id, self.calls = fail_id, 0

    def complete(self, system, user):
        self.calls += 1
        ids = re.findall(r"id=(\S+)", user)
        if self.fail_id and self.fail_id in ids:
            raise RuntimeError("boom")
        return json.dumps({i: {"summary": f"S-{i}"} for i in ids})


def _cfg_chunked(chunk_size, concurrency):
    return Config(general={}, stack={"packages": ["rails"]},
                  categories=["backend"], sources=[],
                  llm={"enabled": True, "max_items_to_enrich": 40,
                       "enrich_chunk_size": chunk_size,
                       "enrich_concurrency": concurrency})


def test_run_enrich_chunks_and_merges_all_items(tmp_path):
    p = tmp_path / "s.json"
    atomic_write_json(p, _snap([_high(str(i)) for i in range(20)]))
    fp = PerItemProvider()
    run_enrich(_cfg_chunked(8, 4), p, provider=fp)
    saved = load_snapshot(p)
    assert fp.calls == 3  # 20 items / chunk 8 -> 3 chunks
    for i in range(20):
        assert saved["items"][i]["llm"]["summary"] == f"S-{i}"


def test_run_enrich_isolates_failed_chunk(tmp_path):
    p = tmp_path / "s.json"
    atomic_write_json(p, _snap([_high(str(i)) for i in range(20)]))
    # chunk 8 -> ids 0-7 / 8-15 / 16-19; fail the chunk containing id "10"
    run_enrich(_cfg_chunked(8, 4), p, provider=PerItemProvider(fail_id="10"))
    saved = {it["id"]: it for it in load_snapshot(p)["items"]}
    assert saved["10"].get("llm") is None       # its whole chunk skipped
    assert saved["8"].get("llm") is None         # same failed chunk
    assert saved["0"]["llm"]["summary"] == "S-0"  # other chunks enriched
    assert saved["19"]["llm"]["summary"] == "S-19"


def test_run_enrich_clamps_zero_chunk_size(tmp_path):
    p = tmp_path / "s.json"
    atomic_write_json(p, _snap([_high(str(i)) for i in range(3)]))
    fp = PerItemProvider()
    run_enrich(_cfg_chunked(0, 0), p, provider=fp)  # 0 -> clamp to 1
    assert fp.calls == 3  # one item per chunk
    assert load_snapshot(p)["items"][0]["llm"]["summary"] == "S-0"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_enrich.py -k "chunks_and_merges or isolates or clamps" -v`
Expected: FAIL (e.g. `fp.calls == 1`, not 3, and item summaries wrong) because `run_enrich` still uses the single-call-per-category loop.

- [ ] **Step 3: Add imports**

Change `radar/pipeline/enrich.py` top imports (lines 1-7) to add `concurrent.futures`:

```python
from __future__ import annotations
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from radar.pipeline.fetch import importance_ge
from radar.store import load_snapshot, atomic_write_json
```

- [ ] **Step 4: Replace the per-category loop**

Replace `radar/pipeline/enrich.py:69-89` (from the `log.info("enrich: %d item(s)...")` line through `return snap`) with:

```python
    chunk_size = max(1, int(cfg.llm.get("enrich_chunk_size", 8)))
    concurrency = max(1, int(cfg.llm.get("enrich_concurrency", 4)))

    tasks = [(cat, chunk)
             for cat, cat_items in by_cat.items()
             for chunk in _chunk(cat_items, chunk_size)]

    log.info("enrich: %d item(s) in %d chunk(s) across %d categor(ies), concurrency=%d",
             len(eligible), len(tasks), len(by_cat), concurrency)

    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        futures = {ex.submit(_enrich_chunk, cat, chunk, cfg.stack, provider): (cat, len(chunk))
                   for cat, chunk in tasks}
        for n, fut in enumerate(as_completed(futures), 1):
            cat, size = futures[fut]
            try:
                for iid, fields in fut.result().items():
                    if iid in by_id:
                        by_id[iid]["llm"] = fields
                snap["meta"].setdefault("enriched", {})[cat] = True
                log.info("  [%d/%d] %s: enriched (%d item(s))", n, len(tasks), cat, size)
            except Exception as e:
                log.warning("  [%d/%d] %s: chunk failed (kept rule-based, %d item(s)) — %s",
                            n, len(tasks), cat, size, e)
            atomic_write_json(snapshot_path, snap)  # checkpoint per completed chunk
    return snap
```

- [ ] **Step 5: Run the new and existing tests**

Run: `.venv/bin/pytest tests/test_enrich.py -v`
Expected: all PASS (the three new tests plus the seven pre-existing enrich tests).

- [ ] **Step 6: Run the full suite to check for regressions**

Run: `.venv/bin/pytest -q`
Expected: full suite PASS (no regressions in `test_cli.py`, `test_render.py`, etc.).

- [ ] **Step 7: Document the new config keys**

In `config/radar.toml`, near the existing `[llm]` settings (around the `max_items_to_enrich` / provider block, ~line 273), add:

```toml
# Enrichment batching: cap items per LLM call so large categories can't hit the
# provider timeout, and run chunks concurrently.
enrich_chunk_size  = 8   # max items packed into one LLM call
enrich_concurrency = 4   # max concurrent LLM calls
```

- [ ] **Step 8: Commit**

```bash
git add radar/pipeline/enrich.py tests/test_enrich.py config/radar.toml
git commit -m "feat: chunk enrich batches and run them concurrently

Cap items per LLM call at enrich_chunk_size (default 8) and run chunks
in a bounded thread pool (enrich_concurrency, default 4). Workers are
pure; the main thread merges results and checkpoints per completed
chunk. A failed chunk is logged and skipped, matching prior degrade
behavior at chunk granularity."
```

---

## Self-Review

**Spec coverage:**
- Config keys `enrich_chunk_size` / `enrich_concurrency` with defaults + clamping → Task 3 Steps 4, 7; clamping test Task 3 Step 1 (`test_run_enrich_clamps_zero_chunk_size`).
- Flat work-list of `(category, chunk)` preserving category-per-prompt → Task 1 (`_chunk`) + Task 3 Step 4 (`tasks` comprehension).
- Bounded thread pool, pure workers, no worker I/O → Task 2 (`_enrich_chunk`) + Task 3 Step 4 (`ThreadPoolExecutor`).
- Merge + checkpoint on main thread via `as_completed` → Task 3 Step 4; merge test `test_run_enrich_chunks_and_merges_all_items`.
- Skip-on-failure, no retry, warning logged → Task 3 Step 4 `except`; isolation test `test_run_enrich_isolates_failed_chunk`.
- `max_items_to_enrich` unchanged → not touched; `cap` logic at `enrich.py:55-60` left intact.
- All spec test cases (chunking, concurrency merge, failure isolation, checkpoint, config clamping) → covered across Tasks 1-3.

**Placeholder scan:** No TBD/TODO/"add error handling"; every code and test step shows full content.

**Type consistency:** `_chunk(items, size) -> list[list]`, `_enrich_chunk(cat, chunk_items, stack, provider) -> dict[str, dict]` used identically in Task 3. `run_enrich` signature unchanged. `provider.complete(system, user)` matches existing `enrich.py:75` and both test providers.

Note: `snap["meta"]["enriched"][cat]` now means "at least one chunk of this category succeeded" (partial), versus the old "the whole category succeeded". This flag is informational only (used by no other read path in enrich); the behavior change is intentional and acceptable.
