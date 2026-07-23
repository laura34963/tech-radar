# 技術資訊 / 新聞 雙板塊 digest — 設計

- **Date:** 2026-07-23
- **Status:** Approved (brainstorm)
- **Scope:** Split the weekly digest into two boards — 技術資訊 (tech) and 新聞
  (news) — so they are never shown mixed in one view. Single file per date,
  CSS-only tabs, each board self-contained.

## Problem

The digest (`digests/<date>.html`) currently renders every item on one page,
grouped only by category (backend/frontend/devops/cloud/security). Official
release/advisory content and industry/threat-intel news are interleaved, which
the user considers unsuitable for a single view.

## Decisions (from brainstorm)

1. **Boundary — by source nature**, derived at render time (no config, no fetch,
   no schema change).
2. **Layout — one file per date, two zones behind CSS-only tabs**, default tab is
   技術資訊. Visually only one board shows at a time.
3. **KPI / Priority — one set per tab.** Each board computes its own KPI tally and
   ⚠ Priority Intelligence list from only its own items.
4. **Archive index — left unchanged** this round (still one `digests/<date>.html`
   link per row, existing `count` + `security` glance).

## A. Classification

Add a pure helper in `radar/pipeline/render.py`, reading only fields already on
each serialized item (`source_type`, `category`):

```python
def _section(it: dict) -> str:
    """Which board an item belongs to, by source nature."""
    st = it.get("source_type")
    if st == "social":                                    # HN / Reddit
        return "news"
    if st == "rss" and it.get("category") == "security":  # THN/Krebs/SANS/Unit42/Talos/DFIR/CISA/Reddit-rss
        return "news"
    return "tech"                                         # github / cloud / registry / security-advisory / official blog rss
```

Verified against the current `config/radar.toml` sources — every source maps
cleanly:

| Source                                              | source_type | category | section |
|-----------------------------------------------------|-------------|----------|---------|
| rails/rails, golang/go, facebook/react, vercel/next.js, k8s, moby | github | * | tech |
| Rails / Go / Next.js / nginx / Docker / K8s blogs   | rss         | backend/frontend/devops | tech |
| AWS What's New                                      | cloud       | cloud    | tech    |
| OSV, GHSA advisories                                | security    | security | tech    |
| CISA advisories (`.xml`)                            | rss         | security | news    |
| THN / SANS ISC / Krebs / Unit42 / Talos / DFIR      | rss         | security | news    |
| Reddit r/netsec (`.rss`)                            | rss         | security | news    |
| Hacker News stories                                 | social      | *        | news    |

Note recorded so future sources land correctly: an RSS feed in a **non-security**
category is treated as an official blog (tech); a security-category RSS feed is
treated as news. Advisory feeds use `source_type == "security"` and stay tech.

## B. Render pipeline (`radar/pipeline/render.py`)

- `_group(snapshot, cfg)` → returns
  `{"tech": {cat: {cards, also_noted}}, "news": {cat: {...}}}`. It first routes
  each de-duplicated item through `_section()`, then buckets by category and
  applies the existing display-importance threshold + severity sort within each
  board.
- `_tally(section_grouped)` and `_priority(section_grouped)` now take a single
  board's `{cat: {cards, also_noted}}` mapping (same shape as today's `grouped`),
  so their internals are unchanged — only the caller passes one board at a time.
- `render_digest()` computes `grouped = _group(...)`, then for each of `tech` and
  `news` computes its own tally and priority, and passes
  `sections = {"tech": {...}, "news": {...}}` (each with `grouped/tally/priority`)
  plus the ordered category list to the template.

## C. Template (`radar/templates/digest.html.j2`)

- **Shared header** stays on top: kicker, `情資週報`, byline, period, and the
  overall `digest_summary` lede (a whole-week summary spanning both boards).
- **CSS-only tabs:** two hidden `<input type="radio" name="board">` with two
  `<label>` tabs — `技術資訊` and `新聞`. The tech radio is `checked` by default.
- **Two `.tab-panel`s**, one per board, each containing that board's own:
  KPI row → ⚠ Priority Intelligence → By Category (reusing the existing card,
  also-noted, and priority-row markup, only fed the board's data).
- **Empty board:** if a board has no items that week, its tab still renders and
  its panel shows a `本區本週無情資` placeholder (no KPI/priority/category blocks).

## D. Styles (`radar/templates/styles.css`)

- Add `.tabs` (radio-hack) and `.tab-panel` show/hide rules driven by
  `input:checked` sibling selectors — no JS on the digest page.
- Reuse existing KPI / priority / card styles unchanged. Tab chrome matches the
  existing intelligence-brief look (topbar/wordmark).

## E. Archive index — unchanged

`index.html` still links one `digests/<date>.html` per row; opening it lands on
the default 技術資訊 tab. No per-board counts this round (deferred).

## F. Testing

- Update existing render tests for the new `_group()` return shape and the
  `sections` template context.
- New unit tests:
  - `_section()` classification across `source_type` × `category` combinations
    (github/cloud/registry/security → tech; social → news; rss+security → news;
    rss+non-security → tech).
  - `_tally` / `_priority` count only their own board's items.
  - Rendered digest contains both tab labels, defaults the tech radio to
    `checked`, and shows the empty-board placeholder when a board is empty.

## Out of scope

- No config, fetch, adapter, or item-schema changes.
- No archive-index per-board counts (deferred).
- No new dependencies; digest page stays JS-free.
