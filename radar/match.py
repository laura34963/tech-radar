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


def score_importance(it: Item, stack: dict,
                     category_keywords: dict | None = None) -> str:
    if it.severity:
        return it.severity
    if stack_matches(it, stack) or category_matches(it, category_keywords):
        return "high"
    if it.source_type in ("github", "cloud", "social", "registry"):
        return "medium"
    return "low"
