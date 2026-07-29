# Noise Filtering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut the digest's card tier from 51 to 30 items by dropping noise with per-category exclusion keywords, fixing keyword matching to respect word boundaries, and demoting (never discarding) the items a per-source floor plus merit allocation cannot fit inside a card budget.

**Architecture:** All item-matching logic moves out of `radar/pipeline/fetch.py` into a new pure module `radar/match.py` — no I/O, no logging, no snapshot awareness — so the rules are unit-testable without fetch scaffolding and `fetch.py` stays an orchestrator. `fetch`'s finalize pass gains two steps: exclusion (a hard drop, before scoring) and source fairness (which only sets a `demoted` marker). Two consumers honor that marker: `render` routes demoted items to "also noted", and `enrich` skips them.

**Tech Stack:** Python 3.13, stdlib `re` / `functools.lru_cache` / `urllib.parse.urlsplit` / `collections.Counter`, `pytest`. No new dependency.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-27-noise-filtering-design.md`. The follow-up spec `2026-07-28-llm-relevance-scoring-design.md` is **Deferred** and out of scope — implement nothing from it.
- Word boundaries use `(?<!\w)` + `re.escape(term)` + `(?!\w)`, **not** `\b`. `\b` is defined relative to the adjacent character and misbehaves for terms beginning or ending with a non-word character — `react-dom` and `next.js` are both in the shipped config.
- Matching is case-insensitive and applies uniformly to `title + summary + url`. Losing glued-in-domain matches (`rubyonrails.org` no longer matching `ruby`) is accepted and measured as zero real loss.
- New `[general]` keys read via `cfg.general.get(...)` and coerced at use, matching how `enrich_chunk_size` is handled: `max_card_items` (default `30`, negative → `0` = disabled), `per_source_limit` (default `3`, negative → `0`). No `load_config` validation for these two.
- New `[exclude]` table **is** validated in `load_config`: every key must be `"global"` or a member of `categories`; every value a list of non-empty strings; `"global"` is reserved so a category named `global` is rejected.
- Exclusion **drops** items from the snapshot. Source fairness **never** drops — it only sets `Item.demoted`.
- `Item.severity` of `high`/`critical` is never demoted, but does consume budget and does satisfy its source's floor.
- Demotion must not alter `Item.importance`. The item's real importance is a fact about the item; hiding a display decision inside it would make the snapshot lie.
- No `radar/store.py` change: `item_to_dict` serializes via `it.__dict__` and `item_from_dict` calls `Item(**d)`, so new fields with defaults are backward compatible. The seven snapshots in `output/data/` must stay readable with no migration.
- Run tests with `.venv/bin/pytest`.

---

### Task 1: `Item.keyword_match` and `Item.demoted`

**Files:**
- Modify: `radar/item.py:13-28`
- Test: `tests/test_store.py`

**Interfaces:**
- Produces: `Item.keyword_match: list[str]` (default `[]`) — which `category_keywords` terms admitted the item. `Item.demoted: str | None` (default `None`) — the reason an item lost its card, currently only `"source_fairness"`. Both consumed by Tasks 4-7.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_store.py`:

```python
def test_item_roundtrip_carries_keyword_match_and_demoted():
    it = Item(id="1", title="t", url="u", source_type="rss", category="ai",
              published=datetime(2026, 7, 17, 9, tzinfo=timezone.utc), summary="s",
              importance="high", keyword_match=["claude"], demoted="source_fairness")
    back = item_from_dict(item_to_dict(it))
    assert back == it
    assert back.keyword_match == ["claude"] and back.demoted == "source_fairness"


def test_old_snapshot_without_new_fields_still_loads():
    # a dict written before these fields existed must take the dataclass defaults
    legacy = {"id": "1", "title": "t", "url": "u", "source_type": "rss",
              "category": "backend", "published": "2026-07-17T09:00:00+00:00",
              "summary": "s", "importance": "high", "provider": None, "tags": [],
              "severity": None, "stack_match": [], "board": None, "llm": None}
    it = item_from_dict(legacy)
    assert it.keyword_match == [] and it.demoted is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_store.py -k "keyword_match or new_fields" -v`
Expected: FAIL with `TypeError: Item.__init__() got an unexpected keyword argument 'keyword_match'`

- [ ] **Step 3: Add the two fields**

In `radar/item.py`, replace the dataclass body (lines 13-28) with:

```python
@dataclass(frozen=True)
class Item:
    id: str
    title: str
    url: str
    source_type: str
    category: str
    published: datetime
    summary: str
    importance: str = "low"
    provider: str | None = None
    tags: list[str] = field(default_factory=list)
    severity: str | None = None
    stack_match: list[str] = field(default_factory=list)
    # which category_keywords terms admitted this item; the counterpart to
    # stack_match, so an item's boost is always explainable.
    keyword_match: list[str] = field(default_factory=list)
    board: str | None = None
    # Why this item lost its card, or None. Set by the source-fairness pass and
    # read by render (routes to "also noted") and enrich (skips it). A reason
    # string rather than a bool so the logs can say which rule fired.
    demoted: str | None = None
    llm: dict | None = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_store.py -v`
Expected: all PASS (2 new + 4 pre-existing).

- [ ] **Step 5: Verify the real snapshots still load**

Run:

```bash
.venv/bin/python -c "
import glob, json
from radar.store import item_from_dict
n = 0
for f in sorted(glob.glob('output/data/*.json')):
    for d in json.load(open(f))['items']:
        it = item_from_dict(d); n += 1
        assert it.keyword_match == [] and it.demoted is None
print(f'{n} historical items loaded with defaults')
"
```

Expected: prints a count (165+) with no assertion error.

- [ ] **Step 6: Commit**

```bash
git add radar/item.py tests/test_store.py
git commit -m "feat: add Item.keyword_match and Item.demoted

keyword_match makes a category_keywords boost explainable, closing the gap
where every ai-category card showed an empty match list. demoted records why
an item lost its card without lying about its importance. Both default, so
existing snapshots load unchanged."
```

---

### Task 2: `radar/match.py` — word-boundary matcher and moved scoring

**Files:**
- Create: `radar/match.py`
- Modify: `radar/pipeline/fetch.py:1-9` (imports), delete `radar/pipeline/fetch.py:22-45` (the three moved functions)
- Test: Create `tests/test_match.py`; modify `tests/test_fetch.py:1-4` (imports) and remove the five moved tests at `tests/test_fetch.py:27-54`

**Interfaces:**
- Consumes: `Item`, `IMPORTANCE_ORDER` (`radar/item.py`).
- Produces:
  - `term_hits(terms: list[str], text: str) -> list[str]` — word-boundary, case-insensitive; returns matched terms in `terms` order, duplicates collapsed.
  - `haystack(it: Item) -> str` — `"{title} {summary} {url}"`.
  - `stack_matches(it: Item, stack: dict) -> list[str]`
  - `category_matches(it: Item, category_keywords: dict | None) -> list[str]`
  - `score_importance(it: Item, stack: dict, category_keywords: dict | None = None) -> str`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_match.py`:

```python
from datetime import datetime, timezone
from radar.item import Item
from radar.match import (term_hits, haystack, stack_matches, category_matches,
                         score_importance)

NOW = datetime(2026, 7, 17, tzinfo=timezone.utc)


def _item(**kw):
    base = dict(id="i", title="t", url="u", source_type="rss", category="backend",
                published=NOW, summary="s")
    base.update(kw)
    return Item(**base)


# --- word-boundary matching --------------------------------------------------

def test_rails_does_not_match_guardrails():
    it = _item(title="Agentic AI Needs Guardrails, Not Guesswork",
               url="https://www.docker.com/blog/agentic-ai-needs-guardrails/")
    assert stack_matches(it, {"frameworks": ["rails"]}) == []


def test_rails_matches_rails_with_trailing_punctuation():
    it = _item(title="Happy Anniversary Rails!", summary="", url="u")
    assert stack_matches(it, {"frameworks": ["rails"]}) == ["rails"]


def test_next_js_term_matches_despite_dot():
    it = _item(title="next.js 16 released", summary="", url="u")
    assert stack_matches(it, {"packages": ["next.js"]}) == ["next.js"]


def test_react_dom_term_matches_despite_hyphen():
    it = _item(title="react-dom patch", summary="", url="u")
    assert stack_matches(it, {"packages": ["react-dom"]}) == ["react-dom"]


def test_short_term_does_not_match_inside_longer_word():
    # the footgun documented in config/radar.example.toml: substring matching
    # made a bare "ai" keyword unusable because it hit "available"/"email".
    it = _item(category="ai", title="Now available by email", summary="", url="u")
    assert category_matches(it, {"ai": ["ai"]}) == []


def test_term_listed_in_two_stack_lists_is_reported_once():
    # `rails` sits in both frameworks and packages in the shipped config; the
    # concatenated list must not report it twice.
    it = _item(title="Rails 8 released", summary="", url="u")
    stack = {"packages": ["rails"], "frameworks": ["rails"], "languages": ["ruby"]}
    assert stack_matches(it, stack) == ["rails"]


def test_matching_is_case_insensitive():
    assert term_hits(["Rails"], "rails 8") == ["Rails"]
    assert term_hits(["rails"], "RAILS 8") == ["rails"]


def test_haystack_spans_title_summary_and_url():
    it = _item(title="A", summary="B", url="C")
    assert haystack(it) == "A B C"


# --- scoring (moved from tests/test_fetch.py) --------------------------------

def test_score_importance_precedence():
    assert score_importance(_item(severity="critical"), {}) == "critical"
    assert score_importance(_item(title="rails x"), {"packages": ["rails"]}) == "high"
    assert score_importance(_item(source_type="rss"), {}) == "low"
    assert score_importance(_item(source_type="github"), {}) == "medium"


def test_category_keywords_boost_matching_item_to_high():
    kw = {"ai": ["llm", "claude"]}
    assert score_importance(_item(category="ai", title="New Claude 5 model"), {}, kw) == "high"


def test_category_keywords_do_not_boost_non_matching_item():
    kw = {"ai": ["llm", "claude"]}
    assert score_importance(_item(category="ai", title="unrelated musings"), {}, kw) == "low"


def test_category_keywords_are_scoped_to_their_category():
    kw = {"ai": ["claude"]}
    assert score_importance(_item(category="backend", title="claude"), {}, kw) == "low"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_match.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'radar.match'`

- [ ] **Step 3: Create the module**

Create `radar/match.py`:

```python
"""Pure item-matching rules: keyword matching, importance scoring, exclusion,
and source-fairness allocation.

No I/O, no logging, no snapshot awareness — everything here is a function of its
arguments, so the rules can be tested offline without fetch scaffolding.
"""
from __future__ import annotations
import re
from functools import lru_cache
from radar.item import Item


@lru_cache(maxsize=None)
def _pattern(term: str) -> re.Pattern:
    """Word-boundary matcher for one term, compiled once per term.

    Lookarounds rather than `\\b`: `\\b` is defined relative to the adjacent
    character, so it fails for terms that begin or end with a non-word character
    — `react-dom` and `next.js` are both real entries in the shipped config.
    """
    return re.compile(r"(?<!\w)" + re.escape(term) + r"(?!\w)", re.IGNORECASE)


def term_hits(terms: list[str], text: str) -> list[str]:
    """Terms appearing in `text` on word boundaries, case-insensitively, in the
    order given by `terms` with duplicates collapsed. Callers concatenate config
    lists that overlap (`rails` is in both frameworks and packages), so the
    dedupe is what keeps a hit from being reported twice."""
    out: list[str] = []
    seen: set[str] = set()
    for term in terms:
        if not term:
            continue
        low = term.lower()
        if low in seen:
            continue
        if _pattern(term).search(text):
            seen.add(low)
            out.append(term)
    return out


def haystack(it: Item) -> str:
    """The text an item is matched against. URLs are included deliberately —
    a github.com/vercel/next.js link is a real signal — at the documented cost
    that terms glued inside a domain (`rubyonrails.org`) no longer match."""
    return f"{it.title} {it.summary} {it.url}"


def stack_matches(it: Item, stack: dict) -> list[str]:
    """`[stack]` terms this item mentions. Global: any category can match."""
    terms = (stack.get("packages", []) + stack.get("frameworks", [])
             + stack.get("languages", []))
    return term_hits(terms, haystack(it))


def category_matches(it: Item, category_keywords: dict | None = None) -> list[str]:
    """Keyword hits for the item's OWN category. Unlike stack_matches this is
    scoped per-category, so an 'ai' keyword can never boost a security item."""
    terms = (category_keywords or {}).get(it.category, [])
    if not terms:
        return []
    return term_hits(terms, haystack(it))


def score_importance(it: Item, stack: dict,
                     category_keywords: dict | None = None) -> str:
    if it.severity:
        return it.severity
    if stack_matches(it, stack) or category_matches(it, category_keywords):
        return "high"
    if it.source_type in ("github", "cloud", "social", "registry"):
        return "medium"
    return "low"
```

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `.venv/bin/pytest tests/test_match.py -v`
Expected: 12 PASS

- [ ] **Step 5: Delete the moved functions from `fetch.py`**

In `radar/pipeline/fetch.py`, delete lines 22-45 — the whole block from `def stack_matches(it: Item, stack: dict) -> list[str]:` through the `return "low"` that ends `score_importance`. Then replace the import block (lines 1-11) with:

```python
from __future__ import annotations
import logging
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from radar.item import Item, IMPORTANCE_ORDER
from radar.match import category_matches, score_importance, stack_matches
from radar.adapters import ADAPTERS
from radar.store import (new_snapshot, load_snapshot, atomic_write_json,
                         item_to_dict, item_from_dict)

log = logging.getLogger("radar.fetch")
```

- [ ] **Step 6: Update `tests/test_fetch.py` imports and drop the moved tests**

Replace `tests/test_fetch.py` lines 1-4 with:

```python
from datetime import datetime, timezone, timedelta
from radar.item import Item
from radar.pipeline.fetch import (importance_ge, within_lookback, dedupe,
                                  rank_and_truncate)
```

Then delete `tests/test_fetch.py` lines 27-54 — the five tests now living in `tests/test_match.py`: `test_stack_matches_by_substring`, `test_score_importance_precedence`, `test_category_keywords_boost_matching_item_to_high`, `test_category_keywords_do_not_boost_non_matching_item`, and `test_category_keywords_are_scoped_to_their_category`.

- [ ] **Step 7: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: full suite PASS. `run_fetch` still calls `score_importance` and `stack_matches`, now resolved through the new import.

- [ ] **Step 8: Confirm the real false match is gone**

Run:

```bash
.venv/bin/python -c "
import json, tomllib
from radar.store import item_from_dict
from radar.match import stack_matches
stack = tomllib.load(open('config/radar.toml','rb'))['stack']
for d in json.load(open('output/data/2026-07-27.json'))['items']:
    if 'Guardrails' in d['title']:
        print('was:', d['stack_match'])
        print('now:', stack_matches(item_from_dict(d), stack))
"
```

Expected: `was: ['rails', 'rails', 'docker']` / `now: ['docker']` — the bogus `rails` and its duplicate are both gone, and the legitimate `docker` hit survives.

- [ ] **Step 9: Commit**

```bash
git add radar/match.py radar/pipeline/fetch.py tests/test_match.py tests/test_fetch.py
git commit -m "feat: match keywords on word boundaries in a new pure module

Move stack_matches, category_matches and score_importance out of the fetch
orchestrator into radar/match.py, and match on word boundaries via lookarounds
instead of substrings. Lookarounds not \\b, because \\b misbehaves for terms
that start or end with a non-word character (react-dom, next.js).

Fixes a live false match: 'rails' hit 'Guardrails', so a docker.com post
recorded stack_match ['rails','rails','docker'] and the card rendered that
list verbatim. term_hits also dedupes, killing the duplicate that came from
'rails' being listed in both frameworks and packages."
```

---

### Task 3: Exclusion keywords — `exclusion_hit` and `[exclude]` config

**Files:**
- Modify: `radar/match.py` (add `exclusion_hit` after `category_matches`), `radar/config.py:12-13,19-29,32-62`
- Test: `tests/test_match.py`, `tests/test_config.py`

**Interfaces:**
- Consumes: `term_hits`, `haystack` (Task 2).
- Produces:
  - `exclusion_hit(it: Item, exclude: dict | None) -> str | None` — the first term disqualifying the item, else `None`. Terms are `exclude["global"] + exclude[it.category]`.
  - `Config.exclude: dict` (default `{}`) — validated at load.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_match.py`:

```python
from radar.match import exclusion_hit


def test_global_exclude_applies_to_every_category():
    exclude = {"global": ["fireside chat"]}
    for cat in ("ai", "security", "backend"):
        it = _item(category=cat, title="A Fireside Chat with the team")
        assert exclusion_hit(it, exclude) == "fireside chat"


def test_per_category_exclude_does_not_leak_across_categories():
    # the measured over-blocking hazard: `preview` must kill a frontend canary
    # release without touching a legitimate cloud announcement.
    exclude = {"frontend": ["preview"]}
    canary = _item(category="frontend", title="vercel/next.js v16.3.0-preview.9")
    cloud = _item(category="cloud",
                  title="PostgreSQL 19 Beta 2 is now available in Amazon RDS "
                        "Database Preview Environment")
    assert exclusion_hit(canary, exclude) == "preview"
    assert exclusion_hit(cloud, exclude) is None


def test_exclude_is_case_insensitive():
    assert exclusion_hit(_item(title="CANARY build"), {"global": ["canary"]}) == "canary"


def test_item_with_no_exclusion_match_is_kept():
    assert exclusion_hit(_item(title="Rails 8 released"), {"global": ["canary"]}) is None


def test_exclusion_hit_with_no_config_is_none():
    assert exclusion_hit(_item(title="anything"), None) is None
    assert exclusion_hit(_item(title="anything"), {}) is None
```

Add to `tests/test_config.py`:

```python
def test_exclude_table_loads(tmp_path):
    cfg = load_config(_write(tmp_path, """
        categories = ["frontend", "ai"]
        [exclude]
        global = ["fireside chat"]
        frontend = ["canary", "preview"]
    """))
    assert cfg.exclude["global"] == ["fireside chat"]
    assert cfg.exclude["frontend"] == ["canary", "preview"]


def test_absent_exclude_table_defaults_to_empty(tmp_path):
    cfg = load_config(_write(tmp_path, """
        categories = ["frontend"]
    """))
    assert cfg.exclude == {}


def test_exclude_unknown_key_raises(tmp_path):
    with pytest.raises(ConfigError, match="exclude.frontnd"):
        load_config(_write(tmp_path, """
            categories = ["frontend"]
            [exclude]
            frontnd = ["canary"]
        """))


def test_exclude_non_list_value_raises(tmp_path):
    with pytest.raises(ConfigError, match="list of non-empty strings"):
        load_config(_write(tmp_path, """
            categories = ["frontend"]
            [exclude]
            frontend = "canary"
        """))


def test_exclude_empty_string_term_raises(tmp_path):
    with pytest.raises(ConfigError, match="list of non-empty strings"):
        load_config(_write(tmp_path, """
            categories = ["frontend"]
            [exclude]
            frontend = ["canary", "  "]
        """))


def test_category_named_global_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="reserved"):
        load_config(_write(tmp_path, """
            categories = ["global", "frontend"]
        """))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_match.py -k exclu -v tests/test_config.py -k exclude`
Expected: FAIL with `ImportError: cannot import name 'exclusion_hit'` and `AttributeError: 'Config' object has no attribute 'exclude'`.

- [ ] **Step 3: Add `exclusion_hit` to `radar/match.py`**

Insert after `category_matches`:

```python
def exclusion_hit(it: Item, exclude: dict | None) -> str | None:
    """The first configured term that disqualifies this item, else None.

    `exclude["global"]` applies to every category; `exclude[<category>]` only
    within its own. Per-category scoping is load-bearing, not cosmetic: `preview`
    is pure noise on a frontend release feed but names a real product stage in a
    cloud announcement, so a single global list would over-block.
    """
    if not exclude:
        return None
    terms = list(exclude.get("global", [])) + list(exclude.get(it.category, []))
    hits = term_hits(terms, haystack(it))
    return hits[0] if hits else None
```

- [ ] **Step 4: Add `exclude` to `Config` and validate it**

In `radar/config.py`, add the reserved-key constant next to `_BOARDS` (line 12):

```python
_BOARDS = {"tech", "news"}

# `[exclude]` keys are category names plus this one reserved word meaning
# "every category", so a category may not be called it.
_RESERVED_CATEGORY = "global"
```

Add the field to `Config` after `category_keywords` (line 29):

```python
    # exclusion keywords: {"global": [...], "<category>": [...]}. An item matching
    # any applicable term is dropped at fetch, never written to the snapshot.
    exclude: dict = field(default_factory=dict)
```

In `load_config`, insert this validation after the `categories`/`sources` per-source loop ends (after line 54, immediately before the `return Config(...)`):

```python
    if _RESERVED_CATEGORY in categories:
        raise ConfigError(
            f"categories must not contain {_RESERVED_CATEGORY!r}: it is reserved as "
            f"the [exclude] key meaning 'every category'")
    exclude = raw.get("exclude", {})
    if not isinstance(exclude, dict):
        raise ConfigError("[exclude] must be a table")
    allowed = set(categories) | {_RESERVED_CATEGORY}
    for key, terms in exclude.items():
        if key not in allowed:
            raise ConfigError(
                f"exclude.{key}: unknown key; expected {_RESERVED_CATEGORY!r} "
                f"or one of {sorted(categories)}")
        if not isinstance(terms, list) or not all(
                isinstance(t, str) and t.strip() for t in terms):
            raise ConfigError(f"exclude.{key}: must be a list of non-empty strings")
```

Then extend the `return Config(...)` call (lines 55-62) with the new argument:

```python
    return Config(
        general=raw.get("general", {}),
        stack=raw.get("stack", {}),
        categories=categories,
        sources=sources,
        llm=raw.get("llm", {}),
        category_keywords=raw.get("category_keywords", {}),
        exclude=exclude,
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_match.py tests/test_config.py -v`
Expected: all PASS (5 new match tests + 6 new config tests + all pre-existing).

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: PASS. `Config` gained a defaulted field, so every existing `Config(...)` call in the tests still works.

- [ ] **Step 7: Commit**

```bash
git add radar/match.py radar/config.py tests/test_match.py tests/test_config.py
git commit -m "feat: add per-category exclusion keywords

exclusion_hit reports the first term disqualifying an item, drawing from a
global list plus the item's own category. Per-category scoping is required,
not cosmetic: 'preview' is noise on a frontend release feed but names a real
product stage in 'PostgreSQL 19 Beta 2 ... RDS Database Preview Environment'.

load_config rejects unknown [exclude] keys so a mistyped category name fails
loudly instead of becoming a silently inert rule, and reserves 'global' as a
category name since it would otherwise mean two things at once."
```

---

### Task 4: `source_key` and `relevance_key`

**Files:**
- Modify: `radar/match.py` (append both), `radar/pipeline/fetch.py:57-58` (`_rank_key` delegates)
- Test: `tests/test_match.py`

**Interfaces:**
- Consumes: `Item`, `IMPORTANCE_ORDER`.
- Produces:
  - `source_key(url: str) -> str` — normalized host; `github.com` split to `github.com/{owner}/{repo}`; never raises; unusable URL → `"(unknown)"`.
  - `relevance_key(it: Item) -> tuple[int, int, datetime]` — `(importance rank, len(stack_match) + len(keyword_match), published)`, intended for `sorted(..., reverse=True)`.
  - `fetch._rank_key` now delegates to `relevance_key`, so the pipeline has one notion of rank.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_match.py`:

```python
from radar.match import source_key, relevance_key


def test_github_source_key_is_repo_scoped():
    assert source_key("https://github.com/vercel/next.js/releases/tag/v16") == \
        "github.com/vercel/next.js"
    # sibling repos must not pool into one bucket
    assert source_key("https://github.com/facebook/react/releases/tag/v19") == \
        "github.com/facebook/react"


def test_github_url_without_repo_path_falls_back_to_host():
    assert source_key("https://github.com/") == "github.com"


def test_www_prefix_stripped_from_source_key():
    assert source_key("https://www.docker.com/blog/x") == "docker.com"
    assert source_key("https://docker.com/blog/x") == "docker.com"


def test_source_key_is_case_insensitive_on_host():
    assert source_key("https://OpenAI.com/blog") == "openai.com"


def test_malformed_url_source_key_does_not_raise():
    for bad in ("", "not a url", "mailto:x@y.z", "https://[oops"):
        assert source_key(bad) == "(unknown)"


def test_relevance_key_ranks_more_matches_above_fewer():
    few = _item(id="few", importance="high", stack_match=["docker"])
    many = _item(id="many", importance="high",
                 stack_match=["docker"], keyword_match=["llm", "claude"])
    assert relevance_key(many) > relevance_key(few)


def test_relevance_key_ranks_importance_above_match_count():
    crit = _item(id="c", importance="critical")
    high = _item(id="h", importance="high", stack_match=["a", "b", "c"])
    assert relevance_key(crit) > relevance_key(high)


def test_relevance_key_breaks_equal_matches_by_recency():
    old = _item(id="o", importance="high", published=NOW - timedelta(days=1))
    new = _item(id="n", importance="high", published=NOW)
    assert relevance_key(new) > relevance_key(old)
```

Add `timedelta` to the datetime import at the top of `tests/test_match.py`:

```python
from datetime import datetime, timezone, timedelta
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_match.py -k "source_key or relevance_key" -v`
Expected: FAIL with `ImportError: cannot import name 'source_key'`

- [ ] **Step 3: Append both functions to `radar/match.py`**

Add the import at the top of `radar/match.py` (alongside the existing imports):

```python
from urllib.parse import urlsplit
from radar.item import Item, IMPORTANCE_ORDER
```

Append:

```python
_UNKNOWN_SOURCE = "(unknown)"


def source_key(url: str) -> str:
    """Grouping key for source fairness: the publisher, as coarsely as is useful.

    Host, lowercased, with a leading `www.` stripped. `github.com` is split to
    owner/repo so sibling repos are not pooled into one quota. Never raises — an
    unusable URL lands in one shared bucket rather than aborting the pass, since
    a single bad URL from one adapter must not fail the whole finalize.
    """
    try:
        parts = urlsplit(url or "")
    except ValueError:
        return _UNKNOWN_SOURCE
    host = (parts.netloc or "").lower().removeprefix("www.")
    if not host:
        return _UNKNOWN_SOURCE
    if host == "github.com":
        seg = parts.path.strip("/").split("/")
        if len(seg) >= 2 and seg[0] and seg[1]:
            return f"github.com/{seg[0]}/{seg[1]}"
    return host


def relevance_key(it: Item) -> tuple:
    """The pipeline's single notion of rank, for `sorted(..., reverse=True)`:
    importance tier, then how many configured terms the item matched, then
    recency.

    The match-count term is a weak signal by design — most items match exactly
    one term, so this often decays to newest-first. It is included because it is
    free (both fields are computed during scoring) and it does separate some real
    cases. A genuine relevance signal is deferred; see the follow-up spec.
    """
    return (IMPORTANCE_ORDER[it.importance],
            len(it.stack_match) + len(it.keyword_match),
            it.published)
```

- [ ] **Step 4: Make `fetch._rank_key` delegate**

In `radar/pipeline/fetch.py`, replace `_rank_key` (lines 57-58) with:

```python
def _rank_key(it: Item):
    """One notion of rank across the pipeline; see match.relevance_key."""
    return relevance_key(it)
```

and add `relevance_key` to the `radar.match` import line:

```python
from radar.match import (category_matches, relevance_key, score_importance,
                         stack_matches)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_match.py -v`
Expected: all PASS (8 new + 17 pre-existing).

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: PASS. `test_rank_and_truncate_limits_per_category` still holds — its items all have zero matches, so the new middle term ties and ordering stays newest-first.

- [ ] **Step 7: Verify grouping against real data**

Run:

```bash
.venv/bin/python -c "
import json, collections
from radar.match import source_key
items = json.load(open('output/data/2026-07-27.json'))['items']
for k, n in collections.Counter(source_key(i['url']) for i in items).most_common(6):
    print(f'{n:3}  {k}')
"
```

Expected: `aws.amazon.com`, `github.com/vercel/next.js`, `github.com/axios/axios`, `openai.com`, `simonwillison.net` at the top — repo-level granularity, no bucket named `github.com`.

- [ ] **Step 8: Commit**

```bash
git add radar/match.py radar/pipeline/fetch.py tests/test_match.py
git commit -m "feat: add source_key and relevance_key

source_key derives the fairness grouping key from the URL, splitting
github.com to owner/repo so sibling repos keep separate quotas, and falling
back to one shared bucket rather than raising on an unusable URL.

relevance_key becomes the pipeline's single sort key, with _rank_key
delegating to it. The match-count term is free (both fields are already
computed during scoring) and admittedly weak — most items match one term, so
it often decays to newest-first. Documented rather than oversold."
```

---

### Task 5: `apply_source_fairness`

**Files:**
- Modify: `radar/match.py` (append)
- Test: `tests/test_match.py`

**Interfaces:**
- Consumes: `source_key`, `relevance_key`, `IMPORTANCE_ORDER`, `dataclasses.replace`.
- Produces: `apply_source_fairness(items: list[Item], budget: int, floor: int) -> tuple[list[Item], dict[str, int]]` — returns every input item (demoted ones replaced with `demoted="source_fairness"`) plus `{source_key: demoted count}`. Requires unique `Item.id` across `items`, which `dedupe` guarantees upstream.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_match.py`:

```python
from radar.match import apply_source_fairness


def _card(id, host, n=0, published=None, severity=None):
    """A high-importance item from `host`, with `n` fake stack matches."""
    return _item(id=id, importance="high", url=f"https://{host}/{id}",
                 stack_match=[f"t{i}" for i in range(n)],
                 published=published or NOW, severity=severity)


def test_fairness_pass_demotes_nobody_when_under_budget():
    # one source holding every card; lopsidedness alone must not trigger trimming
    items = [_card(str(i), "openai.com") for i in range(10)]
    out, demoted = apply_source_fairness(items, budget=30, floor=3)
    assert demoted == {}
    assert all(it.demoted is None for it in out)


def test_fairness_pass_demotes_instead_of_dropping():
    items = [_card(str(i), "openai.com") for i in range(5)]
    out, demoted = apply_source_fairness(items, budget=3, floor=3)
    assert len(out) == 5                                   # nothing discarded
    assert sum(1 for it in out if it.demoted) == 2
    assert demoted == {"openai.com": 2}
    assert all(it.importance == "high" for it in out)       # importance untouched


def test_fairness_pass_guarantees_floor_to_every_source():
    # 3 sources x 4 cards = 12, budget 9, floor 3 -> each source keeps exactly 3
    items = [_card(f"{h}-{i}", h)
             for h in ("a.com", "b.com", "c.com") for i in range(4)]
    out, _ = apply_source_fairness(items, budget=9, floor=3)
    kept = collections.Counter(source_key(it.url) for it in out if not it.demoted)
    assert kept == {"a.com": 3, "b.com": 3, "c.com": 3}


def test_fairness_pass_does_not_pad_a_source_holding_fewer_than_the_floor():
    items = [_card("a1", "a.com")] + [_card(f"b{i}", "b.com") for i in range(5)]
    out, _ = apply_source_fairness(items, budget=4, floor=3)
    kept = collections.Counter(source_key(it.url) for it in out if not it.demoted)
    # a.com has only 1 item and keeps it; b.com takes its floor of 3
    assert kept == {"a.com": 1, "b.com": 3}


def test_fairness_pass_allocates_surplus_by_merit_ignoring_source():
    # floor 1 reserves 1 per source (2 slots), budget 4 leaves 2 merit slots.
    # b.com's leftovers have more matches, so it takes BOTH — no rotation.
    items = [_card("a1", "a.com", n=5), _card("a2", "a.com", n=0),
             _card("a3", "a.com", n=0),
             _card("b1", "b.com", n=4), _card("b2", "b.com", n=3),
             _card("b3", "b.com", n=2)]
    out, _ = apply_source_fairness(items, budget=4, floor=1)
    kept = {it.id for it in out if not it.demoted}
    assert kept == {"a1", "b1", "b2", "b3"}


def test_fairness_pass_fills_floors_level_by_level_when_floors_exceed_budget():
    # 4 sources x floor 3 = 12 reserved but budget is only 4, so the floor phase
    # must give every source its 1st item before anyone gets a 2nd.
    items = [_card(f"{h}-{i}", h, n=(3 - i))
             for h in ("a.com", "b.com", "c.com", "d.com") for i in range(3)]
    out, _ = apply_source_fairness(items, budget=4, floor=3)
    kept = collections.Counter(source_key(it.url) for it in out if not it.demoted)
    assert kept == {"a.com": 1, "b.com": 1, "c.com": 1, "d.com": 1}


def test_fairness_pass_never_demotes_high_severity_item():
    advisory = _card("cve", "openai.com", severity="critical")
    filler = [_card(str(i), "openai.com", n=5) for i in range(5)]
    out, _ = apply_source_fairness([advisory] + filler, budget=2, floor=0)
    kept = {it.id for it in out if not it.demoted}
    assert "cve" in kept


def test_fairness_pass_counts_exempt_items_against_the_budget():
    advisories = [_card(f"cve{i}", "a.com", severity="high") for i in range(3)]
    others = [_card(f"o{i}", "b.com") for i in range(3)]
    out, _ = apply_source_fairness(advisories + others, budget=3, floor=0)
    kept = {it.id for it in out if not it.demoted}
    assert kept == {"cve0", "cve1", "cve2"}   # budget consumed by the exempt items


def test_fairness_pass_exempt_items_satisfy_their_sources_floor():
    # a.com's 2 advisories already meet a floor of 2, so it reserves nothing more
    advisories = [_card(f"cve{i}", "a.com", severity="high") for i in range(2)]
    extra = [_card(f"a{i}", "a.com", n=5) for i in range(2)]
    others = [_card(f"b{i}", "b.com") for i in range(2)]
    out, _ = apply_source_fairness(advisories + extra + others, budget=4, floor=2)
    kept = collections.Counter(source_key(it.url) for it in out if not it.demoted)
    assert kept == {"a.com": 2, "b.com": 2}


def test_fairness_pass_ignores_medium_items():
    med = [_item(id=f"m{i}", importance="medium", url="https://a.com/x") for i in range(5)]
    cards = [_card(str(i), "a.com") for i in range(5)]
    out, demoted = apply_source_fairness(med + cards, budget=3, floor=3)
    assert all(it.demoted is None for it in out if it.importance == "medium")
    assert sum(demoted.values()) == 2       # only cards were trimmed


def test_fairness_pass_disabled_when_budget_zero():
    items = [_card(str(i), "a.com") for i in range(10)]
    out, demoted = apply_source_fairness(items, budget=0, floor=3)
    assert demoted == {} and all(it.demoted is None for it in out)


def test_fairness_pass_is_deterministic_for_equal_ranks():
    # identical rank on every item: repeated runs must demote the same ones
    items = [_card(f"{h}-{i}", h) for h in ("a.com", "b.com") for i in range(4)]
    first = {it.id for it in apply_source_fairness(items, 3, 1)[0] if not it.demoted}
    for _ in range(5):
        again = {it.id for it in apply_source_fairness(items, 3, 1)[0] if not it.demoted}
        assert again == first


def test_fairness_pass_never_removes_an_item():
    items = [_card(str(i), "a.com") for i in range(9)]
    out, _ = apply_source_fairness(items, budget=2, floor=1)
    assert {it.id for it in out} == {str(i) for i in range(9)}
```

Add `collections` to the imports at the top of `tests/test_match.py`:

```python
import collections
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_match.py -k fairness -v`
Expected: FAIL with `ImportError: cannot import name 'apply_source_fairness'`

- [ ] **Step 3: Append the implementation to `radar/match.py`**

Add `replace` to the imports at the top of `radar/match.py`:

```python
from dataclasses import replace
```

Append:

```python
_CARD_TIER = "high"
_EXEMPT_SEVERITIES = ("high", "critical")


def apply_source_fairness(items: list[Item], budget: int, floor: int
                          ) -> tuple[list[Item], dict[str, int]]:
    """Decide which card-tier items keep their card when the tier is over budget.

    Returns `(items, {source_key: demoted count})`. Every input item comes back —
    losing this pass costs an item its card, not its place in the snapshot, so a
    demoted item is returned with `demoted="source_fairness"` set and nothing is
    discarded.

    Two phases once the tier exceeds `budget`:

    1. **Floor.** Every source is guaranteed its top `min(floor, group size)`
       items — a floor, not a cap. A source holding fewer than the floor simply
       keeps what it has; there is nothing to pad. When the floors together
       exceed the budget, they are filled level by level (every source's 1st
       item, then every source's 2nd) so the guarantee degrades evenly instead of
       the first few sources consuming every slot.
    2. **Merit.** The remaining budget goes to the best unreserved items
       globally, by `relevance_key`, ignoring source. This is the only place a
       source with deep, strong leftovers can take several slots in a row.

    Items with a high/critical `severity` are never demoted: they do consume
    budget, and they do satisfy their own source's floor, but a fairness rule must
    never bury a serious advisory. If they alone exceed the budget, all are still
    kept and the budget is overshot.

    Under budget this is a no-op regardless of how lopsided the sources are —
    diversity is enforced only under scarcity. `budget <= 0` disables it entirely.

    Requires unique `Item.id` across `items`; `fetch.dedupe` guarantees that.
    """
    if budget <= 0:
        return items, {}
    tier = [it for it in items
            if IMPORTANCE_ORDER[it.importance] >= IMPORTANCE_ORDER[_CARD_TIER]]
    if len(tier) <= budget:
        return items, {}

    groups: dict[str, list[Item]] = {}
    for it in tier:
        groups.setdefault(source_key(it.url), []).append(it)
    for group in groups.values():
        group.sort(key=relevance_key, reverse=True)

    keep = {it.id for it in tier if it.severity in _EXEMPT_SEVERITIES}
    # Best-item rank orders the sources; source_key breaks ties so the outcome is
    # reproducible for items that rank identically.
    order = sorted(groups, key=lambda k: (relevance_key(groups[k][0]), k), reverse=True)
    # Exempt items already banked for a source count toward its floor (per the
    # contract above), so only its not-yet-kept items are eligible to fill
    # whatever floor slots remain. Computed before the floor loop mutates `keep`,
    # so a non-exempt item can never inflate the exempt count.
    remaining = {k: [it for it in group if it.id not in keep]
                 for k, group in groups.items()}
    floor_quota = {k: max(0, floor - (len(groups[k]) - len(remaining[k])))
                   for k in groups}
    for level in range(max(0, floor)):
        for key in order:
            if len(keep) >= budget:
                break
            if level < floor_quota[key] and level < len(remaining[key]):
                keep.add(remaining[key][level].id)
    for it in sorted((x for x in tier if x.id not in keep),
                     key=relevance_key, reverse=True):
        if len(keep) >= budget:
            break
        keep.add(it.id)

    demoted: dict[str, int] = {}
    out: list[Item] = []
    for it in items:
        if (it.id in keep
                or IMPORTANCE_ORDER[it.importance] < IMPORTANCE_ORDER[_CARD_TIER]):
            out.append(it)
            continue
        key = source_key(it.url)
        demoted[key] = demoted.get(key, 0) + 1
        out.append(replace(it, demoted="source_fairness"))
    return out, demoted
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_match.py -k fairness -v`
Expected: 13 PASS

- [ ] **Step 5: Run the whole match suite and the full suite**

Run: `.venv/bin/pytest tests/test_match.py -v && .venv/bin/pytest -q`
Expected: both PASS. Nothing calls `apply_source_fairness` yet, so no behavior change.

- [ ] **Step 6: Commit**

```bash
git add radar/match.py tests/test_match.py
git commit -m "feat: add apply_source_fairness (floor + merit, demote not drop)

per_source_limit is a floor, not a cap: every source is guaranteed up to N
cards, a source with fewer keeps what it has, and the surplus budget goes to
the best leftovers globally rather than being rotated. Rotation survives only
inside the floor phase, and only so an over-subscribed floor degrades evenly.

Nothing is discarded — losers are marked demoted='source_fairness' and keep
their importance, so the snapshot never lies about what an item is. Under
budget the pass is a no-op no matter how lopsided the sources are."
```

---

### Task 6: Wire exclusion and fairness into `run_fetch`

**Files:**
- Modify: `radar/pipeline/fetch.py:1-11` (imports), `radar/pipeline/fetch.py:120-140` (the finalize block)
- Test: `tests/test_fetch.py`

**Interfaces:**
- Consumes: `exclusion_hit`, `category_matches`, `score_importance`, `stack_matches`, `apply_source_fairness` (Tasks 2-5); `Config.exclude` (Task 3); `Item.keyword_match` / `Item.demoted` (Task 1).
- Produces: `run_fetch(cfg, snapshot_path, *, now, client, force=False, fresh=False) -> dict` — unchanged signature. Finalize order becomes: exclusion → score → lookback + `min_keep` → `dedupe` → source fairness → category truncate.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_fetch.py`:

```python
_TWO_ITEM_FEED = """<?xml version="1.0"?>
<rss version="2.0"><channel><title>S</title>
  <item>
    <title>{t1}</title><link>{l1}</link><description>d</description>
    <pubDate>Wed, 16 Jul 2026 10:00:00 GMT</pubDate><guid>{l1}</guid>
  </item>
  <item>
    <title>{t2}</title><link>{l2}</link><description>d</description>
    <pubDate>Wed, 16 Jul 2026 09:00:00 GMT</pubDate><guid>{l2}</guid>
  </item>
</channel></rss>"""


def _cfg_ex(sources, exclude=None, general=None):
    base = {"lookback_days": 3650, "min_keep_importance": "low"}
    base.update(general or {})
    return Config(general=base, stack={}, categories=["backend"], sources=sources,
                  llm={}, exclude=exclude or {})


def test_run_fetch_drops_excluded_items(tmp_path):
    feed = _TWO_ITEM_FEED.format(t1="next.js v16.3.0-canary.97", l1="https://x/1",
                                 t2="next.js v16.2.12", l2="https://x/2")

    def handler(req):
        return httpx.Response(200, text=feed)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    cfg = _cfg_ex([{"type": "rss", "category": "backend", "url": "https://f/feed"}],
                  exclude={"backend": ["canary"]})
    snap = run_fetch(cfg, tmp_path / "ex.json",
                     now=datetime(2026, 7, 17, tzinfo=timezone.utc),
                     client=client, fresh=True)
    titles = [it["title"] for it in snap["items"]]
    assert titles == ["next.js v16.2.12"]   # the canary release never reached disk


def test_run_fetch_records_keyword_match(tmp_path):
    feed = _TWO_ITEM_FEED.format(t1="New Claude model", l1="https://x/1",
                                 t2="unrelated musings", l2="https://x/2")

    def handler(req):
        return httpx.Response(200, text=feed)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    cfg = Config(general={"lookback_days": 3650, "min_keep_importance": "low"},
                 stack={}, categories=["backend"], sources=[
                     {"type": "rss", "category": "backend", "url": "https://f/feed"}],
                 llm={}, category_keywords={"backend": ["claude"]})
    snap = run_fetch(cfg, tmp_path / "kw.json",
                     now=datetime(2026, 7, 17, tzinfo=timezone.utc),
                     client=client, fresh=True)
    by_title = {it["title"]: it for it in snap["items"]}
    assert by_title["New Claude model"]["keyword_match"] == ["claude"]
    assert by_title["New Claude model"]["importance"] == "high"
    assert by_title["unrelated musings"]["keyword_match"] == []


def test_run_fetch_demotes_over_budget_without_dropping(tmp_path):
    feed = _TWO_ITEM_FEED.format(t1="Claude one", l1="https://only.example/1",
                                 t2="Claude two", l2="https://only.example/2")

    def handler(req):
        return httpx.Response(200, text=feed)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    # both items score high via the keyword; budget 1 forces one demotion
    cfg = Config(general={"lookback_days": 3650, "min_keep_importance": "low",
                          "max_card_items": 1, "per_source_limit": 1},
                 stack={}, categories=["backend"], sources=[
                     {"type": "rss", "category": "backend", "url": "https://f/feed"}],
                 llm={}, category_keywords={"backend": ["claude"]})
    snap = run_fetch(cfg, tmp_path / "fair.json",
                     now=datetime(2026, 7, 17, tzinfo=timezone.utc),
                     client=client, fresh=True)
    assert len(snap["items"]) == 2                                  # nothing dropped
    demoted = [it for it in snap["items"] if it["demoted"]]
    assert len(demoted) == 1
    assert demoted[0]["demoted"] == "source_fairness"
    assert demoted[0]["importance"] == "high"                       # importance intact


def test_run_fetch_does_not_demote_when_within_budget(tmp_path):
    feed = _TWO_ITEM_FEED.format(t1="Claude one", l1="https://only.example/1",
                                 t2="Claude two", l2="https://only.example/2")

    def handler(req):
        return httpx.Response(200, text=feed)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    cfg = Config(general={"lookback_days": 3650, "min_keep_importance": "low",
                          "max_card_items": 30, "per_source_limit": 3},
                 stack={}, categories=["backend"], sources=[
                     {"type": "rss", "category": "backend", "url": "https://f/feed"}],
                 llm={}, category_keywords={"backend": ["claude"]})
    snap = run_fetch(cfg, tmp_path / "under.json",
                     now=datetime(2026, 7, 17, tzinfo=timezone.utc),
                     client=client, fresh=True)
    # one source holds both cards, but the tier is under budget -> untouched
    assert all(it["demoted"] is None for it in snap["items"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_fetch.py -k "excluded or keyword_match or demote or within_budget" -v`
Expected: FAIL — `_cfg_ex` passes `exclude=` (accepted after Task 3) but `run_fetch` ignores it, so the canary item survives; `keyword_match` stays `[]`; nothing is demoted.

- [ ] **Step 3: Update the imports**

Replace `radar/pipeline/fetch.py` lines 1-11 with:

```python
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
```

- [ ] **Step 4: Replace the finalize block**

Replace `radar/pipeline/fetch.py:120-140` — everything from the `# finalize: importance, lookback, hard-cut, rank` comment through the closing `return snap` — with:

```python
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
                     keyword_match=category_matches(it, cfg.category_keywords))
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_fetch.py -v`
Expected: all PASS (4 new + 6 pre-existing).

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add radar/pipeline/fetch.py tests/test_fetch.py
git commit -m "feat: run exclusion and source fairness in fetch finalize

Finalize order becomes exclude -> score -> lookback + min_keep -> dedupe ->
fairness -> truncate. Exclusion runs first so an excluded item is never
scored; fairness runs after dedupe so duplicate copies cannot consume a
source's share, and before truncation which would otherwise have already cut.

Also records keyword_match on every kept item, and breaks the summary log's
'dropped' count down by cause. A bare total gives no way to tune the config,
which is the whole point of a keyword list you have to iterate on."
```

---

### Task 7: `render` and `enrich` honor `demoted`

**Files:**
- Modify: `radar/pipeline/render.py:161`, `radar/pipeline/enrich.py:86-87`
- Test: `tests/test_render.py`, `tests/test_enrich.py`

**Interfaces:**
- Consumes: `Item.demoted` (Task 1), set by `run_fetch` (Task 6).
- Produces: `render._group` routes a demoted item to `also_noted` regardless of `min_display_importance`; `enrich.run_enrich` excludes demoted items from the eligible set.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_render.py`:

```python
def test_demoted_high_item_renders_under_also_noted(tmp_path):
    out = tmp_path / "output"
    kept = Item(id="1", title="Kept Card", url="https://x/1", source_type="rss",
                category="backend", published=NOW, summary="s", importance="high")
    # clears min_display_importance="high" but lost the fairness pass
    demoted = Item(id="2", title="Demoted Item", url="https://x/2", source_type="rss",
                   category="backend", published=NOW, summary="s",
                   importance="high", demoted="source_fairness")
    grouped = _group(_snap_with([kept, demoted]), _cfg())
    bucket = grouped["tech"]["backend"]
    assert [c["title"] for c in bucket["cards"]] == ["Kept Card"]
    assert [c["title"] for c in bucket["also_noted"]] == ["Demoted Item"]

    snap_path = _write_snap(tmp_path, _snap_with([kept, demoted]))
    run_render(_cfg(), snap_path, out, force=True)
    digest = (out / "digests" / "2026-07-17.html").read_text()
    assert "Demoted Item" in digest and "Also noted" in digest


def test_demoted_item_is_not_counted_as_a_card(tmp_path):
    demoted = Item(id="1", title="D", url="https://x/1", source_type="rss",
                   category="backend", published=NOW, summary="s",
                   importance="high", demoted="source_fairness")
    t = _tally(_group(_snap_with([demoted]), _cfg())["tech"])
    assert t["total"] == 0
```

Add to `tests/test_enrich.py`:

```python
def _demoted(id):
    return Item(id=id, title="t", url="u", source_type="rss", category="backend",
                published=NOW, summary="s", importance="high",
                demoted="source_fairness")


def test_run_enrich_skips_demoted_items(tmp_path):
    p = tmp_path / "s.json"
    atomic_write_json(p, _snap([_high("keep"), _demoted("skip")]))
    payload = json.dumps({"keep": {"summary": "S"}, "skip": {"summary": "S"}})
    fp = FakeProvider(payload)
    run_enrich(_cfg(), p, provider=fp)
    saved = {it["id"]: it for it in load_snapshot(p)["items"]}
    assert saved["keep"]["llm"]["summary"] == "S"
    # "also noted" renders only a title link and a date, so enriching a demoted
    # item spends tokens on output that is never displayed
    assert saved["skip"].get("llm") is None


def test_run_enrich_noop_when_every_eligible_item_is_demoted(tmp_path):
    p = tmp_path / "s.json"
    atomic_write_json(p, _snap([_demoted("a"), _demoted("b")]))
    fp = FakeProvider(json.dumps({"a": {"summary": "S"}}))
    run_enrich(_cfg(), p, provider=fp)
    assert fp.calls == 0   # no LLM call at all
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_render.py -k demoted tests/test_enrich.py -k demoted -v`
Expected: FAIL — the demoted item is still counted as a card (`also_noted` empty, `t["total"] == 1`) and is still enriched (`fp.calls == 1`).

- [ ] **Step 3: Route demoted items to "also noted"**

In `radar/pipeline/render.py`, replace line 161:

```python
        if IMPORTANCE_ORDER[it["importance"]] >= threshold:
```

with:

```python
        # A demoted item cleared the importance threshold but lost the fetch-stage
        # source-fairness pass, so it belongs in "also noted" regardless.
        if IMPORTANCE_ORDER[it["importance"]] >= threshold and not it.get("demoted"):
```

- [ ] **Step 4: Exclude demoted items from enrichment**

In `radar/pipeline/enrich.py`, replace lines 86-87:

```python
    eligible = [it for it in items if importance_ge(it["importance"], "high")
                and (force or not it.get("llm"))][:cap]
```

with:

```python
    # Demoted items render as a bare title link and date in "also noted"
    # (templates/digest.html.j2), so enriching them would spend tokens on output
    # nothing displays.
    eligible = [it for it in items if importance_ge(it["importance"], "high")
                and not it.get("demoted")
                and (force or not it.get("llm"))][:cap]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_render.py tests/test_enrich.py -v`
Expected: all PASS (4 new + all pre-existing).

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add radar/pipeline/render.py radar/pipeline/enrich.py tests/test_render.py tests/test_enrich.py
git commit -m "feat: honor Item.demoted in render and enrich

render._group routes a demoted item to 'also noted' even though it clears
min_display_importance, and enrich drops demoted items from the eligible set:
the also-noted template renders only a title link and a date, so enriching
one spends tokens on output that is never displayed."
```

---

### Task 8: Example config and documentation

**Files:**
- Modify: `config/radar.example.toml:5-11` (`[general]`), `config/radar.example.toml:20-28` (`[category_keywords]` comment), and add an `[exclude]` table after `[category_keywords]`
- Modify: `docs/domain-models.md`, `docs/project-overview.md`

**Interfaces:**
- Consumes: every config key introduced in Tasks 3-6 — `[exclude]`, `max_card_items`, `per_source_limit`.
- Produces: no code. A user can configure the feature, and the docs describe the new `Item` fields and pipeline steps.

- [ ] **Step 1: Add the two `[general]` keys**

In `config/radar.example.toml`, replace the `[general]` block (lines 5-11) with:

```toml
[general]
title = "Kdan Tech Radar"
timezone = "Asia/Taipei"
max_items_per_category = 15
lookback_days = 7
min_keep_importance = "medium"      # low | medium | high | critical
min_display_importance = "high"

# Source fairness. `max_card_items` budgets the card tier (high/critical items);
# under budget the pass is a no-op no matter how lopsided the sources are, so a
# quiet week is never trimmed. `per_source_limit` is a FLOOR, not a cap: each
# source is guaranteed up to that many cards, a source holding fewer keeps what
# it has, and the surplus budget goes to the best leftovers globally. Items that
# fit neither are demoted to "also noted", never discarded. Set max_card_items
# to 0 to disable. A high/critical severity is never demoted.
max_card_items   = 30
per_source_limit = 3
```

- [ ] **Step 2: Drop the obsolete short-keyword warning**

In `config/radar.example.toml`, the `[category_keywords]` comment block ends with this line, which word-boundary matching has made obsolete:

```
# Keep terms distinctive substrings (no bare "ai" — matches "email"/"detail").
```

Replace it with:

```
# Terms match on word boundaries, so short words are safe: "ai" matches "AI"
# but not "email" or "available".
```

- [ ] **Step 3: Add the `[exclude]` table**

Insert into `config/radar.example.toml` immediately after the `[category_keywords]` block (after the `"neural network", ... "hugging face"]` line, before the `# `board` routes an item...` comment):

```toml
# Exclusion keywords: an item matching any applicable term is dropped at fetch
# and never written to the snapshot. `global` applies everywhere; a category key
# applies only within that category.
#
# Scoping is load-bearing. `preview` is pure noise on a frontend release feed
# but names a real product stage in a cloud announcement — globally it would kill
# "PostgreSQL 19 Beta 2 is now available in Amazon RDS Database Preview
# Environment". Same for the broad words in `ai` below: `community` and `program`
# would shred legitimate cloud and devops items if they were global.
#
# The `ai` list is the corporate-PR filter and is where this pays off most.
# Measured on the 2026-07-27 snapshot it takes openai.com from 9 card-tier items
# to 5, with every survivor substantive. Do NOT add "partner to address" — it
# killed "OpenAI and Hugging Face partner to address security incident", a real
# security story.
[exclude]
global   = ["fireside chat", "joins the board"]
frontend = ["canary", "preview", "nightly"]
devops   = ["beta", "alpha"]
ai       = ["small business", "joins the boards", "join the boards",
            "board of directors", "national science", "news organizations",
            "community", "program", "anniversary"]
```

- [ ] **Step 4: Verify the example config loads**

Run:

```bash
.venv/bin/python -c "
from radar.config import load_config
cfg = load_config('config/radar.example.toml')
print('exclude keys:', sorted(cfg.exclude))
print('max_card_items:', cfg.general['max_card_items'])
print('per_source_limit:', cfg.general['per_source_limit'])
"
```

Expected: `exclude keys: ['ai', 'devops', 'frontend', 'global']`, `max_card_items: 30`, `per_source_limit: 3`. A `ConfigError` here means an `[exclude]` key is not in `categories`.

- [ ] **Step 5: Document the new `Item` fields and config table**

In `docs/domain-models.md`, add `keyword_match` and `demoted` to the `Item` field table, and `[exclude]` plus the two `[general]` keys to the configuration model section. Use these descriptions:

- `keyword_match: list[str]` — which `[category_keywords]` terms admitted this item. The per-category counterpart to `stack_match`; together they make an item's `high` score explainable.
- `demoted: str | None` — why this item lost its card, or `None`. Currently only `"source_fairness"`. Set at fetch; read by render (routes to "also noted") and enrich (skips it). Deliberately does not alter `importance`, so the snapshot never misstates what an item is.
- `[exclude]` — `{"global": [str], "<category>": [str]}`. Word-boundary terms; a match drops the item at fetch. Validated at load: keys must be `"global"` or a declared category, values lists of non-empty strings, and `global` is reserved as a category name.
- `[general].max_card_items` (default 30) — card-tier budget; `0` disables source fairness. `[general].per_source_limit` (default 3) — per-source floor.

- [ ] **Step 6: Document the new fetch steps**

In `docs/project-overview.md`, update §3's pipeline diagram and §5's `fetch` row so the fetch stage reads:

```
adapters → normalize → exclude → score importance → lookback filter
→ hard cut → dedupe → source fairness (demote) → rank/truncate
```

Add a sentence to §5 noting that `demoted` is set at fetch but consumed by render and enrich, so those two stages depend on a field fetch owns.

- [ ] **Step 7: Run the full suite one last time**

Run: `.venv/bin/pytest -q`
Expected: PASS.

- [ ] **Step 8: Verify the spec's acceptance numbers against the real snapshot**

This reproduces the whole finalize chain over `output/data/2026-07-27.json` using the shipped example config's `[exclude]` and checks the four numbers the spec commits to.

```bash
.venv/bin/python - <<'PY'
import json, collections
from dataclasses import replace
from radar.config import load_config
from radar.item import Item, IMPORTANCE_ORDER
from radar.match import (apply_source_fairness, category_matches, exclusion_hit,
                         relevance_key, score_importance, source_key, stack_matches)
from radar.pipeline.fetch import dedupe, importance_ge, rank_and_truncate
from radar.store import item_from_dict

cfg = load_config('config/radar.example.toml')
raw = json.load(open('output/data/2026-07-27.json'))['items']
before_cards = sum(1 for d in raw if d['importance'] in ('high', 'critical'))

kept, excluded = [], 0
for d in raw:
    it = item_from_dict(d)
    if exclusion_hit(it, cfg.exclude) is not None:
        excluded += 1
        continue
    it = replace(it, importance=score_importance(it, cfg.stack, cfg.category_keywords),
                 stack_match=stack_matches(it, cfg.stack),
                 keyword_match=category_matches(it, cfg.category_keywords))
    if importance_ge(it.importance, cfg.general.get('min_keep_importance', 'medium')):
        kept.append(it)

tier = sum(1 for it in kept if IMPORTANCE_ORDER[it.importance] >= IMPORTANCE_ORDER['high'])
fair, demoted = apply_source_fairness(
    dedupe(kept), cfg.general['max_card_items'], cfg.general['per_source_limit'])
final = rank_and_truncate(fair, cfg.categories, cfg.general['max_items_per_category'])
cards = [it for it in final
         if IMPORTANCE_ORDER[it.importance] >= IMPORTANCE_ORDER['high'] and not it.demoted]

print(f"excluded ............ {excluded}   (expect 15)")
print(f"card-tier candidates  {tier}   (expect 36)")
print(f"cards ............... {before_cards} -> {len(cards)}   (expect 51 -> 30)")
print(f"also noted .......... {len(final) - len(cards)}   (expect 38)")
print(f"demotions ........... {sum(demoted.values())} {dict(demoted)}"
      f"   (expect 6: simonwillison.net 4, openai.com 2)")
hf = [it for it in final if 'Hugging Face partner' in it.title]
print(f"over-block canary ... {'SURVIVED' if hf else 'WRONGLY EXCLUDED'}"
      f"   (expect SURVIVED)")
gr = [it for it in final if 'Guardrails' in it.title]
print(f"Guardrails stack .... {gr[0].stack_match if gr else 'missing'}"
      f"   (expect ['docker'])")
PY
```

Expected output:

```
excluded ............ 15   (expect 15)
card-tier candidates  36   (expect 36)
cards ............... 51 -> 30   (expect 51 -> 30)
also noted .......... 38   (expect 38)
demotions ........... 6 {'simonwillison.net': 4, 'openai.com': 2}   (expect 6: ...)
over-block canary ... SURVIVED   (expect SURVIVED)
Guardrails stack .... ['docker']   (expect ['docker'])
```

A mismatch here means the implementation diverges from the spec — investigate before committing.

- [ ] **Step 9: Commit**

```bash
git add config/radar.example.toml docs/domain-models.md docs/project-overview.md
git commit -m "docs: seed exclusion keywords and document the new config keys

Adds [exclude] with the phrases measured against real data, max_card_items
and per_source_limit, and drops the 'no bare ai' warning that only existed
because matching used substrings.

The comments carry the three findings that are expensive to rediscover: why
'preview' and the broad ai words are per-category rather than global, why
per_source_limit is a floor rather than a cap, and why 'partner to address'
must stay out (it killed a real security story)."
```

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
|---|---|
| §1 new module `radar/match.py`, `fetch.py` keeps orchestration | Task 2 Steps 3, 5 |
| §1 `term_hits` order + dedupe | Task 2 (`term_hits`), test `term_listed_in_two_stack_lists_is_reported_once` |
| §1 `exclusion_hit` | Task 3 |
| §1 `source_key`, `relevance_key` | Task 4 |
| §1 `apply_source_fairness` | Task 5 |
| §2 lookarounds not `\b`, compiled-pattern cache | Task 2 Step 3 (`_pattern` + `lru_cache`) |
| §2 uniform boundaries over title+summary+url | Task 2 (`haystack`) |
| §2 `stack_matches` dedupes overlapping config lists | Task 2 |
| §3 `[exclude]` table, semantics, drop-at-fetch | Task 3 (matcher), Task 6 (drop), Task 8 (seeded config) |
| §3 validation: unknown key, non-list, empty string, reserved `global` | Task 3 Step 4 + 6 config tests |
| §3 `max_card_items` / `per_source_limit` coerced at use, no load validation | Task 6 Step 4 (`max(0, int(...))`) |
| §4 no fixed cap; no-op under budget | Task 5 test `fairness_pass_demotes_nobody_when_under_budget` |
| §4 scope = high/critical only | Task 5 test `fairness_pass_ignores_medium_items` |
| §4 floor phase, no padding | Tasks 5 tests `guarantees_floor_to_every_source`, `does_not_pad_a_source_holding_fewer_than_the_floor` |
| §4 merit phase ignores source | Task 5 test `allocates_surplus_by_merit_ignoring_source` |
| §4 level-by-level fill when floors exceed budget | Task 5 test `fills_floors_level_by_level_when_floors_exceed_budget` |
| §4 deterministic tie-break | Task 5 test `is_deterministic_for_equal_ranks` |
| §4 `source_key` rules + `"(unknown)"` fallback, never raises | Task 4 tests |
| §4 exemption: never demoted, consumes budget, satisfies floor | Task 5 three exempt tests |
| §5 `Item.demoted` reason string, `importance` untouched | Task 1, Task 6 test `demotes_over_budget_without_dropping` |
| §5 render routes demoted to also-noted | Task 7 Step 3 |
| §5 enrich skips demoted | Task 7 Step 4 |
| §6 finalize order | Task 6 Step 4 |
| §6 fairness changes no snapshot membership | Task 5 test `never_removes_an_item`, Task 6 test asserting `len == 2` |
| §7 `keyword_match`, no `store.py` change, old snapshots load | Task 1 Steps 3, 5 |
| §8 logging: excluded terms, demotions, no-op line, dropped by cause | Task 6 Step 4 |
| §9 example config + `docs/domain-models.md` + `docs/project-overview.md` | Task 8 |
| Acceptance table (15 excluded / 36 tier / 30 cards / 38 also-noted / 6 demotions) | Task 8 Step 8 |
| Over-blocking regression (`partner to address`) | Task 8 Step 3 comment + Step 8 canary check |

Out of scope and deliberately absent: displaying `keyword_match` on the card (spec §7 marks it out of scope), and everything in the deferred LLM spec.

**Placeholder scan:** One found and removed — Task 3 Step 1 briefly contained a broken `test_exclude_table_loads` sketch referencing an undefined `_TMP`, followed by prose discarding it. Both are gone; only the real test remains. No TBD/TODO anywhere, no "add validation" or "handle edge cases" without code, and every code step shows complete content.

**Type consistency:**
- `term_hits(terms: list[str], text: str) -> list[str]` — defined Task 2, called by `stack_matches`, `category_matches`, `exclusion_hit` (Task 3) with the same argument order.
- `haystack(it: Item) -> str` — Task 2, used by Task 3's `exclusion_hit`.
- `score_importance(it, stack, category_keywords=None)` — three-arg form with a default, matching both the existing two-arg calls in `test_match.py` and the three-arg call in `run_fetch` (Task 6).
- `source_key(url: str) -> str` — Task 4; called in Task 5 and in Task 8's verification script with a URL string, never an `Item`.
- `relevance_key(it: Item) -> tuple` — Task 4; consumed by `fetch._rank_key` (Task 4 Step 4) and as a `sort`/`sorted` key in Task 5.
- `apply_source_fairness(items, budget, floor) -> (list[Item], dict[str, int])` — Task 5; unpacked as `fair, demoted` in Task 6 and `fair, demoted` in Task 8's script.
- `exclusion_hit(it, exclude) -> str | None` — Task 3; Task 6 tests `is not None` rather than truthiness, so an empty-string term could not be silently ignored (config validation rejects those anyway).
- `Item.keyword_match` / `Item.demoted` — Task 1; read as dict keys `it["keyword_match"]` / `it.get("demoted")` in Tasks 6 and 7 because those stages work on serialized dicts, and as attributes in Tasks 4, 5 and 8 where `Item` instances are in hand. Both spellings are correct for their context.
- `_CARD_TIER = "high"` (Task 5) and `importance_ge(it["importance"], "high")` (Task 7's enrich filter) refer to the same threshold; enrich's literal is pre-existing and left as-is rather than coupling enrich to `match`.

One consistency note worth flagging to the reviewer: Task 4 changes `_rank_key` to include the match count, which also affects `rank_and_truncate`. On real data that is not a behavior change — `rank_and_truncate` currently truncates nothing (68 items in, 68 out at `max_items_per_category = 20`) — but it is a genuine widening of scope beyond the fairness pass, done so the pipeline has one notion of rank rather than two that diverge.