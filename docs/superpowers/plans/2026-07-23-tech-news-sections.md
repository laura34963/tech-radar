# 技術資訊 / 新聞 雙板塊 digest — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the weekly digest into two boards — 技術資訊 (tech) and 新聞 (news) — behind CSS-only tabs so they are never shown mixed in one view.

**Architecture:** Classify each item into a board at render time by source nature (a pure helper reading `source_type` + `category`). `_group()` returns per-board category groupings; `render_digest()` computes an independent KPI tally and priority list per board and passes a `boards` list to the template. The template renders one hidden-radio tab per board with its own KPI → Priority → By-Category panel; the stylesheet hides all but the checked board. No config, fetch, adapter, or item-schema changes; the digest page stays JS-free.

**Tech Stack:** Python 3.13, Jinja2, pytest. Static HTML + one `styles.css`.

## Global Constraints

- No changes to `radar/config.py`, `radar/adapters/*`, `radar/item.py`, `config/*.toml`, or `radar/pipeline/fetch.py` / `enrich.py`.
- Classification derives only from fields already on each serialized item: `source_type` and `category`. No new item fields.
- Board keys are exactly `"tech"` and `"news"`; tab labels are exactly `技術資訊` and `新聞`. Default (first) board is `tech`, rendered with the radio `checked`.
- Digest page has **no JavaScript** — tabs are CSS-only (hidden `<input type="radio">` + `<label>` + sibling-combinator show/hide).
- `index.html` (archive) is unchanged this round.
- Empty-board placeholder text is exactly `本區本週無情資`.
- Use existing CSS custom properties: `--bg`, `--surface`, `--text`, `--muted`, `--border`, `--accent`, `--surface-2`. Do not invent new color values.
- Run tests with the repo venv: `.venv/bin/pytest`.

---

### Task 1: `_section()` board classifier

**Files:**
- Modify: `radar/pipeline/render.py` (add helper near `_level`, ~line 166)
- Test: `tests/test_render.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `_section(it: dict) -> str` returning `"tech"` or `"news"`. Rule: `source_type == "social"` → news; `source_type == "rss"` and `category == "security"` → news; otherwise tech.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_render.py` (near the other helper imports at the top of the tally/priority section):

```python
from radar.pipeline.render import _section


def test_section_classifies_by_source_nature():
    def it(source_type, category):
        return {"source_type": source_type, "category": category}
    # tech: releases, official blogs, cloud, advisories
    assert _section(it("github", "backend")) == "tech"
    assert _section(it("cloud", "cloud")) == "tech"
    assert _section(it("registry", "backend")) == "tech"
    assert _section(it("security", "security")) == "tech"        # OSV / GHSA advisory feeds
    assert _section(it("rss", "backend")) == "tech"              # official blog
    assert _section(it("rss", "frontend")) == "tech"
    assert _section(it("rss", "devops")) == "tech"
    # news: social + security-category news feeds
    assert _section(it("social", "backend")) == "news"          # HN, category is incidental
    assert _section(it("social", "security")) == "news"
    assert _section(it("rss", "security")) == "news"            # THN/Krebs/SANS/CISA/Reddit-rss
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_render.py::test_section_classifies_by_source_nature -v`
Expected: FAIL with `ImportError: cannot import name '_section'`.

- [ ] **Step 3: Implement `_section()`**

In `radar/pipeline/render.py`, add directly above `_level` (line 166):

```python
def _section(it: dict) -> str:
    """Which board an item belongs to, by source nature.

    News = community/social feeds and security-category news outlets. Tech =
    everything else: releases, official-project blogs, cloud change feeds, and
    advisory feeds (OSV/GHSA use source_type 'security'). Derived purely from
    the item so no config or schema change is needed.
    """
    st = it.get("source_type")
    if st == "social":
        return "news"
    if st == "rss" and it.get("category") == "security":
        return "news"
    return "tech"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_render.py::test_section_classifies_by_source_nature -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add radar/pipeline/render.py tests/test_render.py
git commit -m "feat: classify digest items into tech/news boards by source nature"
```

---

### Task 2: Group by board and render two tab panels

Atomic behavior change: `_group()` returns per-board groupings, `render_digest()` builds a `boards` context, and `digest.html.j2` renders both panels. Kept in one task because the `_group()` return-shape change, the template context, and the three direct `_tally`/`_priority` tests must move together to keep the suite green. Styling is deferred to Task 3 — until then both panels render stacked, which is fine for content assertions.

**Files:**
- Modify: `radar/pipeline/render.py` — `_group` (lines 151-163), `render_digest` (lines 203-211)
- Modify: `radar/templates/digest.html.j2` (full rewrite of the `.wrap` body)
- Modify: `tests/test_render.py` — three `_tally`/`_priority` tests + add render tests

**Interfaces:**
- Consumes: `_section` (Task 1); unchanged `_tally(grouped)` / `_priority(grouped)` which each take one board's `{cat: {"cards": [...], "also_noted": [...]}}` mapping.
- Produces:
  - `_group(snapshot, cfg) -> dict` now returns `{"tech": {cat: {...}}, "news": {cat: {...}}}` (each inner value is the same shape `_tally`/`_priority` already accept).
  - `render_digest(...)` passes template vars `boards` (a list of dicts, first = default), `snapshot`, `cfg`, `period`, `prev`, `next`. Each board dict: `{"key": "tech"|"news", "label": str, "grouped": {cat: {...}}, "tally": {...}, "priority": [...], "has_items": bool}`.

- [ ] **Step 1: Update the three helper tests to index a board, and add render assertions**

In `tests/test_render.py`, change the three direct-call helpers so they select the `tech` board (all their fixture items classify as tech):

```python
def test_tally_counts_cards_by_alert_tier():
    cfg = _cfg()
    crit = Item(id="1", title="c", url="https://x/1", source_type="security",
                category="backend", published=NOW, summary="s",
                importance="critical", severity="critical")
    sec_high = Item(id="2", title="sh", url="https://x/2", source_type="security",
                    category="backend", published=NOW, summary="s",
                    importance="high", severity="high")
    plain_high = Item(id="3", title="ph", url="https://x/3", source_type="rss",
                      category="backend", published=NOW, summary="s", importance="high")
    med = Item(id="4", title="m", url="https://x/4", source_type="rss",
               category="backend", published=NOW, summary="s", importance="medium")
    t = _tally(_grouped_from(cfg, [crit, sec_high, plain_high, med])["tech"])
    assert t == {"total": 3, "critical": 1, "high": 1, "medium": 0, "normal": 1}


def test_priority_is_critical_and_high_sorted():
    cfg = _cfg()
    sec_high = Item(id="1", title="H", url="https://x/1", source_type="security",
                    category="backend", published=NOW, summary="s",
                    importance="high", severity="high")
    crit = Item(id="2", title="C", url="https://x/2", source_type="security",
                category="backend", published=NOW, summary="s",
                importance="critical", severity="critical")
    plain_high = Item(id="3", title="N", url="https://x/3", source_type="rss",
                      category="backend", published=NOW, summary="s", importance="high")
    pri = _priority(_grouped_from(cfg, [sec_high, crit, plain_high])["tech"])
    assert [p["title"] for p in pri] == ["C", "H"]
    assert all(p["category"] == "backend" for p in pri)


def test_priority_empty_when_only_normal_tier_cards():
    cfg = _cfg()
    plain_high = Item(id="1", title="H", url="https://x/1", source_type="rss",
                      category="backend", published=NOW, summary="s", importance="high")
    assert _priority(_grouped_from(cfg, [plain_high])["tech"]) == []
```

Then add new tests at the end of the file:

```python
def test_group_splits_items_into_tech_and_news_boards(tmp_path):
    cfg = _cfg()
    blog = Item(id="1", title="Rails 8", url="https://x/1", source_type="rss",
                category="backend", published=NOW, summary="s", importance="high")
    news = Item(id="2", title="Breach", url="https://x/2", source_type="rss",
                category="security", published=NOW, summary="s",
                importance="high", severity="high")
    hn = Item(id="3", title="HN Story", url="https://x/3", source_type="social",
              category="backend", published=NOW, summary="s", importance="high")
    grouped = _group(_snap_with([blog, news, hn]), cfg)
    tech_titles = [c["title"] for b in grouped["tech"].values() for c in b["cards"]]
    news_titles = [c["title"] for b in grouped["news"].values() for c in b["cards"]]
    assert tech_titles == ["Rails 8"]
    assert sorted(news_titles) == ["Breach", "HN Story"]


def test_digest_renders_both_tabs_with_tech_default(tmp_path):
    out = tmp_path / "output"
    tech = Item(id="1", title="Rails Release", url="https://x/1", source_type="rss",
                category="backend", published=NOW, summary="s", importance="high")
    news = Item(id="2", title="Krebs Story", url="https://x/2", source_type="rss",
                category="security", published=NOW, summary="s",
                importance="high", severity="high")
    snap_path = _write_snap(tmp_path, _snap_with([tech, news]))
    run_render(_cfg_sec(), snap_path, out, force=True)
    digest = (out / "digests" / "2026-07-17.html").read_text()
    assert "技術資訊" in digest and "新聞" in digest
    assert 'id="tab-tech"' in digest and "checked" in digest
    assert 'class="tab-panel tab-panel--tech"' in digest
    assert 'class="tab-panel tab-panel--news"' in digest
    assert "Rails Release" in digest and "Krebs Story" in digest


def test_digest_empty_board_shows_placeholder(tmp_path):
    out = tmp_path / "output"
    # only a tech item -> news board is empty
    tech = Item(id="1", title="Go Release", url="https://x/1", source_type="rss",
                category="backend", published=NOW, summary="s", importance="high")
    snap_path = _write_snap(tmp_path, _snap_with([tech]))
    run_render(_cfg_sec(), snap_path, out, force=True)
    digest = (out / "digests" / "2026-07-17.html").read_text()
    assert "本區本週無情資" in digest
```

Also add a `security`-aware cfg helper near `_cfg` at the top of the file (the default `_cfg` only lists `["backend"]`, so security-category news items would have nowhere to group):

```python
def _cfg_sec():
    return Config(general={"title": "Radar", "min_display_importance": "high"},
                  stack={}, categories=["backend", "security"], sources=[], llm={})
```

- [ ] **Step 2: Run the render tests to verify they fail**

Run: `.venv/bin/pytest tests/test_render.py -k "group_splits or renders_both_tabs or empty_board" -v`
Expected: FAIL — `_group` still returns the flat `{cat: {...}}` shape, so `grouped["tech"]` raises `KeyError`, and the tab markup is absent.

- [ ] **Step 3: Rewrite `_group()` to bucket by board**

Replace `_group` (lines 151-163) in `radar/pipeline/render.py` with:

```python
def _group(snapshot: dict, cfg) -> dict:
    """Route items into two boards (tech / news), then group each board by
    category. Returns {"tech": {cat: {cards, also_noted}}, "news": {...}}."""
    min_disp = cfg.general.get("min_display_importance", "high")
    threshold = IMPORTANCE_ORDER[min_disp]
    sections = {s: {c: {"cards": [], "also_noted": []} for c in cfg.categories}
                for s in ("tech", "news")}
    for it in _dedupe_by_content(snapshot["items"]):
        grouped = sections[_section(it)]
        bucket = grouped.setdefault(it["category"], {"cards": [], "also_noted": []})
        if IMPORTANCE_ORDER[it["importance"]] >= threshold:
            bucket["cards"].append(it)
        else:
            bucket["also_noted"].append(it)
    for grouped in sections.values():
        for b in grouped.values():
            b["cards"].sort(key=lambda it: _SEV.get(it.get("severity"), -1), reverse=True)
    return sections
```

- [ ] **Step 4: Rewrite `render_digest()` to build the `boards` context**

Replace `render_digest` (lines 203-211) with:

```python
def render_digest(snapshot: dict, cfg, env: Environment,
                  prev: str | None = None, next: str | None = None) -> str:
    lookback = int(cfg.general.get("lookback_days", 7))
    period = _period(snapshot["meta"]["date"], lookback)
    sections = _group(snapshot, cfg)
    boards = []
    for key, label in (("tech", "技術資訊"), ("news", "新聞")):
        grouped = sections[key]
        boards.append({
            "key": key,
            "label": label,
            "grouped": grouped,
            "tally": _tally(grouped),
            "priority": _priority(grouped),
            "has_items": any(g["cards"] or g["also_noted"] for g in grouped.values()),
        })
    return env.get_template("digest.html.j2").render(
        snapshot=snapshot, cfg=cfg, boards=boards,
        period=period, prev=prev, next=next)
```

- [ ] **Step 5: Rewrite the `digest.html.j2` body**

Replace the entire contents of `radar/templates/digest.html.j2` with:

```jinja
<!doctype html>
<html lang="zh-Hant"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ cfg.general.get('title', 'Tech Radar') }} — Week {{ period.week }} ({{ period.start }} ~ {{ period.end }})</title>
<link rel="stylesheet" href="../styles.css">
</head>
<body>
<div class="topbar"><div class="topbar-inner">
  <a class="brand" href="../index.html">{{ cfg.general.get('title', 'Tech Radar') }}</a>
  <span class="wordmark">Intelligence Brief</span>
  <span class="spacer"></span>
  <a class="navlink" href="../index.html">Home</a>
  {% if prev %}<a class="navlink" href="{{ prev }}.html">← {{ prev }}</a>{% endif %}
  {% if next %}<a class="navlink" href="{{ next }}.html">{{ next }} →</a>{% endif %}
</div></div>

<div class="wrap">
  <header class="brief-head">
    <div class="kicker">Weekly Intelligence Brief · Week {{ period.week }}, {{ period.year }}</div>
    <h1>情資週報</h1>
    <div class="byline">{{ period.start }} ~ {{ period.end }}</div>
    {% if snapshot.digest_summary %}<div class="lede">{{ snapshot.digest_summary | md }}</div>{% endif %}
  </header>

  <div class="tabs">
    {% for b in boards %}
    <input type="radio" name="board" id="tab-{{ b.key }}" class="tab-radio"{% if loop.first %} checked{% endif %}>
    {% endfor %}
    {% for b in boards %}
    <label class="tab-label" for="tab-{{ b.key }}">{{ b.label }}</label>
    {% endfor %}

    <div class="panels">
    {% for b in boards %}
      <section class="tab-panel tab-panel--{{ b.key }}">
      {% if b.has_items %}
        <div class="kpi-row">
          <div class="kpi kpi--total"><span class="kpi-num">{{ b.tally.total }}</span><span class="kpi-label">TOTAL</span></div>
          <div class="kpi kpi--critical"><span class="kpi-num">{{ b.tally.critical }}</span><span class="kpi-label">CRITICAL</span></div>
          <div class="kpi kpi--high"><span class="kpi-num">{{ b.tally.high }}</span><span class="kpi-label">HIGH</span></div>
          <div class="kpi kpi--medium"><span class="kpi-num">{{ b.tally.medium }}</span><span class="kpi-label">MEDIUM</span></div>
          <div class="kpi kpi--normal"><span class="kpi-num">{{ b.tally.normal }}</span><span class="kpi-label">NORMAL</span></div>
        </div>
        <div class="counts">
          {% for cat in cfg.categories %}
            {% set n = b.grouped[cat].cards|length + b.grouped[cat].also_noted|length %}
            {% if n %}<span class="count-chip"><strong>{{ n }}</strong> {{ cat }}</span>{% endif %}
          {% endfor %}
        </div>

        {% if b.priority %}
        <section class="priority">
          <h2 class="section-head">⚠ Priority Intelligence</h2>
          <ul class="priority-list">
            {% for it in b.priority %}
            {% set lvl = it | level %}
            <li class="priority-row priority-row--{{ lvl }}">
              <span class="level level--{{ lvl }}">{{ lvl }}</span>
              <span class="pill pill--stack">{{ it.category }}</span>
              <a class="priority-title" href="{{ it.url | safe_url }}">{{ it.title }}</a>
              <span class="when">{{ it.published[:10] }}</span>
            </li>
            {% endfor %}
          </ul>
        </section>
        {% endif %}

        <h2 class="section-head">By Category</h2>
        {% for cat in cfg.categories %}
          {% set cards = b.grouped[cat].cards %}
          {% set noted = b.grouped[cat].also_noted %}
          {% if cards or noted %}
          <details class="category" open>
            <summary class="cat-head">{{ cat }}<span class="cat-count">{{ cards|length + noted|length }}</span></summary>
            <div class="cards">
            {% for it in cards %}
            {% set lvl = it | level %}
            <article class="card card--{{ lvl }}">
              {% if lvl != 'normal' %}<span class="level level--{{ lvl }}">{{ lvl }}</span>{% endif %}
              <div class="meta">
                {% if it.provider %}<span class="pill pill--provider">{{ it.provider }}</span>{% endif %}
                {% if it.stack_match %}<span class="pill pill--stack">stack: {{ it.stack_match|join(', ') }}</span>{% endif %}
                <span class="when">{{ it.published[:10] }}</span>
              </div>
              <h3><a href="{{ it.url | safe_url }}">{{ it.title }}</a></h3>
              <div class="summary">{{ (it.llm.summary if it.llm else it.summary) | md }}</div>
              {% set why = it.llm.why_it_matters if it.llm else '' %}
              {% if why %}
              <div class="why"><span class="label">Why it matters</span>{{ why | md }}</div>
              {% endif %}
              {% if it.llm and it.llm.recommended_action %}
              <div class="action"><span class="label">Recommended action</span>{{ it.llm.recommended_action | md }}</div>
              {% endif %}
              {% set detail = it.llm.detail if it.llm else it.summary %}
              {% if detail %}
              <details class="detail">
                <summary>展開詳細</summary>
                <div class="detail-body">{{ detail | md }}</div>
              </details>
              {% endif %}
            </article>
            {% endfor %}
            </div>
            {% if noted %}
            <details class="also-noted">
              <summary>Also noted ({{ noted|length }})</summary>
              <ul>
                {% for it in noted %}<li><a href="{{ it.url | safe_url }}">{{ it.title }}</a> <span class="when">— {{ it.published[:10] }}</span></li>{% endfor %}
              </ul>
            </details>
            {% endif %}
          </details>
          {% endif %}
        {% endfor %}
      {% else %}
        <p class="empty-board">本區本週無情資</p>
      {% endif %}
      </section>
    {% endfor %}
    </div>
  </div>

  <footer>Generated by tech-radar · Week {{ period.week }} · {{ period.start }} ~ {{ period.end }}</footer>
</div>
</body></html>
```

- [ ] **Step 6: Run the full render test module to verify it passes**

Run: `.venv/bin/pytest tests/test_render.py -v`
Expected: PASS (all existing tests plus the new ones — every fixture item in the pre-existing `run_render` tests classifies as tech, so their content lands in the default tech panel and their string assertions still hold).

- [ ] **Step 7: Commit**

```bash
git add radar/pipeline/render.py radar/templates/digest.html.j2 tests/test_render.py
git commit -m "feat: render digest as tech/news tab boards, each with own KPI + priority"
```

---

### Task 3: CSS-only tab show/hide styling

**Files:**
- Modify: `radar/templates/styles.css` (append a `tab boards` block)
- Test: `tests/test_render.py::test_stylesheet_drops_serif_and_styles_new_hooks`

**Interfaces:**
- Consumes: DOM hooks emitted by Task 2 (`.tabs`, `.tab-radio`, `.tab-label`, `#tab-tech`, `#tab-news`, `.panels`, `.tab-panel--tech`, `.tab-panel--news`, `.empty-board`).
- Produces: only the default board's panel is visible; the checked tab's label is highlighted.

- [ ] **Step 1: Extend the stylesheet hook test**

In `tests/test_render.py`, in `test_stylesheet_drops_serif_and_styles_new_hooks`, extend the hook tuple to include the tab hooks:

```python
    for hook in (".kpi-row", ".kpi--critical", ".priority-row", ".priority-row--high", ".section-head",
                 ".latest-card", ".tab-label", ".tab-panel", ".empty-board"):
        assert hook in css
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/pytest tests/test_render.py::test_stylesheet_drops_serif_and_styles_new_hooks -v`
Expected: FAIL on the `.tab-label` / `.tab-panel` / `.empty-board` assertions (not yet in the stylesheet).

- [ ] **Step 3: Append the tab styling to `styles.css`**

Add to the end of `radar/templates/styles.css`:

```css
/* --- tab boards (CSS-only, no JS): 技術資訊 / 新聞 --- */
.tab-radio { position: absolute; width: 1px; height: 1px; opacity: 0; pointer-events: none; }
.tabs { margin-top: 1.6rem; }
.tab-label {
  display: inline-block;
  padding: .55rem 1.15rem;
  margin-right: .4rem;
  border: 1px solid var(--border);
  border-bottom: none;
  border-radius: var(--radius) var(--radius) 0 0;
  font-weight: 600;
  color: var(--muted);
  background: var(--surface-2);
  cursor: pointer;
}
.tab-label:hover { color: var(--text); }
/* the label immediately following its checked radio is the active tab */
#tab-tech:checked ~ label[for="tab-tech"],
#tab-news:checked ~ label[for="tab-news"] {
  color: var(--text);
  background: var(--surface);
  border-color: var(--accent);
}
.panels { border-top: 1px solid var(--border); padding-top: 1.4rem; }
.tab-panel { display: none; }
#tab-tech:checked ~ .panels .tab-panel--tech,
#tab-news:checked ~ .panels .tab-panel--news { display: block; }
.empty-board { color: var(--muted); text-align: center; padding: 2.4rem 0; }
```

- [ ] **Step 4: Run the stylesheet test to verify it passes**

Run: `.venv/bin/pytest tests/test_render.py::test_stylesheet_drops_serif_and_styles_new_hooks -v`
Expected: PASS.

- [ ] **Step 5: Run the whole suite**

Run: `.venv/bin/pytest -q`
Expected: PASS (all modules).

- [ ] **Step 6: Commit**

```bash
git add radar/templates/styles.css tests/test_render.py
git commit -m "feat: CSS-only tab show/hide for tech/news digest boards"
```

---

## Verification (after all tasks)

- [ ] `.venv/bin/pytest -q` is fully green.
- [ ] Regenerate a digest against the real config and eyeball it (dark + light):
  `.venv/bin/python radar.py render --force` (or the project's render entrypoint), then open `output/digests/<latest>.html` — confirm the 技術資訊 tab shows on load, clicking 新聞 swaps panels, and each board carries its own KPI + Priority.

## Out of scope (leave for the human)

- Archive `index.html` per-board counts (deferred by design).
- Any config / adapter / fetch changes.
