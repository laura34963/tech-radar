# Design: explicit `board` field for digest routing

- **Date:** 2026-07-23
- **Status:** Approved
- **Scope:** single implementation plan

## Problem

`radar/pipeline/render.py::_section()` decides which board (`tech` / `news`) an
item lands on by inspecting `(source_type, category)`:

```python
if st == "social":                                   return "news"
if st == "rss" and it.get("category") == "security": return "news"
return "tech"
```

This conflates the *transport* of a source with its *nature*, which produces two
defects:

1. **Advisories are split by transport, not nature.** CISA advisories arrive via
   RSS (`source_type == "rss"`, `category == "security"`) → routed to `news`.
   OSV/GHSA advisories arrive via the security adapter
   (`source_type == "security"`) → routed to `tech`. All three are actionable
   advisories; only the plumbing differs.
2. **News-nature feeds outside `security` are unreachable.** A news outlet filed
   under `category = "backend"` (e.g. InfoQ) is forced to `tech`, because
   "news-ness" is encoded only in which feeds the author happened to file under
   `security`.

The rule is a config coincidence baked into code.

## Approach

Introduce an optional per-source `board` field with values `tech` or `news`. The
value rides on the `Item`, is stamped once in the fetch loop, and is read by
`_section()`. The only remaining *inferred* default is `social → news`;
everything else defaults to `tech`.

Board routing becomes an explicit, source-level decision instead of an inference
from category name.

### Units and boundaries

| Unit | Responsibility | Depends on |
|------|----------------|------------|
| `Item.board: str \| None` | Carry the explicit override as data. `None` = "derive at render". | — |
| `config.load_config` | Validate `board` at the trust boundary; reject values outside `{tech, news}`. | raw TOML |
| `fetch.run_fetch` loop | The single point that stamps `board` onto fetched items (has both `source` and the items). | `Item`, source dict |
| `render._section` | Pure decision: explicit `board` wins, else `social → news`, else `tech`. | item dict |

No adapter is modified — stamping happens in the fetch loop, which is the one
place holding both the source config and the produced items.

### Data flow

```
radar.toml `board`
  → validated by load_config (ConfigError on bad value)
  → stamped onto Item in run_fetch via dataclasses.replace
  → persisted through existing item_to_dict / item_from_dict
    (new field with a default round-trips; old snapshots lack the key → default)
  → read by _section() at render time
```

## Behavior delta vs. today

- **CISA advisories move `news → tech`** (intentional): government advisories are
  treated as actionable reference alongside OSV/GHSA. CISA gets no `board` and
  falls to the `tech` default.
- **Seven security RSS feeds keep `news`** by declaring `board = "news"`:
  The Hacker News, SANS ISC, Krebs on Security, Palo Alto Unit 42, Cisco Talos,
  The DFIR Report, r/netsec.
- **All other sources: identical routing** to today.

## Error handling

- Invalid `board` value → `ConfigError` at load time (fail fast, before fetch).
- Missing `board` → default path. Back-compatible with snapshots written before
  this change, which have no `board` key; `Item(**d)` uses the field default.

## Testing

- `test_config`: a source with `board = "bogus"` raises `ConfigError`; a valid
  `board` loads.
- `test_fetch`: a source declaring `board = "news"` stamps it onto fetched items;
  a source without one leaves `board = None`.
- `test_render`:
  - `_section()` — explicit `board` wins (both directions, including
    `board="tech"` overriding a social item); default is `social → news` else
    `tech`; `rss + security` with no board now routes to `tech`.
  - Update `test_group_splits_items_into_tech_and_news_boards` and
    `test_digest_renders_both_tabs_with_tech_default`: their security news
    `Item`s gain `board = "news"`.

## Migration

`config/radar.toml` and `config/radar.example.toml` gain `board = "news"` on the
seven security news feeds in the **same commit** as the code change; otherwise
the news board loses its content on the next render. CISA and OSV/GHSA are left
unannotated (tech).

## Out of scope (YAGNI)

- No `board` values beyond `tech` / `news`.
- No per-item board override or LLM-based classification.
- No UI/label changes to the boards themselves.
