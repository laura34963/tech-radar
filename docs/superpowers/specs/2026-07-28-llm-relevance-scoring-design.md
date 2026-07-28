# LLM relevance scoring for the card tier

**Date:** 2026-07-28
**Status:** Deferred — do not implement until the exclusion-keyword mechanism has run in
production for several weeks and the residual noise has been observed.

**Depends on:** [`2026-07-27-noise-filtering-design.md`](2026-07-27-noise-filtering-design.md)
must ship first. This spec exists to record the design and the measurements while they are
fresh, not to authorise work.

## Problem

The prerequisite spec introduces `relevance_key = (importance, match_count, published)` and
documents, honestly, that it cannot rank the card tier well:

- 22 of the 36 card-tier items on the 2026-07-27 snapshot have exactly one keyword match,
  and only 7 carry a `severity`. For most items the key therefore collapses to *newest
  first*, which favours whichever source publishes most often — the behavior the card budget
  exists to contain.
- Keyword counts are the wrong signal, not merely a badly weighted one.
  `Advancing the next era of national science` (2 matches, corporate PR) outranks
  `Safety and alignment in an era of long-horizon models` (1 match, substantive). No
  re-weighting of match counts fixes an ordering where the count itself is misleading.
- This was verified against a weighted composite score in TRENDRADAR's style
  (`rank·0.6 + frequency·0.3 + hotness·0.1`). It changes nothing here: within a source group
  every item shares a `source_type`, so a source weight is constant; and a recency decay
  term orders items identically to using `published` as a tiebreaker. The only remaining
  input is the match count that is already in use.

Exclusion keywords are the cheaper and more effective lever and are already in the
prerequisite spec — they take `openai.com` from 9 card-tier items to 5, which no reordering
can achieve, because reordering cannot remove PR from the pool. What this spec addresses is
the *residue*: items that survive exclusion and still need ranking against each other.

## Why the earlier cost objection does not apply

The prerequisite spec's first draft rejected LLM filtering because "every fetched item must
be scored". That is true of filtering *before* fetch-stage filters, and it is the wrong
place to do it. Scoring only the card tier is 36 items on the sample snapshot — the same
order as the existing `max_items_to_enrich = 40`, i.e. roughly one enrich-sized pass.

The objection that does survive: this must not live inside `fetch`, which stays a pure,
offline-testable rule engine per `docs/coding-style.md`. Hence a separate stage.

## Design

### 1. A new optional stage, modelled on `enrich`

`radar/pipeline/score.py`, running **after** `fetch` and **before** `enrich`:

```
fetch  →  score (optional)  →  enrich (optional)  →  render
```

It mirrors `enrich`'s contract exactly, because that contract is already proven in this
codebase:

- Reads the snapshot, writes results back into it, checkpoints atomically per chunk.
- Chunked and concurrently executed, reusing the `_chunk` helper and the
  `ThreadPoolExecutor` shape from `radar/pipeline/enrich.py:96-120`.
- A chunk that fails is logged and skipped; its items keep their rule-based ordering.
- Resumable: an item already carrying a score is skipped unless `--force`.
- With no provider configured, the stage is a no-op and the digest is unchanged.

`radar.py` gains a `score` subcommand, and `run` becomes
`fetch → score → enrich → render`.

### 2. Interests expressed in prose

A new file `config/interests.md` (gitignored, with a committed `interests.example.md`),
holding a plain-prose description of what the reader cares about and what they do not. TOML
is wrong for this: it is a paragraph, not a table.

Why a separate file rather than a key in `radar.toml`: it is prose that will be edited often
and iterated on, and keeping it out of the config file means tuning it never risks a TOML
syntax error that breaks every stage.

The path is configurable via `[llm] interests_file`. If the file is absent, the stage is a
no-op — the same clean degradation as a missing API key.

### 3. What the model returns

For each item, a relevance score in `[0.0, 1.0]` and a one-line reason. Batched exactly like
enrichment: the prompt carries the interests prose plus a numbered listing of
`id | title | summary`, and the response is a JSON object keyed by item id.

`Item` gains `relevance: float | None = None` and `relevance_reason: str | None = None`.
As with `keyword_match` and `demoted`, no `store.py` change is needed — `item_to_dict` goes
through `__dict__` and `item_from_dict` uses `Item(**d)`, so older snapshots take the
defaults.

### 4. How the score is consumed

`relevance_key` becomes `(importance, relevance, match_count, published)`, all descending,
with `relevance` substituted by a neutral value when it is `None` so scored and unscored
items remain mutually orderable within one digest.

Deliberately **not** a threshold filter. The score reorders the card tier and therefore
decides which items the source-fairness pass demotes; it never removes an item from the
snapshot. Rationale: a hallucinated low score should cost an item its card, not its
existence. Exclusion keywords remain the only mechanism that discards.

`[llm] min_relevance` is therefore **not** part of this design. If a threshold is wanted
later, it should be argued for separately with evidence.

### 5. Two-stage tag extraction — rejected

TRENDRADAR extracts structured tags from the interests prose once, caches them, then scores
items against the tags. It is an optimization for their scale (hundreds of items per run,
many runs per day). At 36 items in a weekly run the extra stage buys nothing and adds a cache
to invalidate. Send the prose with each batch.

## Error handling

- No provider, no API key, or no interests file → the stage logs why and returns; the digest
  degrades to rule-based ordering.
- A chunk that times out, errors, or returns unparseable JSON → logged with its item count,
  its items keep `relevance = None`, and the run continues. Identical to
  `enrich`'s per-chunk isolation.
- A score outside `[0.0, 1.0]`, or a non-numeric value, is treated as missing rather than
  clamped. Clamping would silently launder a confused response into a confident-looking
  number.
- Item ids in the response that are absent from the chunk are ignored, as
  `_enrich_chunk` already does (`radar/pipeline/enrich.py:65`).

## Testing

All deterministic against a mocked provider — no live LLM in the suite.

```
score_stage_is_noop_without_provider
score_stage_is_noop_without_interests_file
scores_are_written_back_into_the_snapshot
chunk_failure_leaves_its_items_unscored_and_run_succeeds
out_of_range_score_is_treated_as_missing
non_numeric_score_is_treated_as_missing
response_id_not_in_chunk_is_ignored
already_scored_item_is_skipped_without_force
force_rescores_an_already_scored_item
relevance_orders_above_match_count_in_relevance_key
unscored_item_remains_orderable_against_scored_items
score_never_removes_an_item_from_the_snapshot
```

Acceptance is qualitative and must be judged on real output, not asserted in a test: on the
2026-07-27 snapshot, `Safety and alignment in an era of long-horizon models` should outrank
`Advancing the next era of national science`. If it does not, the interests prose is wrong or
this approach does not work — and that is the signal to abandon it rather than tune it
indefinitely.

## Cost

One enrich-sized pass per run: ~36 items, chunked at `enrich_chunk_size = 8`, so roughly 5
calls per run. At a weekly cadence that is ~5 additional LLM calls per week. Reuses the
existing provider abstraction in `radar/llm/provider.py`, so no new dependency and no new
credential.

## Open question to settle before implementing

Whether the prose interests file actually outperforms a well-tuned `[exclude]` list. The
prerequisite spec's exclusion mechanism has not yet run in production. If a few weeks of
exclusion tuning leaves the card tier well ordered, this stage is unnecessary and should be
closed rather than built.