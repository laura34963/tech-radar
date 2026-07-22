# Intelligence Brief UI Redesign — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign the rendered digest and index from an editorial magazine layout into a professional executive intelligence brief — KPI severity tiles, an auto-surfaced priority block, and refined category cards — without touching the fetch/enrich/rank pipeline.

**Architecture:** `render_digest` gains two precomputed context values (`tally`, `priority`) so display logic stays out of Jinja. The two templates (`digest.html.j2`, `index.html.j2`) and the shared `styles.css` are rewritten to the new visual system. `styles.css` is copied from `radar/templates/` to `output/` by `run_render` (render.py:248), so editing the template source is sufficient.

**Tech Stack:** Python 3.11+ (3.13), Jinja2 (autoescape on), MarkupSafe, vanilla JS, hand-written CSS. Tests: pytest.

## Global Constraints

- Static, self-contained site — no build step, no framework, no external assets (no CDN fonts/scripts/images). Copy verbatim from spec.
- Rendering stays Jinja2 templates + one `styles.css` + only vanilla JS.
- Theme-aware: light + dark via `@media (prefers-color-scheme: dark)`.
- Content is Traditional Chinese; deploys to GitHub Pages.
- Same source data — no fields dropped. No change to `min_display_importance` threshold, dedupe, or grouping.
- Severity palette unchanged: critical=red, high=orange, medium=amber, low=slate. Existing CSS token names (`--sev-critical` etc.) are reused.
- Effective level of an item is `it["severity"] or it["importance"]`. Severity may be `None`; importance is always one of `low|medium|high` (and `critical` when set). Sort order via `_SEV = {"critical":3,"high":2,"medium":1,"low":0,None:-1}` (already defined in render.py:15).

---

### Task 1: Precompute `tally` and `priority` in the render context

**Files:**
- Modify: `radar/pipeline/render.py` (add two helpers near `_group` around line 150–162; extend `render_digest` at 165–171)
- Test: `tests/test_render.py`

**Interfaces:**
- Consumes: `_group(snapshot, cfg) -> dict[str, {"cards": list, "also_noted": list}]` (existing); module-level `_SEV` (existing).
- Produces:
  - `_tally(grouped) -> dict` returning exactly `{"total": int, "critical": int, "high": int, "medium": int, "low": int}`, counting each **card** (not also_noted) by effective level (`it["severity"] or it["importance"]`).
  - `_priority(grouped) -> list[dict]` returning the displayed cards whose effective level is `"critical"` or `"high"`, each dict being the original item with an added `"category"` key, sorted highest effective severity first (stable within a level).
  - `render_digest(...)` additionally passes `tally=` and `priority=` to the template.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_render.py`:

```python
from radar.pipeline.render import _tally, _priority, _group


def _grouped_from(cfg, items):
    return _group(_snap_with(items), cfg)


def test_tally_counts_cards_by_effective_level():
    cfg = _cfg()
    crit = Item(id="1", title="c", url="https://x/1", source_type="security",
                category="backend", published=NOW, summary="s",
                importance="critical", severity="critical")
    high = Item(id="2", title="h", url="https://x/2", source_type="rss",
                category="backend", published=NOW, summary="s", importance="high")
    med = Item(id="3", title="m", url="https://x/3", source_type="rss",
               category="backend", published=NOW, summary="s", importance="medium")
    t = _tally(_grouped_from(cfg, [crit, high, med]))
    # medium is below min_display_importance="high" -> "also noted", not a card
    assert t == {"total": 2, "critical": 1, "high": 1, "medium": 0, "low": 0}


def test_priority_is_critical_and_high_sorted():
    cfg = _cfg()
    high = Item(id="1", title="H", url="https://x/1", source_type="rss",
                category="backend", published=NOW, summary="s", importance="high")
    crit = Item(id="2", title="C", url="https://x/2", source_type="security",
                category="backend", published=NOW, summary="s",
                importance="critical", severity="critical")
    pri = _priority(_grouped_from(cfg, [high, crit]))
    assert [p["title"] for p in pri] == ["C", "H"]      # critical before high
    assert all(p["category"] == "backend" for p in pri)


def test_priority_empty_when_no_high_or_critical():
    cfg = _cfg()
    high = Item(id="1", title="H", url="https://x/1", source_type="rss",
                category="backend", published=NOW, summary="s", importance="high")
    # only cards at/above threshold reach grouped["cards"]; drop the high one
    med_only = Item(id="2", title="M", url="https://x/2", source_type="rss",
                    category="backend", published=NOW, summary="s", importance="medium")
    assert _priority(_grouped_from(cfg, [med_only])) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_render.py -k "tally or priority" -v`
Expected: FAIL — `ImportError: cannot import name '_tally'`.

- [ ] **Step 3: Implement the helpers**

In `radar/pipeline/render.py`, add after `_group` (after line 162):

```python
def _level(it: dict) -> str:
    """Effective display level: explicit severity wins, else importance."""
    return it.get("severity") or it.get("importance")


def _tally(grouped: dict) -> dict:
    """Count displayed cards (not 'also noted') by effective level."""
    t = {"total": 0, "critical": 0, "high": 0, "medium": 0, "low": 0}
    for bucket in grouped.values():
        for it in bucket["cards"]:
            t["total"] += 1
            lvl = _level(it)
            if lvl in t:
                t[lvl] += 1
    return t


def _priority(grouped: dict) -> list[dict]:
    """Critical + high displayed cards across all categories, worst first.
    Each returned dict carries its 'category' so the priority row can label it."""
    out = []
    for cat, bucket in grouped.items():
        for it in bucket["cards"]:
            if _level(it) in ("critical", "high"):
                out.append({**it, "category": cat})
    out.sort(key=lambda it: _SEV.get(_level(it), -1), reverse=True)
    return out
```

Then extend `render_digest` (line 169–171) to pass them:

```python
    grouped = _group(snapshot, cfg)
    return env.get_template("digest.html.j2").render(
        snapshot=snapshot, cfg=cfg, grouped=grouped,
        tally=_tally(grouped), priority=_priority(grouped),
        period=period, prev=prev, next=next)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_render.py -k "tally or priority" -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Run the full render suite (nothing regressed)**

Run: `python -m pytest tests/test_render.py -v`
Expected: PASS (all existing tests still green — the template still renders because `tally`/`priority` are only *added* context).

- [ ] **Step 6: Commit**

```bash
git add radar/pipeline/render.py tests/test_render.py
git commit -m "feat: precompute severity tally and priority list for the digest"
```

---

### Task 2: Redesign the digest template

**Files:**
- Modify: `radar/templates/digest.html.j2` (full rewrite of the body; keep the `<head>`/topbar structure and all Jinja filters `md`, `safe_url`)
- Test: `tests/test_render.py`

**Interfaces:**
- Consumes from Task 1: `tally` dict (`tally.total`, `tally.critical`, `tally.high`, `tally.medium`, `tally.low`) and `priority` list (each item has `.category`, `.severity`/`.importance`, `.title`, `.url`, `.published`, `.llm`/`.summary`). Also existing context: `snapshot`, `cfg`, `grouped`, `period`, `prev`, `next`.
- Produces: HTML containing stable class hooks the CSS (Task 4) targets: `.brief-head`, `.kpi-row`, `.kpi`, `.kpi--critical/high/medium/low`, `.priority`, `.priority-row`, and the reused card classes `.card`, `.card--<level>`, `.level--<level>`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_render.py`:

```python
def test_digest_shows_kpi_tiles(tmp_path):
    out = tmp_path / "output"
    high = Item(id="1", title="Big News", url="https://x/1", source_type="rss",
                category="backend", published=NOW, summary="s", importance="high")
    snap_path = _write_snap(tmp_path, _snap_with([high]))
    run_render(_cfg(), snap_path, out, force=True)
    digest = (out / "digests" / "2026-07-17.html").read_text()
    assert 'class="kpi-row"' in digest
    assert "TOTAL" in digest and "HIGH" in digest


def test_digest_priority_block_lists_high_items(tmp_path):
    out = tmp_path / "output"
    high = Item(id="1", title="Urgent Advisory", url="https://x/1", source_type="rss",
                category="backend", published=NOW, summary="s", importance="high")
    snap_path = _write_snap(tmp_path, _snap_with([high]))
    run_render(_cfg(), snap_path, out, force=True)
    digest = (out / "digests" / "2026-07-17.html").read_text()
    assert 'class="priority"' in digest
    assert digest.count("Urgent Advisory") == 2   # priority row + full card


def test_digest_priority_block_omitted_when_none(tmp_path):
    out = tmp_path / "output"
    # a card at threshold "high" but importance exactly "high" is priority;
    # use a lower cfg threshold so a medium card shows but is NOT priority.
    cfg = Config(general={"title": "Radar", "min_display_importance": "medium"},
                 stack={}, categories=["backend"], sources=[], llm={})
    med = Item(id="1", title="Routine", url="https://x/1", source_type="rss",
               category="backend", published=NOW, summary="s", importance="medium")
    snap_path = _write_snap(tmp_path, _snap_with([med]))
    run_render(cfg, snap_path, out, force=True)
    digest = (out / "digests" / "2026-07-17.html").read_text()
    assert 'class="priority"' not in digest
    assert "Routine" in digest    # still shown as a card
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_render.py -k "digest_shows_kpi or priority_block" -v`
Expected: FAIL — `assert 'class="kpi-row"' in digest` fails (old template has no KPI row).

- [ ] **Step 3: Rewrite `radar/templates/digest.html.j2`**

Replace the file with:

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

    <div class="kpi-row">
      <div class="kpi kpi--total"><span class="kpi-num">{{ tally.total }}</span><span class="kpi-label">Total</span></div>
      <div class="kpi kpi--critical"><span class="kpi-num">{{ tally.critical }}</span><span class="kpi-label">Critical</span></div>
      <div class="kpi kpi--high"><span class="kpi-num">{{ tally.high }}</span><span class="kpi-label">High</span></div>
      <div class="kpi kpi--medium"><span class="kpi-num">{{ tally.medium }}</span><span class="kpi-label">Medium</span></div>
      <div class="kpi kpi--low"><span class="kpi-num">{{ tally.low }}</span><span class="kpi-label">Low</span></div>
    </div>
    <div class="counts">
      {% for cat in cfg.categories %}
        {% set n = grouped[cat].cards|length + grouped[cat].also_noted|length %}
        {% if n %}<span class="count-chip"><strong>{{ n }}</strong> {{ cat }}</span>{% endif %}
      {% endfor %}
    </div>
  </header>

  {% if priority %}
  <section class="priority">
    <h2 class="section-head">⚠ Priority Intelligence</h2>
    <ul class="priority-list">
      {% for it in priority %}
      {% set lvl = it.severity or it.importance %}
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
    {% set cards = grouped[cat].cards %}
    {% set noted = grouped[cat].also_noted %}
    {% if cards or noted %}
    <details class="category" open>
      <summary class="cat-head">{{ cat }}<span class="cat-count">{{ cards|length + noted|length }}</span></summary>
      <div class="cards">
      {% for it in cards %}
      {% set lvl = it.severity or it.importance %}
      <article class="card card--{{ lvl }}">
        <span class="level level--{{ lvl }}">{{ lvl }}</span>
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

  <footer>Generated by tech-radar · Week {{ period.week }} · {{ period.start }} ~ {{ period.end }}</footer>
</div>
</body></html>
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_render.py -k "digest_shows_kpi or priority_block" -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Run the full render suite**

Run: `python -m pytest tests/test_render.py -v`
Expected: PASS. (The existing `test_render_produces_digest_and_index` still finds "Big News", "Minor", "Also noted".)

- [ ] **Step 6: Commit**

```bash
git add radar/templates/digest.html.j2 tests/test_render.py
git commit -m "feat: redesign digest page as an intelligence brief"
```

---

### Task 3: Redesign the index template

**Files:**
- Modify: `radar/templates/index.html.j2` (rewrite body; keep the vanilla-JS filter)
- Test: `tests/test_render.py`

**Interfaces:**
- Consumes existing index context: `cfg`, `digests` (each: `.date`, `.period.week/.start/.end`, `.count`, `.cats`, `.security`).
- Produces class hooks for CSS: `.archive-head`, `.latest-card`, `.latest-glance`, reused `.digest-row`, `.search`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_render.py`:

```python
def test_index_uses_archive_layout(tmp_path):
    out = tmp_path / "output"
    high = Item(id="1", title="Big News", url="https://x/1", source_type="rss",
                category="backend", published=NOW, summary="s", importance="high")
    snap_path = _write_snap(tmp_path, _snap_with([high]))
    run_render(_cfg(), snap_path, out, force=True)
    index = (out / "index.html").read_text()
    assert 'class="latest-card"' in index
    assert "Intelligence Archive" in index
    assert 'id="filter"' in index          # search retained
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_render.py -k index_uses_archive -v`
Expected: FAIL — `assert 'class="latest-card"' in index`.

- [ ] **Step 3: Rewrite `radar/templates/index.html.j2`**

Replace the file with:

```jinja
<!doctype html>
<html lang="zh-Hant"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ cfg.general.get('title', 'Tech Radar') }}</title>
<link rel="stylesheet" href="styles.css">
</head>
<body>
<div class="topbar"><div class="topbar-inner">
  <span class="brand">{{ cfg.general.get('title', 'Tech Radar') }}</span>
  <span class="wordmark">Intelligence Archive</span>
</div></div>

<div class="wrap">
  <header class="archive-head">
    <div class="kicker">Intelligence Archive</div>
    <h1>{{ cfg.general.get('title', 'Tech Radar') }}</h1>
    <p>The latest across backend, frontend, devops, cloud &amp; security — ranked for your stack.</p>
  </header>

  {% if digests %}
  {% set d0 = digests[0] %}
  <a class="latest-card" href="digests/{{ d0.date }}.html">
    <div class="latest-label">Latest Brief</div>
    <div class="latest-title">Week {{ d0.period.week }} · {{ d0.period.start }} ~ {{ d0.period.end }}</div>
    <div class="latest-glance">
      <span class="glance"><strong>{{ d0.count }}</strong> items</span>
      {% if d0.security %}<span class="sev sev-high">security {{ d0.security }}</span>{% endif %}
    </div>
  </a>
  {% endif %}

  <input id="filter" class="search" placeholder="Filter briefs by week, date or category…" oninput="f()" aria-label="Filter briefs">

  <ul id="list" class="digest-list">
  {% for d in digests %}
    <li class="digest-row" data-text="week {{ d.period.week }} {{ d.period.start }} {{ d.period.end }} {{ d.cats|join(' ') }}">
      <a class="d-week" href="digests/{{ d.date }}.html">Week {{ d.period.week }}</a>
      <span class="d-range">{{ d.period.start }} ~ {{ d.period.end }}</span>
      <span class="d-count">{{ d.count }} items</span>
      <span class="d-cats">
        {% if d.security %}<span class="sev sev-high">security {{ d.security }}</span>{% endif %}
        {% for c in d.cats %}<span class="pill pill--stack">{{ c }}</span>{% endfor %}
      </span>
    </li>
  {% endfor %}
  </ul>

  <footer>Generated by tech-radar</footer>
</div>

<script>
function f(){var q=document.getElementById('filter').value.toLowerCase();
document.querySelectorAll('#list li').forEach(function(li){
li.style.display=li.dataset.text.toLowerCase().includes(q)?'':'none';});}
</script>
</body></html>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_render.py -k index_uses_archive -v`
Expected: PASS.

- [ ] **Step 5: Run the full render suite**

Run: `python -m pytest tests/test_render.py -v`
Expected: PASS (existing index test still finds "2026-07-17").

- [ ] **Step 6: Commit**

```bash
git add radar/templates/index.html.j2 tests/test_render.py
git commit -m "feat: redesign index as an intelligence archive"
```

---

### Task 4: Rewrite the stylesheet to the intelligence-brief visual system

**Files:**
- Modify: `radar/templates/styles.css` (visual overhaul; keep the `:root` token names and dark-mode block)
- Test: `tests/test_render.py`

**Interfaces:**
- Consumes: all class hooks emitted by Tasks 2 & 3 (`.brief-head`, `.kpi-row`, `.kpi`, `.kpi--*`, `.priority`, `.priority-row`, `.priority-row--*`, `.section-head`, `.wordmark`, `.archive-head`, `.latest-card`, `.latest-glance`, `.glance`) plus the reused `.card`, `.level--*`, `.sev-*`, `.pill*`, `.digest-row`, `.search` classes.
- Produces: the final `output/styles.css` (copied by `run_render` at render.py:248).

- [ ] **Step 1: Write the failing test (asserts the visual-direction decisions)**

Add to `tests/test_render.py`:

```python
def test_stylesheet_drops_serif_and_styles_new_hooks(tmp_path):
    out = tmp_path / "output"
    it = Item(id="1", title="X", url="https://x", source_type="rss",
              category="backend", published=NOW, summary="s", importance="high")
    snap_path = _write_snap(tmp_path, _snap_with([it]))
    run_render(_cfg(), snap_path, out, force=True)
    css = (out / "styles.css").read_text()
    # magazine serif display font is gone (spec: sans everywhere)
    assert "Iowan Old Style" not in css and "Palatino" not in css
    # new structural hooks are styled
    for hook in (".kpi-row", ".kpi--critical", ".priority-row", ".section-head",
                 ".latest-card"):
        assert hook in css
    # dark theme retained
    assert "prefers-color-scheme: dark" in css
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_render.py -k stylesheet_drops_serif -v`
Expected: FAIL — `assert "Iowan Old Style" not in css` fails (current CSS defines it) and `.kpi-row` is absent.

- [ ] **Step 3: Rewrite `radar/templates/styles.css`**

Replace the file with the stylesheet below. It reuses the existing color tokens and dark-mode block, drops `--font-display` (serif), keeps `--font-mono` for KPI numbers, and styles every new hook. Card/pill/detail/also-noted/topbar rules from the current sheet are preserved (only the header/hero/serif rules change), so existing content still renders.

```css
/* tech-radar — intelligence-brief stylesheet. Self-contained, theme-aware. */

:root {
  --bg: #f4f6f8;
  --surface: #ffffff;
  --surface-2: #eef1f5;
  --text: #131820;
  --muted: #5b6672;
  --border: #dde2e8;
  --accent: #1d4ed8;
  --accent-weak: #e8eefc;
  --action: #15803d;
  --action-weak: #e9f7ee;
  --header-band: #0f172a;
  --header-text: #e6eaf0;
  --sev-critical: #dc2626;
  --sev-high: #ea580c;
  --sev-medium: #b45309;
  --sev-low: #64748b;
  --shadow: 0 1px 2px rgba(16,24,40,.06), 0 1px 3px rgba(16,24,40,.08);
  --shadow-hover: 0 4px 12px rgba(16,24,40,.10), 0 2px 4px rgba(16,24,40,.06);
  --radius: 10px;
  --font-body: system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, "PingFang TC", "Microsoft JhengHei", sans-serif;
  --font-mono: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
}

@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0e1116;
    --surface: #161b22;
    --surface-2: #1c232c;
    --text: #e6eaf0;
    --muted: #9aa5b3;
    --border: #29313b;
    --accent: #6ea8fe;
    --accent-weak: #16233b;
    --action: #4ade80;
    --action-weak: #14261c;
    --header-band: #0a0e14;
    --header-text: #e6eaf0;
    --sev-critical: #f87171;
    --sev-high: #fb923c;
    --sev-medium: #fbbf24;
    --sev-low: #94a3b8;
    --shadow: 0 1px 2px rgba(0,0,0,.4);
    --shadow-hover: 0 6px 18px rgba(0,0,0,.5);
  }
}

* { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; }
body {
  font-family: var(--font-body);
  background: var(--bg);
  color: var(--text);
  line-height: 1.6;
  margin: 0; padding: 0;
  font-size: 16px;
  -webkit-font-smoothing: antialiased;
}
.wrap { width: 100%; max-width: 1100px; margin: 0 auto; padding: 0 clamp(1.25rem, 5vw, 3rem) 4rem; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }

/* ---- top navigation (report header band) ---- */
.topbar {
  position: sticky; top: 0; z-index: 10;
  background: var(--header-band);
  border-bottom: 1px solid var(--border);
}
.topbar-inner {
  width: 100%; max-width: 1100px; margin: 0 auto;
  padding: .7rem clamp(1.25rem, 5vw, 3rem);
  display: flex; align-items: center; gap: 1rem; font-size: .9rem;
}
.topbar .brand { font-weight: 700; color: var(--header-text); letter-spacing: -.01em; }
.topbar .wordmark {
  font-family: var(--font-mono); font-size: .68rem; font-weight: 600;
  text-transform: uppercase; letter-spacing: .18em;
  color: var(--header-text); opacity: .6;
  border-left: 1px solid color-mix(in srgb, var(--header-text) 30%, transparent);
  padding-left: 1rem;
}
.topbar .spacer { flex: 1; }
.topbar a.navlink { color: color-mix(in srgb, var(--header-text) 75%, transparent); padding: .25rem .6rem; border-radius: 8px; }
.topbar a.navlink:hover { background: color-mix(in srgb, var(--header-text) 12%, transparent); color: var(--header-text); text-decoration: none; }

/* ---- brief / archive header ---- */
.brief-head, .archive-head { padding: 2.4rem 0 1.4rem; border-bottom: 1px solid var(--border); margin-bottom: 1.8rem; }
.kicker {
  font-family: var(--font-mono);
  text-transform: uppercase; letter-spacing: .16em;
  font-size: .72rem; font-weight: 600; color: var(--accent);
  margin-bottom: .7rem;
}
.brief-head h1, .archive-head h1 {
  font-family: var(--font-body); font-weight: 800;
  font-size: clamp(1.9rem, 4vw, 2.6rem); line-height: 1.1;
  letter-spacing: -.02em; margin: 0 0 .5rem;
}
.byline { color: var(--muted); font-size: .82rem; text-transform: uppercase; letter-spacing: .05em; }
.archive-head p { color: var(--muted); margin: .3rem 0 0; font-size: 1.05rem; }
.lede {
  margin: 1.2rem 0 0; max-width: 74ch;
  font-size: 1.12rem; line-height: 1.55; color: var(--text);
  border-left: 3px solid var(--accent); padding-left: 1.1rem;
}
.lede p { margin: 0; }

/* ---- KPI tiles ---- */
.kpi-row { display: flex; flex-wrap: wrap; gap: .6rem; margin: 1.4rem 0 .9rem; }
.kpi {
  flex: 1 1 5.5rem; min-width: 5rem;
  display: flex; flex-direction: column; align-items: flex-start;
  background: var(--surface); border: 1px solid var(--border);
  border-top: 3px solid var(--sev-low);
  border-radius: var(--radius); padding: .6rem .8rem;
  box-shadow: var(--shadow);
}
.kpi-num { font-family: var(--font-mono); font-size: 1.7rem; font-weight: 700; line-height: 1; }
.kpi-label { font-size: .66rem; text-transform: uppercase; letter-spacing: .1em; color: var(--muted); margin-top: .3rem; }
.kpi--total { border-top-color: var(--accent); }
.kpi--critical { border-top-color: var(--sev-critical); }
.kpi--high { border-top-color: var(--sev-high); }
.kpi--medium { border-top-color: var(--sev-medium); }
.kpi--low { border-top-color: var(--sev-low); }

/* ---- summary chips row ---- */
.counts { display: flex; flex-wrap: wrap; gap: .5rem; margin: 0; }
.count-chip { font-size: .78rem; color: var(--muted); background: var(--surface); border: 1px solid var(--border); border-radius: 999px; padding: .2rem .7rem; }
.count-chip strong { color: var(--text); }

/* ---- section heads ---- */
.section-head {
  font-size: .8rem; text-transform: uppercase; letter-spacing: .12em;
  font-weight: 700; color: var(--muted);
  margin: 2.2rem 0 1rem; display: flex; align-items: center; gap: .6rem;
}
.section-head::after { content: ""; flex: 1; height: 1px; background: var(--border); }

/* ---- priority intelligence ---- */
.priority { margin: 1.6rem 0 0; }
.priority-list { list-style: none; padding: 0; margin: 0; display: grid; gap: .5rem; }
.priority-row {
  display: flex; align-items: center; gap: .6rem; flex-wrap: wrap;
  background: var(--surface); border: 1px solid var(--border);
  border-left: 4px solid var(--sev-high);
  border-radius: var(--radius); padding: .6rem .9rem; box-shadow: var(--shadow);
}
.priority-row--critical { border-left-color: var(--sev-critical); background: color-mix(in srgb, var(--sev-critical) 6%, var(--surface)); }
.priority-row--high { border-left-color: var(--sev-high); }
.priority-title { font-weight: 600; color: var(--text); flex: 1 1 auto; }
.priority-title:hover { color: var(--accent); }
.priority-row .when { color: var(--muted); font-size: .78rem; }

/* ---- category section (collapsible) ---- */
details.category { margin: 1.4rem 0; }
summary.cat-head {
  list-style: none; cursor: pointer; user-select: none;
  font-size: .85rem; text-transform: uppercase; letter-spacing: .08em;
  color: var(--muted); margin: 0 0 1rem; display: flex; align-items: center; gap: .6rem;
}
summary.cat-head::-webkit-details-marker { display: none; }
summary.cat-head::before { content: "\25BE"; font-size: .85em; width: 1em; }
details.category:not([open]) > summary.cat-head::before { content: "\25B8"; }
details.category:not([open]) > summary.cat-head { margin-bottom: 0; }
summary.cat-head:hover { color: var(--text); }
summary.cat-head::after { content: ""; flex: 1; height: 1px; background: var(--border); }
.cat-count { color: var(--text); background: var(--surface-2); border: 1px solid var(--border); border-radius: 999px; padding: 0 .5rem; font-size: .95em; font-weight: 600; }

/* ---- card list ---- */
.cards { display: grid; grid-template-columns: 1fr; gap: 1rem; }
.card {
  position: relative; background: var(--surface);
  border: 1px solid var(--border); border-left: 4px solid var(--sev-low);
  border-radius: var(--radius); padding: 1.15rem 1.3rem; margin: 0;
  box-shadow: var(--shadow); transition: box-shadow .15s ease, transform .15s ease;
}
.card:hover { box-shadow: var(--shadow-hover); transform: translateY(-1px); }
.card--critical { border-left-color: var(--sev-critical); border-color: color-mix(in srgb, var(--sev-critical) 35%, var(--border)); background: color-mix(in srgb, var(--sev-critical) 7%, var(--surface)); }
.card--high     { border-left-color: var(--sev-high);     border-color: color-mix(in srgb, var(--sev-high) 30%, var(--border));     background: color-mix(in srgb, var(--sev-high) 6%, var(--surface)); }
.card--medium   { border-left-color: var(--sev-medium);   background: color-mix(in srgb, var(--sev-medium) 5%, var(--surface)); }
.card--low      { border-left-color: var(--sev-low);      background: var(--surface); }

.level {
  position: absolute; top: 1rem; right: 1.1rem;
  font-size: .62rem; font-weight: 800; text-transform: uppercase; letter-spacing: .08em;
  padding: .2rem .6rem; border-radius: 999px; color: #fff; line-height: 1.4;
}
.priority-row .level { position: static; }
.level--critical { background: var(--sev-critical); }
.level--high     { background: var(--sev-high); }
.level--medium   { background: var(--sev-medium); }
.level--low      { background: var(--sev-low); }

.card h3 { font-family: var(--font-body); font-weight: 700; font-size: 1.28rem; line-height: 1.25; margin: 0 0 .5rem; letter-spacing: -.01em; }
.card h3 a { color: var(--text); }
.card h3 a:hover { color: var(--accent); }

.meta { display: flex; flex-wrap: wrap; align-items: center; gap: .45rem; margin-bottom: .55rem; font-size: .78rem; padding-right: 6rem; }
.meta .when { color: var(--muted); margin-left: auto; }

.pill { display: inline-flex; align-items: center; gap: .3rem; font-size: .72rem; font-weight: 600; padding: .15rem .55rem; border-radius: 999px; background: var(--accent-weak); color: var(--accent); border: 1px solid color-mix(in srgb, var(--accent) 22%, transparent); }
.pill--provider { text-transform: uppercase; letter-spacing: .04em; }
.pill--stack { background: var(--surface-2); color: var(--muted); border-color: var(--border); }

.sev { display: inline-flex; font-size: .7rem; font-weight: 700; text-transform: uppercase; letter-spacing: .04em; padding: .15rem .55rem; border-radius: 999px; color: #fff; }
.sev-critical { background: var(--sev-critical); }
.sev-high { background: var(--sev-high); }
.sev-medium { background: var(--sev-medium); }
.sev-low { background: var(--sev-low); }

.card .summary { color: var(--text); font-size: 1rem; }
.card .summary p:first-child { margin-top: 0; }
.card .summary p:last-child { margin-bottom: 0; }

/* rendered-markdown content */
.md-content p, .summary p, .detail-body p, .why p, .action p { margin: .55rem 0; }
.md-content ul, .md-content ol, .summary ul, .summary ol, .detail-body ul, .detail-body ol, .action ul, .action ol { margin: .5rem 0; padding-left: 1.3rem; }
.md-content li, .detail-body li, .summary li, .action li { margin: .2rem 0; }
.summary code, .detail-body code, .why code, .action code, .lede code { font-family: var(--font-mono); font-size: .88em; background: var(--surface-2); border: 1px solid var(--border); border-radius: 5px; padding: .05em .35em; }
.detail-body h4, .detail-body h5 { font-family: var(--font-body); margin: .8rem 0 .3rem; font-size: 1rem; }

details.detail { margin-top: .7rem; }
details.detail > summary { cursor: pointer; color: var(--accent); font-size: .85rem; font-weight: 600; list-style: none; user-select: none; }
details.detail > summary::-webkit-details-marker { display: none; }
details.detail > summary::before { content: "\25B8  "; }
details.detail[open] > summary::before { content: "\25BE  "; }
.detail-body { color: var(--text); }

.label { display: block; font-family: var(--font-body); font-size: .68rem; font-weight: 700; text-transform: uppercase; letter-spacing: .09em; margin-bottom: .1rem; }
.why { margin-top: .8rem; color: var(--muted); }
.why .label { color: var(--muted); }
.why p { margin: .1rem 0 0; }
.action { margin-top: .85rem; background: var(--action-weak); border: 1px solid color-mix(in srgb, var(--action) 30%, transparent); border-left: 3px solid var(--action); border-radius: 8px; padding: .7rem .9rem; font-size: .96rem; }
.action .label { color: var(--action); }
.action p { margin: .1rem 0 0; }

/* ---- also noted ---- */
.also-noted { margin-top: 1rem; }
.also-noted > summary { cursor: pointer; color: var(--muted); font-size: .85rem; font-weight: 600; }
.also-noted ul { list-style: none; padding: .5rem 0 0; margin: 0; }
.also-noted li { padding: .3rem 0; border-top: 1px solid var(--border); font-size: .92rem; color: var(--muted); }
.also-noted li a { color: var(--text); }

/* ---- index / archive ---- */
.latest-card {
  display: block; margin: 1.6rem 0; padding: 1.1rem 1.3rem;
  background: var(--accent-weak); border: 1px solid color-mix(in srgb, var(--accent) 25%, transparent);
  border-left: 4px solid var(--accent); border-radius: var(--radius);
}
.latest-card:hover { text-decoration: none; box-shadow: var(--shadow-hover); }
.latest-label { font-family: var(--font-mono); font-size: .68rem; text-transform: uppercase; letter-spacing: .14em; color: var(--accent); }
.latest-title { font-size: 1.2rem; font-weight: 700; color: var(--text); margin: .25rem 0 .5rem; }
.latest-glance { display: flex; flex-wrap: wrap; gap: .6rem; align-items: center; font-size: .85rem; color: var(--muted); }
.latest-glance .glance strong { color: var(--text); }

.search { width: 100%; padding: .7rem .9rem; font-size: 1rem; color: var(--text); background: var(--surface); border: 1px solid var(--border); border-radius: 10px; margin: 1rem 0 1.5rem; }
.search:focus { outline: 2px solid var(--accent); outline-offset: 1px; border-color: var(--accent); }

.digest-list { list-style: none; padding: 0; margin: 0; }
.digest-row { display: flex; align-items: center; gap: .75rem; flex-wrap: wrap; padding: .8rem 1.1rem; background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); margin: .5rem 0; box-shadow: var(--shadow); transition: box-shadow .15s ease, transform .15s ease; }
.digest-row:hover { box-shadow: var(--shadow-hover); transform: translateY(-1px); }
.digest-row .d-week { font-weight: 700; font-size: 1.1rem; color: var(--text); }
.digest-row .d-range { color: var(--muted); font-size: .85rem; }
.digest-row .d-count { color: var(--muted); font-size: .85rem; }
.digest-row .d-cats { display: flex; flex-wrap: wrap; gap: .35rem; margin-left: auto; }

footer { margin-top: 3rem; padding-top: 1.5rem; border-top: 1px solid var(--border); color: var(--muted); font-size: .8rem; }

@media (max-width: 560px) {
  .brief-head h1, .archive-head h1 { font-size: 1.6rem; }
  .digest-row .d-cats { margin-left: 0; width: 100%; }
  .kpi { flex-basis: 4rem; }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_render.py -k stylesheet_drops_serif -v`
Expected: PASS.

- [ ] **Step 5: Run the FULL test suite**

Run: `python -m pytest -q`
Expected: PASS (all suites; no regressions across adapters/cli/render).

- [ ] **Step 6: Manual visual check**

```bash
python radar.py render --force
open output/index.html
```
Confirm in light and dark mode: KPI tiles read cleanly, the priority block appears (Week 30 has 2 high-severity items), category cards show why/action inline with a `展開詳細` expander, no serif headings remain, and no horizontal scroll on a narrow window.

- [ ] **Step 7: Commit**

```bash
git add radar/templates/styles.css tests/test_render.py
git commit -m "feat: intelligence-brief stylesheet (KPI tiles, priority block, sans headings)"
```

---

## Self-Review

**Spec coverage:**
- Visual language (drop serif, mono KPI numbers, keep palette, report surfaces) → Task 4.
- Top bar / brief header / executive lede → Task 2 + Task 4.
- KPI tile row + category chips → Task 2 (markup) + Task 4 (style) + Task 1 (`tally`).
- Priority Intelligence block (critical+high, omitted when none, repeats in categories) → Task 1 (`priority`) + Task 2 + Task 4.
- Refined category cards (why/action inline, detail behind expander, also-noted collapsed) → Task 2 + Task 4.
- Index archive layout (hero, latest card w/ glance, filter retained, restyled rows) → Task 3 + Task 4.
- Render-context change (`tally`, `priority`) → Task 1.
- Testing (existing pass, new assertions for tally/priority/omission) → every task's test steps + Task 4 Step 5 (full suite) & Step 6 (manual light/dark).
- Non-goals (no threshold/dedupe/config change, no deps) → honored; no task touches them.

**Placeholder scan:** none — every step has concrete code/commands and expected output.

**Type consistency:** `tally` keys (`total/critical/high/medium/low`) are identical across Task 1 producer, Task 2 template, and Task 4 test. `priority` item shape (original item + `category`) consistent across Task 1 and Task 2. Class hooks emitted in Tasks 2–3 exactly match those asserted/styled in Task 4. `_level` helper used by both `_tally` and `_priority`.
