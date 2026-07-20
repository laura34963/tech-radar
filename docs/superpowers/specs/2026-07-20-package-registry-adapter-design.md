# Package registry adapter — design

> **Date:** 2026-07-20
> **Status:** Approved, implementing
> **Deferral resolved:** [`docs/integrations.md` §5](../../integrations.md#5-deferred-integrations) — "Package registries (npm / PyPI / RubyGems)"

## Goal

Surface recent releases of stack packages from **npm**, **PyPI**, and **RubyGems**
as a new `registry` source type — complementing the GitHub releases adapter for
dependencies that don't publish GitHub releases or whose registry is the canonical
release channel.

## Config

One source per registry with an explicit package list (no cross-product 404s;
each package's ecosystem is unambiguous):

```toml
[[sources]]
type = "registry"
registry = "npm"            # npm | pypi | rubygems
packages = ["react", "axios"]
category = "frontend"
```

`config.py`: add `"registry": ["registry", "packages"]` to `_REQUIRED`. An empty
`packages` list is falsy, so existing validation rejects it. An unknown `registry`
value is rejected at fetch time with `NotImplementedError`, matching the
`security`/`social` adapters' v1-gating pattern.

## Adapter — `radar/adapters/registry.py`

`RegistryAdapter` (`type = "registry"`), registered in `adapters/__init__.py`.
One GET per package, **per-package isolation** (a 404 or transport error is logged
and skipped, never fatal — mirrors `security.py`'s per-query `try/except`).

| registry | endpoint | versions & dates | package/version URL |
|---|---|---|---|
| npm | `registry.npmjs.org/{pkg}` | `time` map `{version: iso}` (drop `created`/`modified` keys) | `npmjs.com/package/{pkg}/v/{version}` |
| pypi | `pypi.org/pypi/{pkg}/json` | `releases[version][0].upload_time_iso_8601` | `pypi.org/project/{pkg}/{version}/` |
| rubygems | `rubygems.org/api/v1/versions/{gem}.json` | list of `{number, created_at}` | `rubygems.org/gems/{pkg}/versions/{version}` |

Each package yields its **10 most recent versions** (mirrors github's
`per_page=10`); the pipeline's `within_lookback` drops anything older than
`lookback_days`. No semver/prerelease parsing — lookback handles staleness (YAGNI).

**Item mapping:** `id=item_id(url)`, `title=f"{pkg} {version}"`,
`source_type="registry"`, `category=source["category"]`,
`published=<version date>`, `summary=f"New {display} release: {pkg} {version}"`,
`stack_match=[pkg]`. `timeout=20.0`, `follow_redirects=True`.

## Pipeline touch-ups — `radar/pipeline/fetch.py`

- **Source label** (~line 73): add `source.get("registry")` to the name
  resolution so registry sources show a real name in logs/meta, not `source{i}`.
- **`score_importance`** (~line 33): add `"registry"` to the medium-tier tuple, so
  a release the user explicitly subscribed to isn't dropped by the default
  `min_keep_importance = "medium"` when the package name doesn't literally appear
  in `[stack].packages`.

## Docs & example

- `config/radar.example.toml`: add a `registry` source example.
- `docs/integrations.md`: add rows to §2 (downstream); move package registries
  out of §5 (deferred).

## Tests — `tests/test_adapter_registry.py`

`httpx.MockTransport` + per-registry JSON fixtures, following
`test_adapter_github.py`:

- maps versions → items correctly for each of npm / pypi / rubygems
- applies the 10-version cap
- per-package error isolation (one package's 404 doesn't kill the others)
- unknown `registry` → `NotImplementedError`

## Explicitly out of scope

- Semver / prerelease filtering (lookback covers staleness).
- Changelog fetching (registries don't expose it reliably; re-fetching item URLs
  would violate the SSRF guardrail in `ai-development-guide.md` §2.3).
- Auth (all three endpoints are keyless).
