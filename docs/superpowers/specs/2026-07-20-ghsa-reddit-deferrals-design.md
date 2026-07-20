# GHSA feed + Reddit source — design

> **Date:** 2026-07-20
> **Status:** Approved, implementing
> **Deferrals resolved:** [`docs/integrations.md` §5](../../integrations.md#5-deferred-integrations) — "GHSA / NVD security feeds" (GHSA only) and "Reddit JSON"

Two independent extensions to existing adapters. Both dispatch on the existing
discriminator field (`security.feed`, `social.source`) — no new source types.

## 1. GHSA security feed — `radar/adapters/security.py`

Add `feed = "ghsa"` alongside `feed = "osv"`. `fetch()` becomes a dispatcher;
the existing OSV logic moves verbatim into `_fetch_osv`.

- **Endpoint:** `GET https://api.github.com/advisories` (verified against GitHub
  REST docs), params `ecosystem`, `affects` (comma-joined package list), `per_page=100`.
  Optional `GITHUB_TOKEN` Bearer, `Accept: application/vnd.github+json` — same as
  the github adapter.
- **Requests:** one per ecosystem (all packages OR-ed via `affects`), fewer than
  OSV's package×ecosystem cross-product. Per-ecosystem `try/except` isolation.
  Dedupe by `ghsa_id`.
- **Ecosystem names:** GitHub uses different names than OSV (`pypi`→`pip`,
  `crates.io`→`rust`, `packagist`→`composer`, `hex`→`erlang`). New
  `_GHSA_ECOSYSTEM` map; unmappable ecosystems are skipped. Requires both
  `[stack].packages` and mappable `ecosystems`, else warn + return `[]` (mirrors OSV).
- **Severity:** advisory `severity` ∈ {critical,high,medium,low,unknown}; passthrough
  for the first four, `unknown`→`medium` (so it still surfaces, matching OSV's default).
- **Item:** `id=item_id(ghsa_id)`, `title=f"{ghsa_id}: {pkg}"`, `severity`,
  `stack_match`=our packages matched against `vulnerabilities[].package.name`,
  `published`=`published_at`, `url`=`html_url`, `summary`=`summary`|`description` [:500].
- **NVD stays deferred:** its keyword search is not ecosystem/package-aware and
  returns heavy noise that OSV already covers precisely. Documented in §5.

## 2. Reddit source — `radar/adapters/social.py`

Add `source = "reddit"` alongside `source = "hn"`. `fetch()` becomes a dispatcher;
existing HN logic moves into `_fetch_hn`.

- **Config:** `subreddit` required (validated at fetch time — social's config
  discriminator is `source`, sub-fields are checked in the adapter, as OSV does).
  Optional `query`, optional `min_points` (against `score`).
- **Endpoint:** with `query` → `https://www.reddit.com/r/{sub}/search.json`
  (`q`, `restrict_sr=1`, `sort=new`, `limit=50`); else
  `https://www.reddit.com/r/{sub}/new.json` (`limit=50`). Sends a descriptive
  `User-Agent` (Reddit 429s the default httpx UA).
- **Item:** iterate `data.children[].data`; skip `score < min_points`;
  `url`=external `url` (fallback to `https://www.reddit.com{permalink}`),
  `published`=`datetime.fromtimestamp(created_utc, tz=utc)`,
  `summary=f"{score} points on r/{sub}"`, `id=item_id(id)`.

## Docs & example

- `integrations.md`: update the security & HN rows in §2 (GHSA sub-endpoint,
  Reddit sub-endpoint); §5 keeps only NVD (with rationale) and drops Reddit.
- `radar.example.toml`: add a `ghsa` security source and a `reddit` social source.

## Tests

- `test_adapter_security.py`: convert the `ghsa`-not-implemented test to `nvd`;
  add GHSA mapping (severity + unknown→medium + stack_match), ecosystem-name
  mapping (`pypi`→`pip` in the request), per-ecosystem failure isolation, and
  missing-packages skip.
- `test_adapter_social.py`: convert the `reddit`-not-implemented test to an
  unknown source; add Reddit min_points, search-vs-new routing, User-Agent, and
  missing-subreddit tests.

## Out of scope

- NVD feed (rationale above).
- Reddit OAuth / pushshift (public `.json` is sufficient and keyless).
- Comment/self-text bodies (title + score is the awareness signal).
