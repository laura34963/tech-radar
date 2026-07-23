# Digest `board` Field Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route digest items to the tech/news boards via an explicit per-source `board` field instead of inferring from `(source_type, category)`.

**Architecture:** Add an optional `board ∈ {tech, news}` to each source. It is validated at config load, stamped onto the `Item` once in the fetch loop, persisted through the existing snapshot serialization, and read by `render._section()`. The only remaining inferred default is `social → news`, else `tech`.

**Tech Stack:** Python 3.11+ (`tomllib`), `dataclasses`, `pytest`, `httpx.MockTransport` for fetch tests.

## Global Constraints

- `board` allowed values: exactly `"tech"` and `"news"`. Any other value is a config error.
- `board` is optional; `None`/absent means "derive at render". Must round-trip through `item_to_dict`/`item_from_dict` and be back-compatible with snapshots that lack the key.
- No adapter files are modified. Stamping happens only in `radar/pipeline/fetch.py::run_fetch`.
- Behavior delta vs. today: CISA moves `news → tech` (leave it unannotated); the seven other security RSS feeds keep `news` via `board = "news"`. All other routing identical.
- Follow existing test style: `test_config` uses `load_config(tmp_path/file)` + `pytest.raises(ConfigError, match=...)`; `test_fetch` uses the `_cfg`/`httpx.MockTransport` helpers; `test_render` uses `_section` on plain dicts and `_group` on `Item`s.

---

## Setup (before Task 1)

- [ ] Create a feature branch off `main` and commit the approved spec.

```bash
git checkout -b feat/digest-board-field
git add docs/superpowers/specs/2026-07-23-digest-board-field-design.md docs/superpowers/plans/2026-07-23-digest-board-field.md
git commit -m "docs: spec and plan for explicit digest board field"
```

---

## Task 1: Validate `board` at config load

**Files:**
- Modify: `radar/config.py:6-10` (add `_BOARDS`), `radar/config.py:38-47` (validation loop)
- Test: `tests/test_config.py` (append)

**Interfaces:**
- Consumes: `load_config(path) -> Config`, `ConfigError` (existing).
- Produces: `load_config` raises `ConfigError` when any source has a `board` not in `{"tech","news"}`; accepts a source with a valid `board` or none.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py`, using the module's existing `_write(tmp_path, body)` helper (writes a `textwrap.dedent`-ed TOML string and returns the path):

```python
def test_invalid_board_raises(tmp_path):
    with pytest.raises(ConfigError, match="board"):
        load_config(_write(tmp_path, """
            categories = ["backend"]
            [[sources]]
            type = "rss"
            category = "backend"
            url = "https://x/feed"
            board = "bogus"
        """))


def test_valid_board_loads(tmp_path):
    cfg = load_config(_write(tmp_path, """
        categories = ["security"]
        [[sources]]
        type = "rss"
        category = "security"
        url = "https://x/feed"
        board = "news"
    """))
    assert cfg.sources[0]["board"] == "news"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_config.py::test_invalid_board_raises tests/test_config.py::test_valid_board_loads -v`
Expected: `test_invalid_board_raises` FAILS (no `ConfigError` raised — validation absent).

- [ ] **Step 3: Add `_BOARDS` constant**

In `radar/config.py`, immediately after the `_REQUIRED` dict (line 10):

```python
_BOARDS = {"tech", "news"}
```

- [ ] **Step 4: Add validation in the source loop**

In `radar/config.py`, inside the `for i, s in enumerate(sources):` loop, after the existing `for field_name in _REQUIRED[stype]:` block (after line 47):

```python
        board = s.get("board")
        if board is not None and board not in _BOARDS:
            raise ConfigError(
                f"{label}: board must be one of {sorted(_BOARDS)}, got {board!r}"
            )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_config.py -v`
Expected: PASS (including the two new tests).

- [ ] **Step 6: Commit**

```bash
git add radar/config.py tests/test_config.py
git commit -m "feat: validate optional source 'board' field at config load"
```

---

## Task 2: Carry `board` on `Item` and stamp it in the fetch loop

**Files:**
- Modify: `radar/item.py:26-27` (add field)
- Modify: `radar/pipeline/fetch.py:104-108` (stamp in loop)
- Test: `tests/test_fetch.py` (append)

**Interfaces:**
- Consumes: `Item` (frozen dataclass), `run_fetch(cfg, snapshot_path, *, now, client, force=False, fresh=False) -> dict`, `dataclasses.replace` (already imported in `fetch.py`).
- Produces: `Item.board: str | None = None`. After `run_fetch`, an item fetched from a source with `board="news"` has `board == "news"` in the snapshot dict; an item from a source with no `board` has `board is None`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_fetch.py` (reuse the module's existing `_cfg` helper and `httpx.MockTransport` pattern). The shared `sample_rss.xml` fixture has a single fixed `guid`, so two feeds using it would dedupe to one item; use two feeds with distinct guids instead:

```python
_BOARD_FEED = """<?xml version="1.0"?>
<rss version="2.0"><channel><title>S</title>
  <item>
    <title>{title}</title>
    <link>{link}</link>
    <description>d</description>
    <pubDate>Wed, 16 Jul 2026 10:00:00 GMT</pubDate>
    <guid>{link}</guid>
  </item>
</channel></rss>"""


def test_run_fetch_stamps_declared_board(tmp_path):
    def handler(req):
        if "newsfeed" in str(req.url):
            return httpx.Response(200, text=_BOARD_FEED.format(title="N", link="https://example.com/n"))
        return httpx.Response(200, text=_BOARD_FEED.format(title="P", link="https://example.com/p"))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    cfg = _cfg([
        {"type": "rss", "category": "backend", "url": "https://newsfeed/feed", "board": "news"},
        {"type": "rss", "category": "backend", "url": "https://plainfeed/feed"},
    ])
    snap = run_fetch(cfg, tmp_path / "board.json",
                     now=datetime(2026, 7, 17, tzinfo=timezone.utc),
                     client=client, fresh=True)
    boards = {it["title"]: it["board"] for it in snap["items"]}
    assert boards == {"N": "news", "P": None}  # declared stamped, undeclared stays None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_fetch.py::test_run_fetch_stamps_declared_board -v`
Expected: FAIL — `Item.__init__` rejects unexpected keyword `board`, or `KeyError: 'board'` on the snapshot dict (field does not exist yet).

- [ ] **Step 3: Add the field to `Item`**

In `radar/item.py`, between the `stack_match` and `llm` fields (line 26):

```python
    stack_match: list[str] = field(default_factory=list)
    board: str | None = None  # explicit board override; None → derive by nature at render
    llm: dict | None = None
```

- [ ] **Step 4: Stamp the board in the fetch loop**

In `radar/pipeline/fetch.py::run_fetch`, replace the fetched-items merge block (lines 104-108):

```python
            fetched = adapter.fetch(source, cfg, client=client, now=now)
            board = source.get("board")
            for it in fetched:
                if board is not None:
                    it = replace(it, board=board)
                cur = by_id.get(it.id)
                if cur is None or len(it.summary) > len(cur.summary):
                    by_id[it.id] = it
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_fetch.py -v`
Expected: PASS (new test plus all existing fetch tests, confirming snapshot round-trip still works).

- [ ] **Step 6: Commit**

```bash
git add radar/item.py radar/pipeline/fetch.py tests/test_fetch.py
git commit -m "feat: stamp source 'board' onto items during fetch"
```

---

## Task 3: Route by `board` in render + migrate config

**Files:**
- Modify: `radar/pipeline/render.py:171-184` (rewrite `_section`)
- Modify: `config/radar.toml` (7 security news feeds), `config/radar.example.toml` (same feeds if present)
- Test: `tests/test_render.py:181-195, 327-340, 343-357` (update 3 tests, add board cases)

**Interfaces:**
- Consumes: item dict with keys `board`, `source_type`, `category`; `Item.board` from Task 2.
- Produces: `_section(it) -> "tech" | "news"` — explicit `board` in `{"tech","news"}` wins; else `"news"` if `source_type == "social"`, else `"tech"`.

- [ ] **Step 1: Rewrite the `_section` tests to the new contract**

In `tests/test_render.py`, replace `test_section_classifies_by_source_nature` (lines 181-195) with:

```python
def test_section_routes_by_explicit_board_then_social_default():
    def it(source_type, category, board=None):
        return {"source_type": source_type, "category": category, "board": board}
    # explicit board wins in both directions
    assert _section(it("rss", "security", "news")) == "news"
    assert _section(it("social", "backend", "tech")) == "tech"
    # no board: social -> news, everything else -> tech
    assert _section(it("social", "backend")) == "news"
    assert _section(it("rss", "security")) == "tech"   # CHANGED: no longer auto-news
    assert _section(it("rss", "backend")) == "tech"
    assert _section(it("github", "backend")) == "tech"
    assert _section(it("security", "security")) == "tech"  # OSV / GHSA advisories
    assert _section(it("cloud", "cloud")) == "tech"
```

- [ ] **Step 2: Update the `_group` tests to declare `board`**

In `tests/test_render.py`, in `test_group_splits_items_into_tech_and_news_boards` (line 331) add `board="news"` to the `news` Item, and in `test_digest_renders_both_tabs_with_tech_default` (line 347) add `board="news"` to the `news` Item:

```python
    news = Item(id="2", title="Breach", url="https://x/2", source_type="rss",
                category="security", published=NOW, summary="s",
                importance="high", severity="high", board="news")
```

```python
    news = Item(id="2", title="Krebs Story", url="https://x/2", source_type="rss",
                category="security", published=NOW, summary="s",
                importance="high", severity="high", board="news")
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_render.py -k "section or group or both_tabs" -v`
Expected: FAIL — `_section` still returns `"news"` for `rss+security` with no board (old rule), so the CHANGED assertion fails.

- [ ] **Step 4: Rewrite `_section`**

In `radar/pipeline/render.py`, replace `_section` (lines 171-184) with:

```python
def _section(it: dict) -> str:
    """Which board an item belongs to. An explicit source-level `board`
    ('tech'|'news') wins; otherwise derive by nature: social/community feeds
    are news, everything else (releases, blogs, cloud, advisory feeds) is tech.
    """
    board = it.get("board")
    if board in ("tech", "news"):
        return board
    return "news" if it.get("source_type") == "social" else "tech"
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_render.py -v`
Expected: PASS (all render tests, including the three edited ones).

- [ ] **Step 6: Migrate the config feeds**

In `config/radar.toml`, add `board = "news"` to the seven security news feeds (leave CISA and the OSV/GHSA `security`-type sources unannotated). For each, insert the line under `category = "security"`:

```toml
[[sources]]
type = "rss"
category = "security"
board = "news"
url = "https://feeds.feedburner.com/TheHackersNews"   # The Hacker News (verified)
```

Apply the same `board = "news"` line to: SANS ISC (`isc.sans.edu`), Krebs (`krebsonsecurity.com`), Unit 42 (`unit42.paloaltonetworks.com`), Cisco Talos (`blog.talosintelligence.com`), The DFIR Report (`thedfirreport.com`), and r/netsec (`reddit.com/r/netsec`). Do **not** add it to CISA (`cisa.gov`). Mirror the identical edits in `config/radar.example.toml` for whichever of these feeds exist there.

- [ ] **Step 7: Verify config still loads and migration is complete**

Run:
```bash
python -c "from radar.config import load_config; from pathlib import Path; c=load_config(Path('config/radar.toml')); print(sum(1 for s in c.sources if s.get('board')=='news'))"
```
Expected: `7`
Run: `grep -c 'board = "news"' config/radar.toml`
Expected: `7`

- [ ] **Step 8: Commit**

```bash
git add radar/pipeline/render.py config/radar.toml config/radar.example.toml tests/test_render.py
git commit -m "feat: route digest boards by explicit 'board' field; migrate security feeds"
```

---

## Task 4: Full-suite verification

**Files:** none (verification only).

- [ ] **Step 1: Run the whole suite**

Run: `pytest -q`
Expected: all tests pass, no new warnings.

- [ ] **Step 2: Confirm behavior delta**

Confirm CISA now routes to `tech` and the seven news feeds route to `news` by inspecting a render or reasoning from the passing `_section` tests. No commit needed unless the suite surfaced a fix.

---

## Self-Review

**Spec coverage:**
- Problem/approach → Tasks 1-3. ✓
- `Item.board` field → Task 2 Step 3. ✓
- Config validation → Task 1. ✓
- Fetch stamping (single choke point, no adapters) → Task 2 Step 4. ✓
- Snapshot round-trip / back-compat → covered by existing fetch tests re-run in Task 2 Step 5 (no `store.py` change needed; `Item(**d)` uses the default when the key is absent). ✓
- `_section` rewrite → Task 3 Step 4. ✓
- CISA `news → tech`, seven feeds keep `news` → Task 3 Step 6 + verification Step 7. ✓
- Testing (config/fetch/render) → Tasks 1-3. ✓
- Migration same-commit → Task 3 Step 8 ships render + config together. ✓
- Out-of-scope items → none added. ✓

**Placeholder scan:** One conditional in Task 2 Step 1 (fixture item-count check) is explicit and instructs inspecting `sample_rss.xml` and adjusting — not a deferred TODO. No other placeholders.

**Type consistency:** `board: str | None`; `_BOARDS = {"tech","news"}`; `_section(it: dict) -> str`; `source.get("board")` used consistently across config/fetch/render. Names match across tasks.
