# Integrations

> **Type:** Reference
> **Audience:** Developers, AI assistants, and any tooling that needs project context
> **Last updated:** 2026-07-20
>
> The external services tech-radar depends on (downstream), and the contract/failure
> behavior on each link.
>
> **Status — forward-looking:** the `tech-radar/` code does not yet exist. Endpoints and
> behavior below are grounded in the approved spec and implementation plan; re-verify the
> exact request shapes against the adapters once implemented.
>
> Related docs:
> - [`project-overview.md` §7](project-overview.md#7-integrations) — integrations summary
> - [`domain-models.md` §2](domain-models.md#2-item-entity) — the `Item` these sources produce
> - [`ai-development-guide.md` §2](ai-development-guide.md#2-security-sensitive-areas) — SSRF/secrets guardrails

---

<a id="1-overview"></a>

## 1. Overview

tech-radar is a **leaf** tool: it has many downstream dependencies (the data sources and
the LLM) and **no upstream callers**. Every downstream call is outbound HTTP via a shared
`httpx.Client`, carries a timeout, and fails in isolation — a dead or slow source is
logged and marked `failed` in the snapshot `meta`, never fatal to the run.

All target URLs come from the **user-owned config** (`sources[].url`, `llm.base_url`) or
from provider APIs whose base is fixed in code. Item `url` values harvested from fetched
content are recorded for display only and are **never re-fetched** (SSRF guardrail — see
[`ai-development-guide.md` §2.3](ai-development-guide.md#2-security-sensitive-areas)).

---

<a id="2-downstream"></a>

## 2. Downstream dependencies

| Service | Adapter / module | Protocol | Endpoint (base) | Purpose | Auth | Failure behavior |
|---|---|---|---|---|---|---|
| RSS/Atom feeds (blogs, newsletters, HN-RSS) | `adapters/rss.py` → `_feed.parse_feed` | HTTPS GET | configured `url` | Generic awareness items | none | timeout 20s; source marked `failed`, skipped |
| Cloud "What's New" (AWS/GCP/Azure) | `adapters/cloud.py` → `_feed.parse_feed` | HTTPS GET | configured `url` | Cloud updates, filtered by `services` | none | timeout 20s; isolated |
| GitHub Releases | `adapters/github.py` | HTTPS GET | `api.github.com/repos/{repo}/releases` | Release/tag news for stack repos | optional `GITHUB_TOKEN` (Bearer) | timeout 20s; isolated |
| OSV vulnerability DB | `adapters/security.py` (`feed = "osv"`) | HTTPS POST | `api.osv.dev/v1/query` | CVEs for configured `packages` | none (keyless) | timeout 20s; isolated |
| GitHub Security Advisories | `adapters/security.py` (`feed = "ghsa"`) | HTTPS GET | `api.github.com/advisories?ecosystem=&affects=` | Advisories for configured `packages` | optional `GITHUB_TOKEN` (Bearer) | timeout 20s; per-ecosystem isolated |
| Hacker News (Algolia) | `adapters/social.py` (`source = "hn"`) | HTTPS GET | `hn.algolia.com/api/v1/search_by_date` | Curated stories over `min_points` | none | timeout 20s; isolated |
| Reddit | `adapters/social.py` (`source = "reddit"`) | HTTPS GET | `reddit.com/r/{sub}/new.json` or `/search.json` | Subreddit posts over `min_points` | none (descriptive User-Agent) | timeout 20s; isolated |
| Package registries (npm / PyPI / RubyGems) | `adapters/registry.py` | HTTPS GET | `registry.npmjs.org/{pkg}`, `pypi.org/pypi/{pkg}/json`, `rubygems.org/api/v1/versions/{gem}.json` | Recent releases for configured `packages` | none | timeout 20s; per-package isolated |
| LLM provider | `llm/provider.py` | HTTPS POST | configured `base_url` (`/chat/completions`, `/v1/messages`, `:generateContent`, or `/api/chat`) | Enrich items (summary/detail/why/action) | `RADAR_LLM_API_KEY` (or none for ollama) | timeout 60–120s; item keeps rule-based fields on failure |

**LLM provider dialects** (all in `llm/provider.py`, plain `httpx` — no vendor SDKs):

| `provider` | Path appended to `base_url` | Auth header |
|---|---|---|
| `openai_compatible` | `/chat/completions` | `Authorization: Bearer <key>` |
| `anthropic` | `/v1/messages` | `x-api-key: <key>` + `anthropic-version` |
| `gemini` | `/v1beta/models/{model}:generateContent` | `x-goog-api-key: <key>` |
| `ollama` | `/api/chat` | none (local) |
| `cli` | local subprocess — runs a configured CLI (`claude -p`, `gemini`, …), prompt on stdin, result from stdout | none (the CLI handles its own auth) |

The `cli` provider is not an HTTP call: it runs an operator-configured command as an argv list (no shell → no injection), passing the prompt on stdin (or into a `{prompt}` argument token). Use it to route enrichment through a locally-authenticated CLI such as Claude Code (`claude -p`) or the Gemini CLI.

---

<a id="3-upstream"></a>

## 3. Upstream consumers

**None.** tech-radar exposes no API, queue, or webhook. Its output is consumed only by:

- the developer running the CLI, and
- visitors to the generated GitHub Pages site (static HTML — no server contract).

Because there are no programmatic callers, there is no inbound trust boundary to defend and
no contract that editing a handler could break.

---

<a id="4-topology"></a>

## 4. Topology diagram

```
        ┌──────────── RSS / Atom feeds ────────────┐
        │            (incl. HN-RSS)                 │
        ├──────────── Cloud What's-New ────────────┤ GET
        │            (AWS / GCP / Azure)            │
        ├──────────── GitHub Releases API ─────────┤ GET (+opt token)
 tech-  │                                           │
 radar ─┼──────────── OSV vuln API ─────────────────┤ POST
  CLI   │                                           │
        ├──────────── HN Algolia API ──────────────┤ GET
        │                                           │
        └──────────── LLM provider ────────────────┘ POST (+key)
                          │
                          ▼
                generated static HTML  ──▶ GitHub Pages ──▶ browser (read-only)
```

All arrows are **outbound** from the CLI. Nothing calls the CLI.

---

<a id="5-deferred-integrations"></a>

## 5. Deferred integrations (not in v1)

These are documented deferrals, not gaps — the code raises `NotImplementedError` (adapters)
or simply omits them, by design:

| Integration | Status | Notes |
|---|---|---|
| NVD security feed | Deferred | `security` adapter supports `feed = "osv"` and `feed = "ghsa"`; NVD's keyword search is not ecosystem/package-aware and returns heavy noise that OSV+GHSA already cover precisely. `NVD_API_KEY` reserved if it's ever added. |

**Now implemented** (previously deferred): GHSA advisories (`feed = "ghsa"`),
Reddit (`source = "reddit"`), and package registries (`registry` adapter) — see [§2](#2-downstream).

When adding any remaining deferral, extend the relevant adapter and update [§2](#2-downstream).
