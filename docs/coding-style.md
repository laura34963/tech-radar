# Coding Style

> **Type:** Reference / How-to
> **Audience:** Developers, AI assistants, and any tooling that needs project context
> **Last updated:** 2026-07-17
>
> Conventions, idioms, and layering rules that new code in tech-radar must follow.
>
> **Status — forward-looking:** the `tech-radar/` code does not yet exist. These
> conventions are the ones the implementation plan builds to; treat them as the target and
> re-verify against real files once code lands. Examples are drawn from the plan's task code.
>
> Related docs:
> - [`project-overview.md`](project-overview.md) — module layout the layering rules refer to
> - [`domain-models.md`](domain-models.md) — the `Item`/snapshot/config models
> - [`ai-development-guide.md`](ai-development-guide.md) — safe-change guardrails (prescriptive)

**Terminology:** This document uses RFC 2119 keywords — **MUST** (mandatory), **SHOULD** (recommended), **MAY** (optional).

---

<a id="1-language-and-runtime"></a>

## 1. Language & runtime conventions

| Rule | Rationale |
|---|---|
| Target Python **3.11+**; `from __future__ import annotations` at the top of each module | `tomllib` is 3.11+; deferred annotations keep `str \| None` hints cheap |
| Type-hint every public function signature | The pipeline passes typed structures between stages; hints are the contract |
| Model data as `@dataclass` (`Item`, `Config`); make value objects `frozen=True` | Immutable domain objects prevent accidental mutation across stages |
| Prefer stdlib over new dependencies | Runtime deps are capped at `feedparser` + `httpx` + `Jinja2` by spec |
| Times are **timezone-aware UTC** internally | Avoids naive/aware comparison bugs in lookback/ranking |

---

<a id="2-purity-and-time"></a>

## 2. Purity & injected time

Pure logic (scoring, dedupe, ranking, filtering) **MUST NOT** read the wall clock,
filesystem, or network. The "current time" is injected as `now` from the CLI boundary and
threaded through.

```python
# Bad — pure logic reaches for the clock, breaking determinism + tests
def within_lookback(published, days):
    return published >= datetime.now(timezone.utc) - timedelta(days=days)

# Good — now is injected by the caller (CLI passes datetime.now once, at the edge)
def within_lookback(published: datetime, now: datetime, days: int) -> bool:
    return published >= now - timedelta(days=days)
```

Only `radar.py` (the CLI entrypoint) **MAY** call `datetime.now(timezone.utc)`, once, and
pass it inward.

---

<a id="3-layering-rules"></a>

## 3. Layering rules

The pipeline is ports-and-adapters. Each layer has one responsibility:

| Layer | MUST contain | MUST NOT contain |
|---|---|---|
| **Adapters** (`radar/adapters/*`) | Outbound I/O + normalization to `Item` | Dedupe/rank/importance/filter logic |
| **Fetch** (`pipeline/fetch.py`) | Dedupe, scoring, ranking, filtering, per-source resumability | HTTP request shapes (delegate to adapters) |
| **Enrich** (`pipeline/enrich.py`) | Batch/prompt/parse LLM, checkpointing | Provider-specific HTTP (delegate to `llm/provider.py`) |
| **Render** (`pipeline/render.py`) | Snapshot → HTML, index, nav, hashing | Business rules about what to keep |
| **Store** (`store.py`) | Atomic IO + (de)serialization | Any domain decision |
| **CLI** (`radar.py`) | Arg parsing, wiring, the single `now` | Any business logic |

**Frozen dataclasses are modified by copy, never in place:**

```python
# Bad — mutating a frozen Item's list field in place
item.tags.append(service)

# Good — produce a new Item
from dataclasses import replace
item = replace(item, tags=[*item.tags, service])
```

**New source types** are added by writing an adapter that implements `fetch(source, cfg,
*, client, now) -> list[Item]` and registering it in `ADAPTERS`. Do **NOT** add dynamic
plugin discovery — the spec forbids it as YAGNI.

---

<a id="4-error-handling-and-logging"></a>

## 4. Error handling & logging

Distinguish three failure classes (per the spec's error philosophy):

| Class | Handling |
|---|---|
| **Config errors** (invalid TOML, unknown source type, missing field) | **Fail fast, loudly** — raise `ConfigError`, print to stderr, exit code `2` |
| **Runtime source errors** (dead feed, 403, timeout) | **Degrade** — log WARN, mark the source `failed`, continue; run still produces a digest |
| **LLM errors** (timeout, unparseable JSON) | **Degrade** — item keeps its rule-based fields; WARN; run continues |

```python
# Good — per-source isolation; one bad feed never aborts the run
try:
    fetched = adapter.fetch(source, cfg, client=client, now=now)
    snap["meta"]["sources"][name] = {"status": "ok", "count": len(fetched)}
except Exception as e:            # broad catch is intentional at this boundary
    log.warning("source %s failed: %s", name, e)
    snap["meta"]["sources"][name] = {"status": "failed", "error": str(e)[:200]}
```

**Exit codes:** `0` on success (including partial-with-warnings); non-zero only for config
errors (`2`) or total failure. So CI surfaces real breakage but not one flaky feed.

**Logging:** structured, level-tagged, to **stderr**. Emit a summary line at the end
(counts of fetched/kept/dropped/enriched). **MUST NOT** log secrets, full tokens, or
request/response bodies that could contain them.

**All file writes go through `store.atomic_write_*`** (`*.tmp` + `os.replace`) — a crash
must never leave a half-written snapshot or HTML page.

---

<a id="5-html-safety"></a>

## 5. HTML safety

Feed content and LLM output are **untrusted** and rendered into a published page.

- Jinja2 **MUST** run with autoescape enabled; templates **MUST NOT** use `| safe` on
  item-derived text.
- Build HTML only through templates — never string-concatenate item fields into markup.
- `parse_feed` strips tags as defense-in-depth, but autoescape is the primary control.

---

<a id="6-testing-conventions"></a>

## 6. Testing conventions

| Rule | Detail |
|---|---|
| **TDD** | Write the failing test first, watch it fail, implement minimally, watch it pass, commit — one task per commit (see the plan) |
| **No live network** | Mock with `httpx.MockTransport` / `respx`; load payloads from `tests/fixtures/` |
| **Determinism** | Inject `now`; never depend on the real clock, randomness, or the real FS outside `tmp_path` |
| **Test behavior, not prose** | For LLM output, assert fields are populated and HTML-escaped — never assert exact wording |
| **Cover the degrade paths** | Failing source isolated; LLM failure leaves rule-based fields; atomic write leaves no `*.tmp` |
| **Run** | `cd tech-radar && python -m pytest -q` |

```python
# Good — offline adapter test against a saved fixture
def test_hn_applies_min_points():
    client = httpx.Client(transport=httpx.MockTransport(
        lambda req: httpx.Response(200, text=FIX)))
    items = SocialAdapter().fetch(
        {"source": "hn", "category": "frontend", "query": "react", "min_points": 100},
        None, client=client, now=NOW)
    assert len(items) == 1
```

---

<a id="7-naming-and-files"></a>

## 7. Naming & file conventions

- One responsibility per module; a stage's logic, not a technical-layer grab-bag.
- Function names describe intent: `score_importance`, `within_lookback`, `rank_and_truncate`
  — not `process`, `handle`, or `do_it`.
- Private helpers are prefixed `_` (`_feed.py`, `_clean`, `_published`, `_rank_key`).
- Adapter classes are `<Source>Adapter` with a `type` class attribute matching the config
  `type` string (`RssAdapter.type == "rss"`).
- Keep modules focused and small; when a file grows to do more than its layer's job, split
  it rather than letting it sprawl.
