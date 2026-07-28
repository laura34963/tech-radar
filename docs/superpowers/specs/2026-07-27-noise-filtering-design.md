# Exclusion keywords, word-boundary matching, and source diversity caps

**Date:** 2026-07-27
**Status:** Approved, pending implementation

## Problem

The digest surfaces too many items, and the ones it surfaces are ordered close to
arbitrarily. Measured against the real `output/data/2026-07-27.json` snapshot (83 items,
51 of them cards at `min_display_importance = "high"`):

1. **Tiers are saturated, so ordering collapses to publish date.** 51 of 83 items score
   `high` or `critical`, and 44 of those carry no `severity`. `_group` in
   `radar/pipeline/render.py:167` sorts cards by severity only, so those 44 all tie and
   fall back to newest-first. `max_items_per_category = 20` is already truncating, which
   means the cut is being made on a near-arbitrary basis.

2. **Pre-release releases flood a board.** 9 of 17 GitHub release items are
   canary/preview/beta (`vercel/next.js v16.3.0-canary.97`, `…canary.96`,
   `…preview.9`, `kubernetes/kubernetes v1.37.0-beta.0`, …).

3. **A single source dominates.** After removing pre-releases, `openai.com` holds 10
   cards, `simonwillison.net` 8, and `github.com/vercel/next.js` 6 — out of 42.

4. **Corporate PR scores the same as substantive news.** `category_keywords` boosts any
   `ai`-category item matching one of its terms to `high`, so
   `David Vélez and Robin Vince join the boards of the OpenAI Foundation` (matches
   `openai`) sits in the same tier as `Introducing Claude Opus 5` (matches `claude`).
   Both match exactly one keyword.

5. **Substring matching produces live false positives.** `stack.frameworks` contains
   `rails`, matched as a plain substring against `title + summary + url`
   (`radar/pipeline/fetch.py:22-25`). It matches `Guardrails`, so
   `Agentic AI Needs Guardrails, Not Guesswork` is scored `high` as though it affected
   the reader's stack. `ruby` similarly matches the string `rubyonrails` inside URLs.
   `config/radar.example.toml:24` already documents the workaround forced by this
   design ("no bare `ai` — matches `email`/`detail`").

6. **Nothing records why an item scored `high`.** `Item.stack_match` records `[stack]`
   hits only. `category_keywords` hits are discarded, so every `ai`-category card shows
   an empty match list and there is no way to tell which keyword admitted it.

## Non-goals

Deliberately excluded after measurement (see *Rejected alternatives*):

- A numeric composite relevance score.
- Cross-run continuity tracking (`first_seen` / `seen_count`).
- LLM-based relevance filtering.

## Goals

- Drop pre-release and low-value items **before** they reach the snapshot.
- Stop any single source from monopolising a board.
- Make keyword matching respect word boundaries, eliminating the `Guardrails` class of
  false positive and removing the "never write a short keyword" footgun.
- Record which keyword admitted an item, and log what was dropped and why, so the
  config can be tuned from evidence rather than guesswork.

Target: 51 cards → ~27 on the 2026-07-27 snapshot, with no `Item` schema break.

Note on Problem 1: this design does **not** change how cards are ordered. It attacks tier
saturation by volume instead — fewer, better-filtered cards make the arbitrary ordering
within a tier far less costly, since a reader can skim 27 items but not 51. Fixing the
ordering itself requires a relevance score, which is deferred (see *Rejected
alternatives*).

## Current architecture (for reference)

`radar/pipeline/fetch.py` — `run_fetch(cfg, snapshot_path, *, now, client, force, fresh)`:

1. Loop over `cfg.sources`; skip sources already marked `ok` unless `force`; call the
   adapter; merge fetched items into `by_id` (richest summary per id wins); checkpoint
   the snapshot after every source.
2. Finalize: for each item, `replace(importance=score_importance(...),
   stack_match=stack_matches(...))`; keep it if `within_lookback` **and**
   `importance_ge(min_keep_importance)`; then `dedupe` → `rank_and_truncate` per
   category at `max_items_per_category`.

The matching helpers `stack_matches`, `category_matches`, and `score_importance` live in
`fetch.py:22-45` and use `term.lower() in haystack.lower()` — plain substring.

## Design

### 1. New module `radar/match.py`

All item-matching logic moves into a new pure module: no I/O, no logging, no snapshot
awareness. This keeps `fetch.py` responsible for orchestration and state (resume,
checkpoint) only, and lets the matching rules be unit-tested without any fetch
scaffolding.

Moved from `fetch.py` (and changed to word-boundary matching):

- `stack_matches(item, stack) -> list[str]`
- `category_matches(item, category_keywords) -> list[str]`
- `score_importance(item, stack, category_keywords) -> str`

New:

| Function | Responsibility |
|---|---|
| `term_hits(terms, text) -> list[str]` | Case-insensitive, word-boundary matching; returns the matched terms in the order they appear in `terms`, with duplicate terms collapsed |
| `exclusion_hit(item, exclude) -> str \| None` | The first exclusion term that matches, else `None` |
| `source_key(url) -> str` | Normalized host; `github.com` split to owner/repo granularity |
| `cap_per_source(items, limit, rank_key) -> tuple[list[Item], dict[str, int]]` | Returns kept items plus per-source dropped counts for the caller to log |

`fetch.py` retains `importance_ge`, `within_lookback`, `dedupe`, `_rank_key`,
`rank_and_truncate`, `_source_name`, and `run_fetch`. The existing
`from radar.pipeline.fetch import importance_ge` in `radar/pipeline/enrich.py:7` is
unaffected.

### 2. Word-boundary matching

A term matches when it appears in `f"{title} {summary} {url}"` (case-insensitively) with
a non-word character or string edge on both sides. Implemented as:

```
(?<!\w) + re.escape(term) + (?!\w)
```

Lookarounds, **not** `\b`. `\b` is defined relative to the adjacent character, so it
fails for terms that begin or end with a non-word character — `react-dom` and `next.js`
are both in the current config, and `\bnext\.js\b` does not behave as intended.

Compiled patterns are cached per term so the pattern set is built once, not per item.

**Trade-off (accepted).** Word boundaries apply uniformly across title, summary, **and**
URL. The cost is that terms glued inside a domain no longer match: `rubyonrails.org`
stops matching `ruby` and `rails`. Measured impact on the 165 unique items in
`output/data/`: `rails` 4 → 3 hits, `ruby` 4 → 2 hits. Every affected item is a
rubyonrails.org post whose *title* already contains `Rails`, so it still matches and its
importance is unchanged — the real loss is zero. The alternative (word boundaries on text,
substring on URL) was rejected because it keeps the hazard alive on the URL field, where
`go` would match `govcloud`.

### 3. Exclusion keywords — new `[exclude]` config table

```toml
[general]
max_items_per_source = 3      # 0 or omitted disables the cap

[exclude]
# global: applies to every category
global   = ["fireside chat", "joins the board"]
# per-category: applies only within that category. `preview` belongs here, not in
# global — a global exclusion would also kill legitimate cloud announcements such as
# "PostgreSQL 19 Beta 2 is now available in Amazon RDS Database Preview Environment".
frontend = ["canary", "preview", "nightly"]
devops   = ["beta", "alpha"]
ai       = ["small business", "partner with"]
```

Semantics:

- An item is dropped when any term in `global + exclude.get(item.category, [])` matches
  it, using the §2 matcher.
- Dropped means **not written to the snapshot** — the same hard cut the existing
  `lookback_days` and `min_keep_importance` filters perform.
- `Config` gains `exclude: dict = field(default_factory=dict)`, populated from
  `raw.get("exclude", {})`.

Validation in `load_config` (`radar/config.py`), failing with `ConfigError`:

- Every key must be `"global"` or a member of `categories`. A mistyped category name is
  an error, not a silently inert rule.
- Every value must be a list of non-empty strings.
- `"global"` is a reserved key. A category literally named `global` would make an
  `[exclude]` entry mean both "every category" and "that one category", so declaring it
  in `categories` is rejected at load.

Configs with no `[exclude]` table behave exactly as today.

### 4. Source diversity cap

`[general] max_items_per_source` (default `3`; `0` or omitted disables it) limits how
many items one source may contribute to a single digest.

- **Key.** `source_key(url)`: lowercase host, leading `www.` stripped. When the host is
  `github.com` and the path has at least two segments, the key becomes
  `github.com/{owner}/{repo}` so sibling repos are not pooled. A URL that cannot be
  parsed, or that has an empty host, yields the literal fallback key `"(unknown)"` and
  **must not raise**. All such items therefore share one quota.
- **Exemption.** Items whose `severity` is `high` or `critical` bypass the cap entirely:
  they are never dropped, **and they do not consume quota**, so two critical advisories
  from one host cannot squeeze out that host's other items. A diversity rule must never
  hide a serious advisory, nor penalise a source for publishing one.
- **Which N are kept.** The existing `_rank_key` (importance descending, published
  descending), so the cap reuses the pipeline's established notion of rank.
- **Scope.** One global quota per source key, shared across categories. In practice a
  source is configured for a single category, so per-category quotas would add a
  configuration layer for almost no behavioral difference.

### 5. Finalize order in `run_fetch`

```
exclusion  →  score  →  lookback + min_keep  →  dedupe  →  source cap  →  category truncate
```

- Exclusion runs first: an excluded item is never scored.
- The cap runs **after** `dedupe`, so duplicate copies of one story cannot consume a
  source's quota, and **before** `rank_and_truncate`, which would otherwise have already
  applied the per-category cut.

### 6. Traceability

`Item` gains `keyword_match: list[str] = field(default_factory=list)`, populated from
`category_matches`. This fills the gap in Problem 6 — an `ai`-category card can now show
which keyword admitted it.

No `store.py` change is required: `item_to_dict` serializes via `it.__dict__`, so the
field is picked up automatically, and `item_from_dict` calls `Item(**d)`, so a snapshot
written before this change simply takes the dataclass default. The seven existing
snapshots in `output/data/` remain readable with no migration.

Displaying `keyword_match` on the digest card is **out of scope** — that is a template
concern, separate from fetch-stage ranking quality.

### 7. Logging

`run_fetch` adds two lines, because a bare total gives no way to tune the config:

- Items excluded, with the most frequently matching terms.
- Items dropped by the source cap, with the over-quota source keys.

The existing summary line (`fetch.py:138`) is extended to break its `dropped` count down
by cause (excluded / out of lookback / below `min_keep` / source cap).

### 8. Documentation and example config

- `config/radar.example.toml` gains `max_items_per_source` under `[general]` and a
  commented `[exclude]` table, seeded with the terms verified against real data
  (`canary` for `frontend`, `beta`/`alpha` for `devops`). The comment must record why
  `preview` is scoped to `frontend` rather than `global`, so the reasoning is not lost.
- `config/radar.example.toml:24` — the "no bare `ai`" warning is now obsolete and must be
  removed; word boundaries make short keywords safe.
- `docs/domain-models.md` — document `Item.keyword_match` and the `[exclude]` table in the
  config model.
- `docs/project-overview.md` §5 — the fetch stage description gains the exclusion and
  source-cap steps.

## Error handling

- `radar/match.py` is pure and network-free; it has no transient failure mode.
- Invalid `[exclude]` config raises `ConfigError` at load, using the existing exit-code
  path (`docs/coding-style.md` §4).
- `source_key` must tolerate a malformed or empty URL by returning the fallback key.
  An unparseable URL from one adapter must not abort the finalize pass.

## Testing

New `tests/test_match.py`, named for the behavior under test:

```
rails_does_not_match_guardrails
rails_matches_rails_with_trailing_punctuation
next_js_term_matches_despite_dot
react_dom_term_matches_despite_hyphen
global_exclude_applies_to_every_category
per_category_exclude_does_not_leak_across_categories
exclude_is_case_insensitive
item_with_no_exclusion_match_is_kept
source_cap_keeps_highest_ranked_n
source_cap_never_drops_high_severity_item
source_cap_exempt_item_does_not_consume_quota
source_cap_disabled_when_limit_zero
github_source_key_is_repo_scoped
www_prefix_stripped_from_source_key
malformed_url_source_key_does_not_raise
```

`per_category_exclude_does_not_leak_across_categories` is the regression test for the
`preview` over-blocking hazard: with `frontend = ["preview"]`, a `frontend` canary item
is dropped while the `cloud` item titled
`PostgreSQL 19 Beta 2 is now available in Amazon RDS Database Preview Environment`
survives.

Updates to existing suites:

- `tests/test_fetch.py` — the new finalize order, and imports moved to `radar.match`.
- `tests/test_config.py` — `[exclude]` shape validation: unknown key rejected,
  non-list value rejected, empty string rejected, a category named `global` rejected,
  absent table defaults to `{}`.
- `tests/test_store.py` — `keyword_match` round-trips, and a snapshot dict without the
  key still loads.

Acceptance check: re-run the pipeline against the real `output/data/2026-07-27.json`
and confirm cards fall from 51 to roughly 27, and that
`Agentic AI Needs Guardrails, Not Guesswork` no longer carries a `stack_match`.

## Rejected alternatives

**A numeric composite relevance score** (the `rank·0.6 + frequency·0.3 + hotness·0.1`
weighting from [TRENDRADAR](https://github.com/SANSAN0/TRENDRADAR)). Measured effect on
the 2026-07-27 snapshot: **zero volume change** — it only reorders the same 51 cards. It
also cannot separate the two items the digest most needs separated:
`David Vélez and Robin Vince join the boards of the OpenAI Foundation` matches one
keyword (`openai`) and `Introducing Claude Opus 5` matches one keyword (`claude`), so any
weighting of match counts ties them. The signal that distinguishes corporate PR from
substance is lexical (`join the board`, `fireside chat`, `small business`), which is what
§3 addresses directly. Deferred until exclusion keywords and the source cap have been
observed in production.

**Cross-run continuity tracking** (`first_seen` / `seen_count`, TRENDRADAR's ranking
timeline). 73% of items in `output/data/` appear in more than one snapshot, but those
snapshots are near-daily manual runs and `lookback_days = 7`, so adjacent windows overlap
by six days — the repetition is expected, not a defect. The scheduled cadence in
`.github/workflows/radar.yml:4` is weekly, so consecutive windows barely overlap and
`seen_count` would almost always be 1. Dead weight at the current cadence; revisit if the
schedule changes.

**LLM-based relevance filtering** (TRENDRADAR's `ai_interests.txt`: describe interests in
prose, have the model score each item, fall back to keywords on failure). Removes the
keyword-list maintenance burden, but moves LLM calls from the enrich stage — which
currently touches at most `max_items_to_enrich = 40` high/critical items — to *before*
fetch-stage filtering, where every fetched item must be scored. Cost and latency rise
substantially, and `fetch` changes from a pure, offline-testable rule engine into a
consumer of an external service, which conflicts with the layering in
`docs/coding-style.md`.

**A separate `frequency_words.txt` with `+`/`!`/`/regex/` syntax** (TRENDRADAR's format).
Requires a second config format and a hand-written parser alongside the existing TOML.
Measured need for regex is nil: `canary`, `join the boards`, and `fireside chat` are all
handled by word-boundary phrase matching, and accepting regex from config would open a
ReDoS and input-validation surface for no demonstrated benefit.

**Adding an `Item.source` field** instead of deriving the cap key from the URL. More
precise, but it changes the `Item` schema, snapshot serialization, and every adapter test.
The URL-derived key was verified to group correctly on real data (`openai.com` 10,
`simonwillison.net` 8, `github.com/vercel/next.js` 6), so the schema change buys nothing
today.
