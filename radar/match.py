"""Pure item-matching rules: keyword matching, importance scoring, exclusion,
and source-fairness allocation.

No I/O, no logging, no snapshot awareness — everything here is a function of its
arguments, so the rules can be tested offline without fetch scaffolding.
"""
from __future__ import annotations
import re
from dataclasses import replace
from functools import lru_cache
from urllib.parse import urlsplit
from radar.item import Item, IMPORTANCE_ORDER


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
    # Best-item rank orders the sources; source_key breaks ties, descending, so the
    # outcome is reproducible for items that rank identically.
    order = sorted(groups, key=lambda k: (relevance_key(groups[k][0]), k), reverse=True)
    # Exempt items already banked for a source count toward its floor (per the
    # contract above), so only its not-yet-kept items are eligible to fill
    # whatever floor slots remain.
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
