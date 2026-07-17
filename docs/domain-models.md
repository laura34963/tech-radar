# Domain Models

> **Type:** Reference
> **Audience:** Developers, AI assistants, and any tooling that needs project context
> **Last updated:** 2026-07-17
>
> The core data structures: the `Item` entity, the per-day snapshot document, the config
> model, and the importance/severity value sets.
>
> **Status — forward-looking:** the `tech-radar/` code does not yet exist. Every field and
> value below is grounded in the approved spec and implementation plan. Re-verify against
> the dataclasses once implemented.
>
> Related docs:
> - [`project-overview.md`](project-overview.md) — how these models flow through the pipeline
> - [`coding-style.md` §3](coding-style.md#3-layering-rules) — where each model may be constructed/mutated
> - [`integrations.md`](integrations.md) — the sources that produce `Item`s

---

<a id="1-overview"></a>

## 1. Overview

tech-radar has **no relational database**. Its "domain" is three in-memory/on-disk
structures:

- **`Item`** — one piece of technical news, normalized from any source.
- **The snapshot** — a per-day JSON document holding all items plus a `meta` progress
  block; it is the single source of truth between pipeline stages.
- **`Config`** — the parsed `radar.toml`, driving what to fetch and how to render.

There is no ORM, no migrations, and no schema file. The snapshot JSON on disk
(`output/data/<date>.json`) is the closest thing to a persisted store.

---

<a id="2-item-entity"></a>

## 2. The `Item` entity

Defined in `radar/item.py` as a **frozen** dataclass. It is the contract between adapters
(which produce it) and every downstream stage.

| Field | Type | Purpose |
|---|---|---|
| `id` | `str` | Stable dedupe key: `sha256(url or guid)` truncated to 16 hex chars (`item_id()`) |
| `title` | `str` | Item headline |
| `url` | `str` | Link to the source (display only — never re-fetched) |
| `source_type` | `str` | One of `rss`, `cloud`, `github`, `security`, `social` |
| `category` | `str` | From the source config (e.g. `backend`, `cloud`) |
| `published` | `datetime` | Timezone-aware, UTC internally |
| `summary` | `str` | Raw excerpt from the source (pre-LLM), tag-stripped and truncated |
| `importance` | `str` | `critical` / `high` / `medium` / `low` (default `low`) |
| `provider` | `str \| None` | Cloud provider label: `aws` / `gcp` / `azure` |
| `tags` | `list[str]` | Adapter-specific tags (e.g. matched cloud services) |
| `severity` | `str \| None` | Security items only: `critical`/`high`/`medium`/`low` |
| `stack_match` | `list[str]` | Configured stack terms found in this item |
| `llm` | `dict \| None` | Enrichment output (see [§5.3](#5-value-sets-and-importance-flow)) |

**Invariants / non-obvious rules:**
- The dataclass is **frozen**; modify with `dataclasses.replace(item, ...)`, never in place.
  Its list fields (`tags`, `stack_match`) are still technically mutable — treat them as
  read-only. See [`coding-style.md` §3](coding-style.md#3-layering-rules).
- `published` for an **undated** feed entry falls back to "now" at parse time, so undated
  items always pass the lookback filter. Do not treat `published` as authoritative for
  such sources. (Classified `(c) unusual-but-intentional` — a deliberate v1 simplification.)
- `id` is content-addressable on the source URL/guid, which is what makes cross-source
  dedupe work.

---

<a id="3-snapshot-document"></a>

## 3. Snapshot document

One JSON file per run day at `output/data/<date>.json`, written atomically. Shape:

```json
{
  "meta": {
    "schema_version": 1,
    "date": "2026-07-17",
    "sources":  { "<source-name>": {"status": "ok|failed", "count": 12} },
    "enriched": { "<category>": true },
    "rendered": { "<date>": "sha256:abc…" }
  },
  "digest_summary": "optional overall TL;DR (LLM) or null",
  "items": [ { …Item as dict… } ]
}
```

The `meta` block is the **resumability contract** — each stage records per-unit progress
so re-runs skip completed work:

| `meta` key | Written by | Meaning |
|---|---|---|
| `sources` | fetch | Per-source status + item count; `ok` sources are skipped on re-run |
| `enriched` | enrich | Per-category completion; enriched items are skipped on re-run |
| `rendered` | render | Per-date snapshot hash; unchanged snapshots skip re-render |

Items are serialized via `store.item_to_dict` (which ISO-formats `published`) and restored
via `store.item_from_dict`.

---

<a id="4-configuration-model"></a>

## 4. Configuration model

`radar/config.py` parses `radar.toml` into a `Config` dataclass:

| Field | Type | Source in TOML |
|---|---|---|
| `general` | `dict` | `[general]` (title, timezone, `lookback_days`, `max_items_per_category`, `min_keep_importance`, `min_display_importance`) |
| `stack` | `dict` | `[stack]` (languages, frameworks, packages, ecosystems) |
| `categories` | `list[str]` | top-level `categories` (default: backend, frontend, devops, cloud, security) |
| `sources` | `list[dict]` | `[[sources]]` array of tables |
| `llm` | `dict` | `[llm]` (enabled, provider, base_url, model, `api_key_env`, `max_items_to_enrich`) |

**Validation (fail-fast, before any network call):** every source needs a known `type`
and a `category`; `rss`/`cloud` need `url`, `github` needs `repo`, `security` needs `feed`,
`social` needs `source`. A violation raises `ConfigError` naming the offending entry.

**Two importance thresholds** control the noise cut:

| Knob | Stage | Effect |
|---|---|---|
| `min_keep_importance` | fetch | Items below this are **discarded** and never stored |
| `min_display_importance` | render | Items at/above render as **full cards**; between the two thresholds → "also noted" |

---

<a id="5-value-sets-and-importance-flow"></a>

## 5. Value sets & importance flow

### 5.1 Enumerated values

| Set | Values | Notes |
|---|---|---|
| `importance` | `low` < `medium` < `high` < `critical` | Ordering fixed in `IMPORTANCE_ORDER` |
| `severity` (security) | `low`, `medium`, `high`, `critical` | Derived from CVSS score buckets (≥9.0 critical, ≥7.0 high, ≥4.0 medium, else low) |
| `source_type` | `rss`, `cloud`, `github`, `security`, `social` | The five v1 adapters |
| `provider` (cloud) | `aws`, `gcp`, `azure` | From source config |
| `llm` fields | `summary`, `detail`, `why_it_matters`, `recommended_action` | Populated by enrich |

### 5.2 Importance scoring (rule-based, in fetch)

```
has severity? ──yes──▶ importance = severity
     │no
     ▼
stack_match non-empty? ──yes──▶ importance = "high"
     │no
     ▼
source_type in {github, cloud, social}? ──yes──▶ "medium"
     │no
     ▼
importance = "low"   (typically plain rss)
```

The LLM enrich stage may refine importance but never invents items.

### 5.3 Display tiers (render)

```
importance ≥ min_display_importance  ──▶ full card (summary, detail, why-it-matters, action)
min_keep ≤ importance < min_display  ──▶ "Also noted" one-liner
importance < min_keep_importance     ──▶ discarded at fetch (never reaches render)
```

---

<a id="6-relationship-diagram"></a>

## 6. Relationship diagram

```
 Config ──drives──▶ Source (config entry)
                        │ adapter.fetch()
                        ▼
                      Item ──dedupe/score/rank──▶ Item[] ──stored in──▶ Snapshot
                                                                          │
                                          enrich writes Item.llm ◀────────┤
                                                                          ▼
                                                            render ──▶ digest HTML
                                                                   └──▶ index.html (hub)
```

Cardinality: one `Config` → many `Source`s → many `Item`s → exactly one `Snapshot` per
day → one digest HTML page per day + one shared hub index across all days.
