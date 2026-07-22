# 資訊情資中心 (Intelligence Brief) UI redesign

**Date:** 2026-07-22
**Status:** Approved for planning

## Problem

The rendered digest today reads as an editorial magazine (serif display font,
"What changed in your stack" hero, severity-tinted cards). The goal is a more
professional, clear **intelligence-center** presentation — an executive
intelligence brief that surfaces what matters first and reads as an operational
report, not a magazine.

## Constraints (unchanged)

- Static, self-contained HTML site. No build step, no framework.
- Rendering stays Jinja2 templates + one `styles.css`; only vanilla JS.
- Theme-aware (light + dark via `prefers-color-scheme`).
- Content is Traditional Chinese; deploys to GitHub Pages.
- **Same source data — no fields dropped.** The fetch / enrich / rank pipeline is
  untouched. This is a presentation change plus a small render-context addition.

## Scope

Files changed:

- `radar/templates/digest.html.j2` — per-week digest page
- `radar/templates/index.html.j2` — archive hub
- `radar/templates/styles.css` — shared stylesheet
- `radar/pipeline/render.py` — pass two precomputed values to the digest template
  (severity tally + priority list) so display logic stays out of Jinja

Out of scope: adapters, fetch, enrich, ranking, grouping thresholds, config schema.

## Visual language

- **Drop the magazine serif** (`--font-display` Iowan/Palatino). Headings and body
  both use the existing sans stack. Professional, not editorial.
- **Monospace for KPI tile numbers only** (`--font-mono`), for a dashboard read
  without hurting body scannability.
- **Keep the existing severity palette** (critical=red, high=orange, medium=amber,
  low=slate) and the light/dark token set. Apply it consistently across tiles,
  tags, and card left-borders.
- More "report" surface treatment: a solid header band, uppercase micro-labels as
  section dividers, tighter vertical rhythm.

## Digest page layout (top → bottom)

1. **Top bar** (sticky, as today) — brand + `INTELLIGENCE BRIEF` wordmark +
   Home / ← prev / next → navigation.
2. **Brief header** — kicker `WEEKLY INTELLIGENCE BRIEF · WEEK {{week}}, {{year}}`,
   a clear headline, the date range, and `snapshot.digest_summary` rendered as an
   executive-summary lede (only when present).
3. **KPI tile row** — `TOTAL · CRITICAL · HIGH · MEDIUM · LOW` severity counts
   (mono numbers, color-coded by level), followed by a secondary row of per-category
   count chips (as today). At-a-glance situational read.
4. **⚠ PRIORITY INTELLIGENCE** — auto-surfaced compact rows for every critical +
   high item across all categories:
   `[SEV] [category] title · one-line summary · date · link`.
   Rendered only when at least one such item exists. Priority items **also** appear
   as full cards in their category below — a briefing intentionally repeats the
   "read first" items.
5. **BY CATEGORY** sections (collapsible `<details>`, as today) — refined cards:
   - severity left-border + severity tag
   - meta row: provider / stack / published date
   - title (link) + summary
   - **Why it matters** and **Recommended action** shown inline by default
     (decision-relevant)
   - long **detail** behind a `展開詳細` expander
   - **also noted** collapsed list (as today)

## Index page layout

Same visual system:

- Hero → "Kdan Tech Radar · Intelligence Archive".
- Latest digest as a prominent card showing its glance (total / high / security).
- Keep the filter search (`oninput` vanilla JS).
- Restyle digest rows to match (denser, with a severity indicator).

## Render-context change (`render.py`)

`render_digest` computes and passes to `digest.html.j2`:

- `tally` — `{"total": int, "critical": int, "high": int, "medium": int, "low": int}`,
  counting each displayed card by its effective level (`severity or importance`).
- `priority` — the flattened list of displayed cards whose effective level is
  `critical` or `high`, sorted highest-severity first, each retaining its
  `category` so the priority row can label it.

Both are derived from the same `grouped` structure already built by `_group`, so no
new data source and no change to what counts as a card vs. "also noted".

## Testing

- Existing render tests must still pass (`python -m pytest -q`).
- Add/extend a render test asserting: `tally` totals match the displayed card count
  and per-level sums; `priority` contains exactly the critical+high displayed cards
  in severity order; the priority block is omitted when there are none.
- Manual check: re-render the existing `2026-07-22` snapshot and open
  `output/index.html` + the digest in light and dark mode.

## Non-goals / left alone

- No change to which items are shown (the `min_display_importance` threshold and
  dedupe stay as-is).
- No new dependencies, no JS framework, no external assets.
