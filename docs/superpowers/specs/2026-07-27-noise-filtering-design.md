# Exclusion keywords, word-boundary matching, and source fairness under a card budget

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

5. **Substring matching produces live false matches.** `stack` terms are matched as plain
   substrings against `title + summary + url` (`radar/pipeline/fetch.py:22-25`), so
   `rails` matches `Guardrails`. The Docker blog post
   `Agentic AI Needs Guardrails, Not Guesswork` records
   `stack_match = ["rails", "rails", "docker"]`, and `digest.html.j2:83` renders that list
   verbatim, so the card displays `stack: rails, rails, docker`. `ruby` likewise matches
   the string `rubyonrails` inside URLs.

   Two distinct defects here. The bogus `rails` is visible in the UI and makes
   `stack_match` useless as an explanation of why an item was boosted. The duplicate
   `rails` is separate: `rails` appears in both `stack.frameworks` and `stack.packages`,
   and `stack_matches` concatenates the lists without deduplicating.

   **Scope of the fix, measured honestly.** On the 2026-07-27 snapshot, word-boundary
   matching changes **no item's importance** — the Guardrails post is genuinely a Docker
   post and `docker` is in `[stack]`, so it stays `high` for the right reason, and the two
   items where `ruby` matched `rubyonrails` also match `rails` in their titles. What the
   fix delivers is correct provenance plus the removal of a documented footgun:
   `config/radar.example.toml:24` currently warns against short keywords ("no bare `ai` —
   matches `email`/`detail`"), a constraint that only exists because of substring matching.
   `available`, `availability`, `rails`, `details`, `webmail`, `taipei`, `container`, and
   `campaign` all contain `ai` as a substring in this dataset, so the hazard is real even
   though the workaround has so far contained it.

6. **Nothing records why an item scored `high`.** `Item.stack_match` records `[stack]`
   hits only. `category_keywords` hits are discarded, so every `ai`-category card shows
   an empty match list and there is no way to tell which keyword admitted it.

## Non-goals

Deliberately excluded after measurement (see *Rejected alternatives*):

- A numeric composite relevance score. Measured as useless here: it can only combine
  signals `relevance_key` already uses, and re-weighting keyword counts cannot separate PR
  from substance when the counts themselves are the bad signal.
- Cross-run continuity tracking (`first_seen` / `seen_count`).
- LLM-based relevance filtering — deferred to its own follow-up spec, not rejected.

## Goals

- Drop pre-release and low-value items **before** they reach the snapshot.
- Stop any single source from monopolising the card tier — but only when the card tier is
  actually over budget, and by **demoting** the losers to "also noted" rather than
  discarding them.
- Make keyword matching respect word boundaries and deduplicate its hits, so
  `stack_match` is a trustworthy explanation of why an item was boosted, and short
  keywords stop being a footgun.
- Record which keyword admitted an item, and log what was dropped and why, so the
  config can be tuned from evidence rather than guesswork.

Target on the 2026-07-27 snapshot: 51 cards → 30, with "also noted" **growing** from 32 to
38 as demoted items land there. Only the 15 items removed by exclusion keywords leave the
snapshot; the source-fairness pass discards nothing.

Note on Problem 1: this design does **not** change how cards are ordered. It attacks tier
saturation by volume instead — fewer, better-filtered cards make the arbitrary ordering
within a tier far less costly, since a reader can skim 30 items but not 51. Fixing the
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
| `relevance_key(item) -> tuple` | The pipeline's single sort key: importance, then match count, then published date |
| `apply_source_fairness(items, budget, floor) -> tuple[list[Item], dict[str, int]]` | Returns every input item — with `demoted` set on the ones that won neither a floor slot nor a merit slot — plus per-source demoted counts for the caller to log |

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

`stack_matches` concatenates `packages + frameworks + languages` before matching, and those
lists overlap in the shipped config (`rails` appears in two of them). `term_hits`
deduplicates, so `stack_match` no longer carries the same term twice.

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
max_card_items   = 30    # card-tier budget; 0 or omitted disables the fairness pass
per_source_limit = 3     # floor: every source is guaranteed up to this many cards

[exclude]
# global: applies to every category
global   = ["fireside chat", "joins the board"]
# per-category: applies only within that category. `preview` belongs here, not in
# global — a global exclusion would also kill legitimate cloud announcements such as
# "PostgreSQL 19 Beta 2 is now available in Amazon RDS Database Preview Environment".
frontend = ["canary", "preview", "nightly"]
devops   = ["beta", "alpha"]
# The `ai` list is the corporate-PR filter, and it is where this mechanism pays off most.
# Measured on 2026-07-27: these phrases take openai.com from 10 card-tier items to 5, and
# all 5 survivors are substantive. Keep broad words like `community` and `program` scoped
# to this category — globally they would shred legitimate cloud and devops items.
ai       = ["small business", "joins the boards", "join the boards", "board of directors",
            "national science", "news organizations", "community", "program", "anniversary"]
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

### 4. Source fairness under a card budget

There is **no fixed per-source cap.** A source is only ever trimmed when the card tier as
a whole is over budget, and even then nothing is discarded — the losers are demoted to
"also noted".

`[general] max_card_items` (default `30`; `0` or omitted disables the pass entirely) is
the budget: how many items the digest may present as cards before sources have to share.

**Scope.** The pass considers only items whose importance is `high` or `critical`.
`medium` items are untouched and continue through `rank_and_truncate` as they do today,
so the "also noted" breadth is preserved. The `high` threshold is not a new setting — it
is the same notion of significance `radar/pipeline/enrich.py:86` already uses.

**Under budget, nothing happens.** If the card-tier count is at or below
`max_card_items`, every item keeps its card, no matter how lopsided the source
distribution is. Diversity is enforced only under scarcity.

**Over budget: a per-source floor, then merit for the surplus.**

`[general] per_source_limit` (default `3`) is a **floor, not a cap**: every source is
guaranteed up to that many cards. A source holding fewer items than the floor simply keeps
what it has — there is nothing to pad.

Both keys are read with `cfg.general.get(...)` and coerced at use, matching how
`enrich_chunk_size` is handled today: a negative `max_card_items` is treated as `0`
(disabled), and a `per_source_limit` below `0` is treated as `0` (pure merit allocation).
No `load_config` validation is added for them.

1. Group card-tier items by `source_key`, each group sorted by `relevance_key` descending.
2. Seed the kept set with the exempt items (below). They can never be demoted, they count
   toward the budget, and they count toward their source's floor — a source with three
   exempt items has already met a floor of 3 and reserves nothing further.
3. **Floor phase.** Reserve each source's top `min(per_source_limit, len(group))` items.
4. **Merit phase.** Fill the remaining budget from all unreserved items, taken globally by
   `relevance_key` descending, **ignoring source**. The best leftovers win regardless of
   who published them.
5. Everything still unkept is **demoted**, not dropped.

**When the floors alone exceed the budget** — possible whenever
`per_source_limit × source count > max_card_items` — the floor phase fills level by level
(every source's 1st item, then every source's 2nd, and so on up to the floor), stopping the
moment the budget is full. Sources are visited in `relevance_key` order of their best item,
with `source_key` descending as the tie-break so the result is deterministic. This degrades
gracefully instead of letting the first few sources consume every floor slot. Note this
level-by-level fill is the only place a rotation appears, and it is bounded by the floor;
the surplus is always allocated by merit.

**`relevance_key`.** `(importance, len(stack_match) + len(keyword_match), published)`, all
descending. The match count is a deliberate addition to the existing
`_rank_key` — see the honest limitation below. Both fields are already computed during
scoring, so it costs nothing. `fetch._rank_key` is changed to delegate to `relevance_key`
so the pipeline has a single notion of rank; on real data this carries no behavioral change,
because `rank_and_truncate` currently truncates nothing (68 items in, 68 out at
`max_items_per_category = 20`).

**Honest limitation: "merit" is weaker than it sounds.** 22 of the 36 card-tier items in the
2026-07-27 snapshot have exactly one keyword match, and only 7 items in the tier carry a
`severity` at all, so `relevance_key` collapses to *newest first* for most of them. The merit
phase therefore
tends to favour whichever source publishes most often — the exact behavior the budget is
meant to contain. Measured: with `per_source_limit = 1` the merit phase hands
`simonwillison.net` 6 cards and leaves `openai.com` 3; at `per_source_limit = 3` the floor
dominates and the outcome is balanced (see the acceptance table). This is why the default
floor is 3 rather than 1. A real fix requires the relevance score that is deliberately
deferred (see *Rejected alternatives*); the match-count term is a partial mitigation that
does at least float `Introducing Claude Opus 5` (2 matches) above `openai.com`'s
single-match PR posts.

**Key.** `source_key(url)`: lowercase host, leading `www.` stripped. When the host is
`github.com` and the path has at least two segments, the key becomes
`github.com/{owner}/{repo}` so sibling repos are not pooled. A URL that cannot be parsed,
or that has an empty host, yields the literal fallback key `"(unknown)"` and **must not
raise**. All such items therefore share one group.

**Exemption.** Items whose `severity` is `high` or `critical` are never demoted. They do
count toward `max_card_items`, so the budget stays a real bound; if exempt items alone
exceed it, they are all still kept and the budget is overshot. A fairness rule must never
bury a serious advisory.

**Known consequence of the URL-derived key.** A GHSA advisory's URL points at the affected
repository, so advisories share a group with that repo's release items — on the
2026-07-27 snapshot, 4 of `github.com/vercel/next.js`'s card-tier items are advisories. The
severity exemption means those 4 are always kept and they satisfy next.js's floor outright,
so next.js holds 6 cards (4 exempt + 2 from the merit phase). This is acceptable: the
advisories are the items most worth showing.

### 5. Demotion

`Item` gains `demoted: str | None = None` — `None` for a normal item, otherwise the reason,
which for now is only `"source_fairness"`. A reason string rather than a boolean so the
digest and the logs can say *why* an item is in "also noted", which is the same need §7
addresses.

Demotion deliberately does **not** lower `importance`. The item's real importance is a
fact about the item; hiding a display decision inside it would make the snapshot lie and
would silently change every other consumer of that field.

Two consumers must honor the flag:

- **`render.py` `_group`** (`radar/pipeline/render.py:161`) routes an item to
  `also_noted` when it is demoted, regardless of whether it clears
  `min_display_importance`.
- **`enrich.py` `run_enrich`** (`radar/pipeline/enrich.py:86`) excludes demoted items from
  the eligible set. The `also_noted` template renders only a title link and a date
  (`radar/templates/digest.html.j2:109`) — no LLM fields at all — so enriching a demoted
  item spends tokens on output that is never displayed.

**Known limitation.** The fairness pass uses the `high` threshold while `render._group`
splits cards from "also noted" at `min_display_importance`. These agree at the shipped
default (`min_display_importance = "high"`). Lowering that setting to `medium` would
promote medium items to cards without their having passed through the fairness pass. Not
worth a second knob until someone actually lowers it.

A demoted item also keeps its `high` importance for `rank_and_truncate`, so it still
outranks `medium` items for a category's `max_items_per_category` slots. That is the
intended reading — a demoted item is still more important than a genuinely medium one.

### 6. Finalize order in `run_fetch`

```
exclusion  →  score  →  lookback + min_keep  →  dedupe  →  source fairness  →  category truncate
```

- Exclusion runs first: an excluded item is never scored.
- The fairness pass runs **after** `dedupe`, so duplicate copies of one story cannot
  consume a source's share of the budget, and **before** `rank_and_truncate`, which would
  otherwise have already applied the per-category cut.
- Unlike every other step in this chain, the fairness pass changes no item's membership in
  the snapshot — it only sets `demoted`.

### 7. Traceability

`Item` gains `keyword_match: list[str] = field(default_factory=list)`, populated from
`category_matches`. This fills the gap in Problem 6 — an `ai`-category card can now show
which keyword admitted it.

No `store.py` change is required for either new field: `item_to_dict` serializes via
`it.__dict__`, so `keyword_match` and `demoted` are picked up automatically, and
`item_from_dict` calls `Item(**d)`, so a snapshot written before this change simply takes
the dataclass defaults. The seven existing snapshots in `output/data/` remain readable
with no migration.

Displaying `keyword_match` on the digest card is **out of scope** — that is a template
concern, separate from fetch-stage ranking quality.

### 8. Logging

`run_fetch` adds two lines, because a bare total gives no way to tune the config:

- Items excluded, with the most frequently matching terms.
- Items demoted by the source-fairness pass, with the trimmed source keys and their
  counts. When the card tier is under budget, log that the pass was a no-op — silence
  would be indistinguishable from a misconfigured budget.

The existing summary line (`fetch.py:138`) is extended to break its `dropped` count down
by cause (excluded / out of lookback / below `min_keep`). Demotions are reported
separately, since a demoted item is not dropped.

### 9. Documentation and example config

- `config/radar.example.toml` gains `max_card_items` and `per_source_limit` under
  `[general]` and a commented `[exclude]` table, seeded with the terms verified against real
  data (`canary` for `frontend`, `beta`/`alpha` for `devops`, the PR phrases for `ai`). Three
  things the comments must record, or the reasoning is lost: why `preview` is scoped to
  `frontend` rather than `global`, why broad words like `community` and `program` are scoped
  to `ai`, and why `per_source_limit` is a floor rather than a cap.
- `config/radar.example.toml:24` — the "no bare `ai`" warning is now obsolete and must be
  removed; word boundaries make short keywords safe.
- `docs/domain-models.md` — document `Item.keyword_match`, `Item.demoted`, and the
  `[exclude]` table in the config model.
- `docs/project-overview.md` §5 — the fetch stage description gains the exclusion and
  source-fairness steps, and the note that `demoted` is set at fetch but consumed by
  render and enrich.
- `config/radar.example.toml` `[stack]` — brought up from 8 terms to the fuller set the
  project actually cares about (`docker`, `kubernetes`, `k8s`, `nginx`, `next.js`,
  `react-dom`, `typescript`, `puma`, `devise`, `nokogiri`, …). This is a behavior change to
  what CI publishes, not a cosmetic edit, and it is deliberate: the workflow copies this file
  over `config/radar.toml`, so the published digest was scoring against a stack nobody uses.
  Over-matching was checked across all 165 historical items before adopting it. Bare `next`
  was dropped from the stack: of its 37 raw hits, four items were promoted to card tier
  *solely* by the English word "next", two of them unrelated `security` stories with no
  connection to Next.js — so the term was removed and `next.js` kept in its place. `go` hits
  1 item; `k8s` and `golang` hit 0.

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
term_listed_in_two_stack_lists_is_reported_once
next_js_term_matches_despite_dot
react_dom_term_matches_despite_hyphen
global_exclude_applies_to_every_category
per_category_exclude_does_not_leak_across_categories
exclude_is_case_insensitive
item_with_no_exclusion_match_is_kept
github_source_key_is_repo_scoped
www_prefix_stripped_from_source_key
malformed_url_source_key_does_not_raise
fairness_pass_demotes_nobody_when_under_budget
fairness_pass_demotes_instead_of_dropping
fairness_pass_guarantees_floor_to_every_source
fairness_pass_does_not_pad_a_source_holding_fewer_than_the_floor
fairness_pass_allocates_surplus_by_merit_ignoring_source
fairness_pass_fills_floors_level_by_level_when_floors_exceed_budget
fairness_pass_never_demotes_high_severity_item
fairness_pass_counts_exempt_items_against_the_budget
fairness_pass_exempt_items_satisfy_their_sources_floor
fairness_pass_ignores_medium_items
fairness_pass_disabled_when_budget_zero
fairness_pass_is_deterministic_for_equal_ranks
relevance_key_ranks_more_matches_above_fewer
```

Three of these carry most of the regression weight:

- `per_category_exclude_does_not_leak_across_categories` — the `preview` over-blocking
  hazard. With `frontend = ["preview"]`, a `frontend` canary item is dropped while the
  `cloud` item titled
  `PostgreSQL 19 Beta 2 is now available in Amazon RDS Database Preview Environment`
  survives.
- `fairness_pass_demotes_nobody_when_under_budget` — the core of the requested behavior.
  Given one source holding every card and a budget above the card count, no item is
  demoted; lopsidedness alone must not trigger trimming.
- `fairness_pass_allocates_surplus_by_merit_ignoring_source` — the other half. Once floors
  are reserved, a source with many strong leftovers must be able to take several surplus
  slots in a row; the surplus is explicitly *not* rotated.

Updates to existing suites:

- `tests/test_fetch.py` — the new finalize order, imports moved to `radar.match`, and that
  the fairness pass leaves snapshot membership unchanged.
- `tests/test_config.py` — `[exclude]` shape validation: unknown key rejected,
  non-list value rejected, empty string rejected, a category named `global` rejected,
  absent table defaults to `{}`.
- `tests/test_store.py` — `keyword_match` and `demoted` round-trip, and a snapshot dict
  lacking both keys still loads.
- `tests/test_render.py` — a demoted `high` item renders under "also noted", not as a card,
  even though it clears `min_display_importance`.
- `tests/test_enrich.py` — a demoted item is not eligible for enrichment.

Acceptance check — every number below was produced by simulating this exact finalize chain
(exclusion → word-boundary rescoring → `min_keep` → fairness at `max_card_items = 30`,
`per_source_limit = 3` → category truncation) against the real
`output/data/2026-07-27.json`.

**These numbers depend on `config/radar.toml`, not `config/radar.example.toml`.** The sample
snapshot was produced locally, and the two configs differ materially: the local one carries 17
`[stack]` terms including `docker` and `max_items_per_category = 20`, while the committed
example carries 8 terms without `docker` and a cap of 15. The `Guardrails` case below only
arises at all because `docker` is in the local stack. Anything reproducing these figures must
read `[stack]`, `[category_keywords]` and `[general]` from `config/radar.toml`, and only
`[exclude]` from the example.

Worth flagging separately, because it is a live defect rather than a measurement artifact:
`.github/workflows/radar.yml:25` copies the example config over `config/radar.toml` in CI, so
the **published** digest has been running on the narrower 8-term stack all along. That is
addressed in §9 by bringing the example's `[stack]` up to match.

| Expectation | Value |
|---|---|
| Items leaving the snapshot | exactly 15, all from exclusion keywords; word-boundary rescoring drops 0 more |
| Card-tier candidates | 51 → 36 after exclusion |
| Cards | 51 → 30 (the budget binds) |
| Also noted | 32 → 38 (the 6 demoted items land here) |
| Demotions | 6, falling on `simonwillison.net` (4) and `openai.com` (2) |
| Floor vs merit split | floors reserve 28 of the 30 slots; the merit phase allocates 2 |

Resulting card distribution — no source exceeds its floor except where the merit phase or
the severity exemption earned it:

```
6  github.com/vercel/next.js     (4 exempt advisories + 2 merit)
3  github.com/react/react        3  docker.com
3  github.com/kubernetes/kubernetes
3  simonwillison.net             3  openai.com
2  rubyonrails.org               2  github.com/axios/axios
1  nextjs.org       1  aws.amazon.com      1  github.com/facebook/react
1  huggingface.co   1  deepmind.google
```

Over-blocking regression: `OpenAI and Hugging Face partner to address security incident`
must **survive**. An earlier draft of the `ai` list included `partner to address`, which
killed it — a substantive security story lost to an over-broad phrase. That phrase is
deliberately absent from the seeded list, and this item is the canary for anyone tempted to
add it back.

Plus: `Agentic AI Needs Guardrails, Not Guesswork` keeps its `high` importance — it is a
Docker post and `docker` is in `[stack]` — but its `stack_match` goes from
`["rails", "rails", "docker"]` to `["docker"]`.

## Rejected alternatives

**A numeric composite relevance score** (the `rank·0.6 + frequency·0.3 + hotness·0.1`
weighting from [TRENDRADAR](https://github.com/SANSAN0/TRENDRADAR)). Measured effect on
the 2026-07-27 snapshot: **zero volume change** — it only reorders the same 51 cards. It
also cannot separate the two items the digest most needs separated:
`David Vélez and Robin Vince join the boards of the OpenAI Foundation` matches one
keyword (`openai`) and `Introducing Claude Opus 5` matches one keyword (`claude`), so any
weighting of match counts ties them. The signal that distinguishes corporate PR from
substance is lexical (`join the board`, `fireside chat`, `small business`), which is what
§3 addresses directly. Deferred until exclusion keywords and the source-fairness pass have
been observed in production.

**A fixed per-source cap** (`max_items_per_source = 3`: no source may ever contribute more
than N items). Simpler to implement and to reason about, and it cut the 2026-07-27 snapshot
from 51 cards to 27. Rejected because it charges a source for depth even when the digest
has room to spare — a week with only 20 candidate cards would still have its best source
truncated to 3 for no benefit. §4 turns the same number into a floor and gates the whole
pass behind a budget, so it is a no-op when there is room.

**Round-robin (water-filling) for the whole allocation** — rotate one item per source per
round until the budget fills, with no separate floor or merit phase. It produces the same
headline numbers on this snapshot (30 cards, 10 demotions on the same two sources) and needs
one fewer config key. Rejected because rotation gives a source with many genuinely strong
leftovers no way to claim more than its neighbours: once every source has had its turn, the
next-best item in the whole digest loses to an unrelated source's weaker one. The
floor-plus-merit split in §4 keeps the diversity guarantee where it matters — the floor —
and lets quality decide the surplus. The trade-off is documented in §4's *honest
limitation*: with today's saturated importance tier, "quality" for the surplus is largely
recency, which is why the floor is defaulted high enough to dominate.

**Dropping the fairness losers instead of demoting them.** A whole-digest budget applied
destructively also squeezes out the "also noted" tier, because any rank-ordered allocation
lets cards outrank `medium` items. Measured: a budget of 30 over all
items yields 26 cards but only **4** "also noted" entries, down from 32 — the breadth
disappears, and one knob ends up controlling two unrelated things. Scoping the budget to
the card tier and demoting the losers keeps both adjustable and discards nothing.

**Cross-run continuity tracking** (`first_seen` / `seen_count`, TRENDRADAR's ranking
timeline). 73% of items in `output/data/` appear in more than one snapshot, but those
snapshots are near-daily manual runs and `lookback_days = 7`, so adjacent windows overlap
by six days — the repetition is expected, not a defect. The scheduled cadence in
`.github/workflows/radar.yml:4` is weekly, so consecutive windows barely overlap and
`seen_count` would almost always be 1. Dead weight at the current cadence; revisit if the
schedule changes.

**LLM-based relevance filtering** (TRENDRADAR's `ai_interests.txt`: describe interests in
prose, have the model score each item, fall back to keywords on failure). Deferred to its
own spec rather than rejected on cost — see
[`2026-07-28-llm-relevance-scoring-design.md`](2026-07-28-llm-relevance-scoring-design.md).

Two objections were raised against this and only one survived measurement. The surviving
one: it does not belong inside `fetch`, which must stay a pure, offline-testable rule engine
per `docs/coding-style.md`. The follow-up spec resolves that by making it a separate optional
stage, the way `enrich` already is.

The objection that did **not** survive was cost. Scoring "every fetched item" would indeed be
expensive, but only the card tier needs scoring — 51 items on the 2026-07-27 snapshot, 36
once the PR phrases in §3 are applied. That is the same order as the existing
`max_items_to_enrich = 40`, i.e. roughly one enrich-sized pass, not a scan of everything
fetched.

Sequencing, not cost, is the reason it waits: exclusion keywords are cheaper and, on this
data, strictly more effective. Adding PR phrases to the `ai` exclusion list takes
`openai.com` from 10 card-tier items to 5, and every survivor is substantive. No ranking
change achieves that, because ranking can only reorder a pool that still contains the PR.
Ship the cheap mechanism, observe it for a few weeks, then decide whether the residue
justifies the expensive one.

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
