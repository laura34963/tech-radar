# AI Development Guide

> **Type:** Reference / How-to
> **Audience:** Developers, AI assistants, and any tooling that needs project context
> **Last updated:** 2026-07-17
>
> Opt-in guardrails for developing safely on **tech-radar**. Generated on explicit
> request only — see the repo/user-level `AGENTS.md` (`~/.claude/CLAUDE.md`) for the
> general engineering principles this doc does not repeat.
>
> **Status — forward-looking:** As of this writing the `tech-radar/` code does **not yet
> exist**. Every claim below is grounded in the two authoritative design docs, not in
> code read from a running project. Re-verify against the implementation once Tasks 1–14
> land, and update the `Last updated` date.
>
> Source docs (authoritative):
> - Spec: [`../../docs/superpowers/specs/2026-07-17-tech-radar-design.md`](../../docs/superpowers/specs/2026-07-17-tech-radar-design.md)
> - Plan: [`../../docs/superpowers/plans/2026-07-17-tech-radar.md`](../../docs/superpowers/plans/2026-07-17-tech-radar.md)
> - Project README (planned): [`../README.md`](../README.md)

**Terminology:** This document uses RFC 2119 keywords — **MUST** (mandatory), **SHOULD** (recommended), **MAY** (optional).

---

<a id="1-safe-change-checklist"></a>

## 1. Safe-change checklist

### 1.1 Before you start
- [ ] Confirm you are on Python **3.11+** — the config loader uses stdlib `tomllib`, which does not exist before 3.11.
- [ ] Read the relevant task in the plan before writing code; this project is built strictly test-first (TDD), one task per commit.
- [ ] Confirm which config file you are pointing at (`--config`, default `config/radar.toml`). `config/radar.toml` is gitignored and user-owned; `config/radar.example.toml` is the committed template.
- [ ] If your change touches a network boundary, confirm you can exercise it **offline** with an `httpx.MockTransport` / `respx` fixture — never write a test that hits a live feed or API.

### 1.2 Before you open a PR
- [ ] `cd tech-radar && python -m pytest -q` passes (the whole suite, not just your new test).
- [ ] Your new behavior has a test that **failed before** your implementation and **passes after** (TDD; see [§5](#5-testing-and-verification)).
- [ ] Runtime dependencies are still only `feedparser`, `httpx`, `Jinja2` (test-only: `pytest`, `respx`). Any new dependency **MUST** be justified in the PR — see [§3](#3-project-specific-pitfalls).
- [ ] No secret value appears in code, config, test fixtures, logs, or the committed `output/`.
- [ ] Any HTML-rendering change keeps Jinja2 autoescape on and does not inject raw source/LLM text (see [§2.1](#2-security-sensitive-areas)).

### 1.3 Do / Don't
| Do | Don't |
|---|---|
| Read secrets from `os.environ` via the config's `api_key_env`/`GITHUB_TOKEN`/`NVD_API_KEY` names | Hardcode a key, or put a key value in `radar.toml` / `radar.example.toml` |
| Write files with `store.atomic_write_text` / `atomic_write_json` | Call `open(path, "w").write(...)` directly in a pipeline stage |
| Inject `now` into fetch/ranking and pass it through | Call `datetime.now()` inside pure logic (`fetch.py`, ranking, scoring) |
| Modify a frozen `Item` with `dataclasses.replace(...)` | Mutate `Item` fields in place or assume its list fields are immutable |
| Add a source type by writing an adapter + registering it in `ADAPTERS` | Add dynamic plugin discovery / config-driven class loading (explicit YAGNI in the spec) |
| Let a failing source/LLM call degrade (log WARN, continue) | Let one bad feed or LLM error abort the whole run |

---

<a id="2-security-sensitive-areas"></a>

## 2. Security-sensitive areas

This is a local/CI CLI with no users, no database, no auth, and no PII. The real
attack surface is **untrusted content** (feed items and LLM output rendered into public
HTML), **secret handling**, and **outbound HTTP to configured URLs**. The categories
below that don't apply are marked N/A rather than padded with generic advice.

### 2.1 Untrusted content → generated HTML (stored-XSS surface)
Feed titles/summaries (`radar/adapters/*`) and LLM responses (`radar/pipeline/enrich.py`)
are **untrusted** and end up in `output/digests/<date>.html`, which is published to the
web. When editing anything under `radar/templates/` or the render path
(`radar/pipeline/render.py`):
- **MUST** keep Jinja2 `autoescape=select_autoescape([...])` enabled. Never render a
  template with autoescape off, and never use the `| safe` filter on item-derived text.
- **MUST NOT** build HTML by string concatenation from item fields — go through a template.
- `parse_feed` (`radar/adapters/_feed.py`) strips tags and truncates; keep that as
  defense-in-depth, not the only defense (autoescape is the primary control).

### 2.2 Secrets / credentials
Keys are read from the environment only, by the names in config
(`llm.api_key_env` → default `RADAR_LLM_API_KEY`; plus `GITHUB_TOKEN`, `NVD_API_KEY`):
- **MUST NOT** log a key or a full request/response body that could contain one. The
  logging format is level-tagged to stderr with counts, not payloads — keep it that way.
- **MUST NOT** commit `config/radar.toml`, `.env`, or any key. `.gitignore` covers
  `config/radar.toml`; verify before `git add`.
- In CI, keys arrive as GitHub Actions secrets injected as env vars — never echo them in
  a workflow step.

### 2.3 External / SSRF-prone outbound calls
Every adapter and the LLM provider issue outbound HTTP to URLs taken from config
(`sources[].url`, `llm.base_url`) or upstream data (e.g. OSV/HN response fields). The
config author is the trust boundary, so this is **operator-controlled SSRF**, not
attacker-controlled — but two rules still hold:
- **MUST NOT** follow a URL that came from *fetched content* (e.g. an item's `link`) with
  another server-side request. Adapters record such URLs into `Item.url` for display only;
  they are never re-fetched.
- **SHOULD** keep per-request timeouts on every outbound call (the plan sets 20–120s) so a
  hostile/slow endpoint can't hang a run.

### 2.4 Auth / tokens
N/A — the tool has no sessions, no login, and issues no tokens. `GITHUB_TOKEN` is only an
outbound rate-limit credential (see [§2.2](#2-security-sensitive-areas)).

### 2.5 PII / money / raw SQL / cross-service boundaries
N/A — no personal data is collected, no billing, no database or SQL, and no
service-to-service surface. `tech-radar` is a standalone generator. If a future change
introduces any of these, add the corresponding section and re-scope this guide.

---

<a id="3-project-specific-pitfalls"></a>

## 3. Project-specific pitfalls / footguns

- **Undated feed entries get "now" as their timestamp.** `_feed._published` falls back to
  the current time when an entry has no `published_parsed`/`updated_parsed`. Such items
  therefore always pass the `lookback_days` filter. Don't treat `Item.published` as
  authoritative for undated sources; if you tighten lookback logic, decide explicitly how
  undated items should behave.
- **`Item` is a frozen dataclass with mutable default fields.** `tags` and `stack_match`
  are lists. Frozen prevents attribute *reassignment*, not list *mutation*. To change an
  item, build a new one with `dataclasses.replace(item, ...)` (as the cloud adapter and
  fetch finalizer do) — don't `item.tags.append(...)` on a shared instance.
- **Re-render is skipped when the snapshot hash is unchanged.** `render.run_render` skips a
  date whose `meta.rendered[date]` matches the current `snapshot_hash`. If you change a
  *template* or *CSS* (not the data), a plain re-run won't pick it up — use `--force`.
- **`--force` vs `--fresh` are different.** `--force` re-does a stage's work over the
  existing snapshot (re-fetch ok sources / re-enrich items / re-render). `--fresh` (fetch
  only) discards the snapshot and starts clean. Reaching for `--fresh` to "fix" a render is
  wrong and throws away fetched data.
- **Importance is recomputed every fetch, but only kept items survive the hard cut.**
  `run_fetch` re-scores importance in its finalize step and drops anything below
  `min_keep_importance`. If you lower a threshold in config, you must re-run `fetch` (the
  already-committed snapshot won't retroactively resurrect discarded items).
- **Enrich only touches `importance >= high` and skips items that already have `llm`.**
  A newly added `why_it_matters`/field won't backfill onto previously-enriched items without
  `--force`. Medium/low items are never sent to the LLM by design (cost guard).
- **Partial-run realism.** `fetch` marks a source `ok`/`failed` and checkpoints after each
  source; a re-run skips `ok` and retries `failed`. Don't add logic that re-fetches `ok`
  sources implicitly — it breaks the resumability contract and re-pays network/LLM cost.
- **v1 adapter gaps raise `NotImplementedError` on purpose.** `security` supports only
  `feed = "osv"`; `social` supports only `source = "hn"`. `ghsa`/`nvd`/`reddit` and the
  npm/PyPI registry adapter are documented deferrals, not bugs — don't "fix" them by
  stubbing silent empty results; keep the explicit error.
- **New dependencies are a design decision.** The spec caps runtime deps at
  `feedparser` + `httpx` + `Jinja2` and forbids vendor LLM SDKs (the provider layer is
  plain `httpx`). Adding one contradicts a spec constraint — get sign-off first.

---

<a id="4-before-editing-read"></a>

## 4. Before editing X, read Y

The descriptive docs now exist; read the relevant one before touching an area. The spec
and plan remain the authoritative source for anything not yet reflected in code.

| Before editing… | Read |
|---|---|
| The pipeline / stage boundaries (`fetch`/`enrich`/`render`) | [`project-overview.md` §3](project-overview.md#3-architecture-overview), [§5](project-overview.md#5-pipeline-and-cli) |
| A source adapter or the `Item` schema | [`domain-models.md` §2](domain-models.md#2-item-entity); [`integrations.md` §2](integrations.md#2-downstream); [`coding-style.md` §3](coding-style.md#3-layering-rules) |
| Importance / dedupe / ranking / filtering | [`domain-models.md` §5](domain-models.md#5-value-sets-and-importance-flow) |
| Templates / display tiers / hub navigation / history | [`project-overview.md` §5](project-overview.md#5-pipeline-and-cli); [`domain-models.md` §5](domain-models.md#5-value-sets-and-importance-flow); [`coding-style.md` §5](coding-style.md#5-html-safety) |
| The LLM provider or enrich stage | [`integrations.md` §2](integrations.md#2-downstream); [`domain-models.md` §5](domain-models.md#5-value-sets-and-importance-flow) |
| Config schema or validation | [`domain-models.md` §4](domain-models.md#4-configuration-model) |
| An external data source / SSRF or secrets concern | [`integrations.md`](integrations.md); [§2](#2-security-sensitive-areas) below |
| CI / publishing / Pages | [`project-overview.md` §6](project-overview.md#6-automation-and-config) |
| Any new file | [`coding-style.md`](coding-style.md); the "Global Constraints" header of the plan; the repo/user-level `AGENTS.md` |

---

<a id="5-testing-and-verification"></a>

## 5. Testing & verification expectations

- **Suite that MUST pass:** the `pytest` suite under `tech-radar/tests/`.
- **How to run locally:**
  ```bash
  cd tech-radar
  python -m venv .venv && source .venv/bin/activate
  pip install -r requirements-dev.txt
  python -m pytest -q
  ```
- **Test discipline (grounded in the plan's Global Constraints and per-task steps):**
  - **No live network in any test.** Mock HTTP with `httpx.MockTransport` or `respx`; load
    payloads from `tests/fixtures/` (saved RSS XML, GitHub/OSV/HN JSON).
  - **Determinism:** inject `now` into fetch/ranking; never let a test depend on the wall
    clock, real filesystem outside `tmp_path`, or randomness.
  - **Test behavior, not prose.** For LLM enrichment, assert that fields are populated and
    HTML-escaped — never assert exact model wording (non-deterministic).
  - **Cover the error paths that the design promises degrade gracefully:** a failing source
    is isolated and marked `failed`; an LLM failure leaves items on their rule-based fields;
    an atomic write leaves no `*.tmp` behind and no half-written file.
- **Manual verification CI can't cover:** a full `python radar.py run` reaches the live
  configured feeds/APIs. Before merging a change to an adapter's real request shape,
  smoke-test it once against the real endpoint locally (outside the test suite) and confirm
  the produced `output/digests/<date>.html` renders. `render`-only runs need no network.

---

<a id="6-see-also"></a>

## 6. See also

- Repo/user-level `AGENTS.md` (`~/.claude/CLAUDE.md`) — general engineering principles
  (security classes, testing philosophy, git/PR conventions) that this guide does not repeat.
- `project-security-fix` skill — use to remediate a specific, identified security issue.
- `vuln-scan-report` skill — use to turn scanner output into a structured report.
