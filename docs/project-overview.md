# Project Overview

> **Type:** Explanation
> **Audience:** Developers, AI assistants, and any tooling that needs project context
> **Last updated:** 2026-07-17
>
> What tech-radar is, how it is built, and how its pieces connect.
>
> **Status — forward-looking:** the `tech-radar/` code does not yet exist. Every claim
> here is grounded in the approved spec and implementation plan, not in running code.
> Re-verify and update once the implementation lands.
>
> Related docs:
> - [`domain-models.md`](domain-models.md) — the `Item`, snapshot, and config data model
> - [`coding-style.md`](coding-style.md) — conventions and layering rules
> - [`integrations.md`](integrations.md) — external data sources and LLM providers
> - [`ai-development-guide.md`](ai-development-guide.md) — safe-change guardrails
> - Spec: [`../../docs/superpowers/specs/2026-07-17-tech-radar-design.md`](../../docs/superpowers/specs/2026-07-17-tech-radar-design.md)
> - Plan: [`../../docs/superpowers/plans/2026-07-17-tech-radar.md`](../../docs/superpowers/plans/2026-07-17-tech-radar.md)

---

<a id="1-what-it-is"></a>

## 1. What it is

tech-radar is a command-line tool that gathers the latest technical information across
**backend, frontend, devops, cloud, and security**, ranks and filters it by importance,
optionally enriches it with an LLM, and renders **permanent, date-based static HTML
digests** behind a central hub page. The site is committed to git and served via GitHub
Pages so developers can browse the latest info from the web.

Sources are hybrid and fully user-configured: generic industry feeds for awareness plus a
stack-tailored layer that highlights items affecting the user's own dependencies.

It is a standalone tool in the `kdan_tools` monorepo, alongside `ticket-sync`,
`git-tools`, and `secrets`.

---

<a id="2-tech-stack"></a>

## 2. Tech stack

| Concern | Choice |
|---|---|
| Language / runtime | Python **3.11+** (requires stdlib `tomllib`) |
| Feed parsing | `feedparser` |
| HTTP client | `httpx` |
| HTML templating | `Jinja2` (autoescape on) |
| Config format | TOML (`config/radar.toml`), parsed with stdlib `tomllib` |
| Snapshot / state | JSON files on disk (per-day) |
| LLM access | Provider-agnostic plain HTTP via `httpx` (no vendor SDKs) |
| Tests | `pytest` + `respx` (test-only) |
| CLI | stdlib `argparse` |
| Automation | GitHub Actions (scheduled) + GitHub Pages |

Runtime dependencies are deliberately capped at `feedparser`, `httpx`, and `Jinja2`.

---

<a id="3-architecture-overview"></a>

## 3. Architecture overview

A staged, resumable pipeline with a per-day JSON snapshot as the single source of truth
between stages. Adapters do I/O and normalization only; all dedupe / rank / importance /
filter logic is centralized and unit-tested offline.

```
                 config/radar.toml
                        │
                        ▼
   ┌───────────────── fetch ──────────────────┐
   │  adapters (rss, cloud, github, security,  │
   │  social) → normalize → dedupe → score     │
   │  importance → lookback filter → hard cut  │
   │  → rank/truncate                          │
   └───────────────────┬───────────────────────┘
                        ▼
            output/data/<date>.json  ◀── single source of truth (snapshot)
                        │
   ┌──────────────── enrich (optional) ─────────┐
   │  LLM provider (openai_compatible/anthropic/ │
   │  ollama) → per-category batch → JSON fields │
   │  written back into each Item.llm            │
   └───────────────────┬─────────────────────────┘
                        ▼
   ┌──────────────── render ───────────────────┐
   │  Jinja2 → display tiers (cards / also-noted)│
   │  → output/digests/<date>.html               │
   │  → rebuild output/index.html (central hub)  │
   └───────────────────┬─────────────────────────┘
                        ▼
             GitHub Pages (main:/output)
```

Each stage checkpoints its progress in the snapshot's `meta` block and writes files
atomically, so any stage that fails partway is re-run and continues from disk. See
[`domain-models.md` §3](domain-models.md#3-snapshot-document) for the snapshot shape.

---

<a id="4-directory-layout"></a>

## 4. Directory / module layout

| Path | Purpose |
|---|---|
| `radar.py` | CLI entrypoint; subcommands `fetch`, `enrich`, `render`, `run` |
| `radar/item.py` | The normalized `Item` dataclass, `item_id`, `IMPORTANCE_ORDER` |
| `radar/config.py` | Load + validate `radar.toml` (`Config`, `ConfigError`) |
| `radar/store.py` | Atomic file IO + snapshot serialize/deserialize |
| `radar/adapters/` | One module per source type + shared `_feed.py`; `ADAPTERS` registry |
| `radar/pipeline/fetch.py` | Fetch stage: dedupe, importance scoring, ranking, resumability |
| `radar/pipeline/enrich.py` | Optional LLM enrichment stage |
| `radar/pipeline/render.py` | Render stage: digest pages, central hub index, navigation |
| `radar/llm/provider.py` | Provider-agnostic LLM client (3 providers) |
| `radar/llm/prompts/` | Plain-text prompt templates |
| `radar/templates/` | Jinja2 templates + shared `styles.css` |
| `config/` | `radar.toml` (user, gitignored) + `radar.example.toml` (committed) |
| `output/` | Generated static site (GitHub Pages root); data snapshots + HTML |
| `tests/` | `pytest` suite + `fixtures/` |
| `.github/workflows/radar.yml` | Scheduled run + commit + Pages |

Files that change together live together (a stage's logic, its tests, its templates).

---

<a id="5-pipeline-and-cli"></a>

## 5. Pipeline stages & CLI commands

| Command | Does | Reads | Writes |
|---|---|---|---|
| `radar.py fetch` | Run adapters, normalize, dedupe, score, filter, rank | config + existing snapshot | `output/data/<date>.json` |
| `radar.py enrich` | Optional LLM pass over high/critical items | snapshot | LLM fields back into the snapshot |
| `radar.py render` | Build digest page + rebuild the hub index | snapshot + `output/data/*.json` | `output/digests/<date>.html`, `output/index.html` |
| `radar.py run` | `fetch` → `enrich` → `render` in sequence (used by CI) | — | all of the above |

Shared flags: `--config` (default `config/radar.toml`), `--output` (default `output`),
`--force` (all stages: redo work over the existing snapshot). `fetch` also has `--fresh`
(discard the snapshot and start clean). See [`coding-style.md` §4](coding-style.md#4-error-handling-and-logging)
for exit-code behavior.

**Resumability contract:** `fetch` skips sources already marked `ok` and retries `failed`
ones; `enrich` skips items that already carry an `llm` block; `render` skips a date whose
snapshot hash is unchanged. `--force` overrides each.

---

<a id="6-automation-and-config"></a>

## 6. Scheduled processes, environments & configuration

**Scheduled process.** `.github/workflows/radar.yml` runs on a daily `schedule` cron and
on `workflow_dispatch`. It installs deps, copies the example config, runs `radar.py run`,
and commits `output/` back to `main`; a concurrency guard prevents overlapping runs from
racing the commit. GitHub Pages serves `main:/output`.

**Configuration.** A single TOML file (`config/radar.toml`) holds `[general]`, `[stack]`,
`categories`, `[[sources]]`, and `[llm]`. The committed `config/radar.example.toml` is the
template; `config/radar.toml` is gitignored. See [`domain-models.md` §4](domain-models.md#4-configuration-model)
for the config model.

**Secrets (environment only, never in config or logs):**

| Env var | Used by | Required? |
|---|---|---|
| `RADAR_LLM_API_KEY` (name is configurable via `llm.api_key_env`) | LLM enrichment | Only if LLM enabled + non-ollama |
| `GITHUB_TOKEN` | GitHub releases adapter (rate limits) | Optional |
| `NVD_API_KEY` | NVD security feed (deferred in v1) | Optional |

If no LLM key is present, the run degrades cleanly to a rule-based digest.

---

<a id="7-integrations"></a>

## 7. Integrations

**Downstream (this tool calls out):** every adapter and the LLM provider issue outbound
HTTP to user-configured or upstream-provided URLs — RSS/Atom + cloud "What's New" feeds,
the GitHub REST API, the OSV vulnerability API, the Hacker News Algolia API, and the
configured LLM endpoint. Each call has a timeout and fails in isolation (a dead source is
logged and skipped, not fatal).

**Upstream (who calls this tool):** none — tech-radar is a leaf CLI. Its only "consumers"
are the developer running it and visitors to the generated GitHub Pages site.

The full downstream surface (endpoints, auth, failure behavior) and topology diagram live
in [`integrations.md`](integrations.md).
