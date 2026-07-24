from __future__ import annotations
import hashlib
import json
import logging
import re
from datetime import date as _date, timedelta
from pathlib import Path
from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup, escape
from radar.item import IMPORTANCE_ORDER
from radar.store import load_snapshot, atomic_write_text, atomic_write_json

log = logging.getLogger("radar.render")
_TEMPLATES = Path(__file__).resolve().parent.parent / "templates"
_SEV = {"critical": 3, "high": 2, "medium": 1, "low": 0, None: -1}
_SAFE_SCHEMES = ("http://", "https://")


def _safe_url(u: str) -> str:
    """Allow-list http(s) URLs only; anything else (javascript:, data:, etc.) is defanged."""
    if isinstance(u, str) and u.strip().lower().startswith(_SAFE_SCHEMES):
        return u
    return "#"


# --- minimal, safe markdown subset (LLM output is untrusted) -----------------
# Strategy: HTML-escape the whole string FIRST (so any raw <script>/tags become
# inert), THEN insert only a fixed whitelist of tags for a markdown subset. No
# third-party markdown/sanitizer dependency, and safe by construction.
_RE_CODE = re.compile(r"`([^`]+)`")
_RE_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_RE_BOLD = re.compile(r"\*\*([^*]+?)\*\*")
_RE_ITALIC = re.compile(r"(?<![\*\w])\*([^*\n]+?)\*(?![\*\w])")
_RE_BULLET = re.compile(r"^[ \t]*[-*+][ \t]+(.*)$")
_RE_ORDERED = re.compile(r"^[ \t]*\d+\.[ \t]+(.*)$")
_RE_HEADING = re.compile(r"^[ \t]*(#{1,6})[ \t]+(.*)$")


def _md_inline(s: str) -> str:
    """Inline formatting on an already-HTML-escaped string."""
    s = _RE_CODE.sub(r"<code>\1</code>", s)
    s = _RE_LINK.sub(lambda m: '<a href="%s">%s</a>' % (_safe_url(m.group(2)), m.group(1)), s)
    s = _RE_BOLD.sub(r"<strong>\1</strong>", s)
    s = _RE_ITALIC.sub(r"<em>\1</em>", s)
    return s


def _md(text: str | None) -> Markup:
    """Render a small, safe markdown subset (paragraphs, lists, headings, bold,
    italic, inline code, http(s) links) to HTML. Untrusted input is escaped first."""
    if not text:
        return Markup("")
    esc = str(escape(text))
    out: list[str] = []
    para: list[str] = []
    items: list[str] = []
    list_tag: str | None = None

    def flush_para() -> None:
        if para:
            out.append("<p>" + _md_inline(" ".join(para)) + "</p>")
            para.clear()

    def flush_list() -> None:
        nonlocal list_tag
        if items:
            inner = "".join("<li>" + _md_inline(li) + "</li>" for li in items)
            out.append("<%s>%s</%s>" % (list_tag, inner, list_tag))
            items.clear()
            list_tag = None

    for line in esc.split("\n"):
        if not line.strip():
            flush_para()
            flush_list()
            continue
        mh, mb, mo = _RE_HEADING.match(line), _RE_BULLET.match(line), _RE_ORDERED.match(line)
        if mh:
            flush_para()
            flush_list()
            level = min(len(mh.group(1)) + 3, 6)  # '#' -> <h4>
            out.append("<h%d>%s</h%d>" % (level, _md_inline(mh.group(2).strip()), level))
        elif mb or mo:
            flush_para()
            tag = "ul" if mb else "ol"
            if list_tag and list_tag != tag:
                flush_list()
            list_tag = tag
            items.append((mb or mo).group(1).strip())
        else:
            para.append(line.strip())
    flush_para()
    flush_list()
    return Markup("".join(out))


def _env() -> Environment:
    env = Environment(loader=FileSystemLoader(str(_TEMPLATES)),
                      autoescape=select_autoescape(["html", "j2"]))
    env.filters["safe_url"] = _safe_url
    env.filters["md"] = _md
    env.filters["level"] = _level
    return env


def _period(date_str: str, lookback: int) -> dict:
    """The window a digest covers: [run-date − lookback, run-date], plus the ISO
    week number. Derived from the snapshot date (not the clock), so deterministic."""
    end = _date.fromisoformat(date_str)
    start = end - timedelta(days=lookback)
    iso = end.isocalendar()
    return {"start": start.isoformat(), "end": end.isoformat(),
            "week": iso[1], "year": iso[0]}


def snapshot_hash(snapshot: dict) -> str:
    payload = json.dumps({"items": snapshot["items"],
                          "digest_summary": snapshot.get("digest_summary")},
                         sort_keys=True)
    return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()[:16]


def _content_key(it: dict) -> str:
    """Identity of a story for display de-duplication: normalized title, else URL."""
    title = re.sub(r"\s+", " ", it.get("title") or "").strip().casefold()
    return title or (it.get("url") or "").strip().casefold()


def _dedupe_by_content(items: list[dict]) -> list[dict]:
    """Show a repeated story once. Items sharing a title (else URL) are collapsed
    to the highest-severity / most-recent copy, so the same news arriving from
    two sources (e.g. a CVE from both OSV and GHSA) is not listed twice. Purely a
    display concern — the snapshot keeps every item."""
    ranked = sorted(
        items,
        key=lambda it: (_SEV.get(it.get("severity"), -1),
                        IMPORTANCE_ORDER.get(it.get("importance", "low"), 0),
                        it.get("published", "")),
        reverse=True)
    seen: set[str] = set()
    out: list[dict] = []
    for it in ranked:
        key = _content_key(it)
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


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


def _section(it: dict) -> str:
    """Which board an item belongs to. An explicit source-level `board`
    ('tech'|'news') wins; otherwise derive by nature: social/community feeds
    are news, everything else (releases, blogs, cloud, advisory feeds) is tech.
    """
    board = it.get("board")
    if board in ("tech", "news"):
        return board
    return "news" if it.get("source_type") == "social" else "tech"


def _level(it: dict) -> str:
    """Alert tier for display. A security severity (or a critical ranking) sets
    the tier; anything that merely cleared the display-importance threshold is
    'normal'. Without this, a digest whose min_display_importance is 'high' would
    render every card as one urgent colour and flood the priority block."""
    sev = it.get("severity")
    if it.get("importance") == "critical" or sev == "critical":
        return "critical"
    if sev == "high":
        return "high"
    if sev == "medium":
        return "medium"
    return "normal"


def _tally(grouped: dict) -> dict:
    """Count displayed cards (not 'also noted') by alert tier."""
    t = {"total": 0, "critical": 0, "high": 0, "medium": 0, "normal": 0}
    for bucket in grouped.values():
        for it in bucket["cards"]:
            t["total"] += 1
            t[_level(it)] += 1
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


def render_index(output_dir: Path, env: Environment, cfg) -> str:
    data_dir = output_dir / "data"
    lookback = int(cfg.general.get("lookback_days", 7))
    digests = []
    for f in sorted(data_dir.glob("*.json"), reverse=True):
        snap = load_snapshot(f)
        items = snap.get("items", [])
        digests.append({
            "date": snap["meta"]["date"],
            "period": _period(snap["meta"]["date"], lookback),
            "count": len(items),
            "cats": sorted({it["category"] for it in items}),
            "security": sum(1 for it in items
                            if it.get("severity") in ("high", "critical")),
        })
    return env.get_template("index.html.j2").render(cfg=cfg, digests=digests)


def _render_one(date: str, cfg, env: Environment, output_dir: Path,
                dates: list[str], *, force: bool) -> None:
    """Render (or skip, per the hash-gate) the digest for a single date, using
    `dates` — the current full set of known dates — to compute its prev/next
    neighbors. Reads/writes only output_dir/data/<date>.json."""
    path = output_dir / "data" / f"{date}.json"
    snapshot = load_snapshot(path)
    if not snapshot:
        return
    h = snapshot_hash(snapshot)
    already = snapshot["meta"].get("rendered", {}).get(date)
    if not force and already == h:
        return
    idx = dates.index(date)
    prev = dates[idx - 1] if idx > 0 else None
    nxt = dates[idx + 1] if idx < len(dates) - 1 else None
    html = render_digest(snapshot, cfg, env, prev=prev, next=nxt)
    log.info("render: digest %s (%d item(s))", date, len(snapshot.get("items", [])))
    atomic_write_text(output_dir / "digests" / f"{date}.html", html)
    snapshot["meta"].setdefault("rendered", {})[date] = h
    atomic_write_json(path, snapshot)


def run_render(cfg, snapshot_path: Path, output_dir: Path, *, force: bool = False) -> None:
    snapshot = load_snapshot(snapshot_path)
    if not snapshot or "meta" not in snapshot:
        log.info("no snapshot at %s; run fetch first", snapshot_path)
        return
    date = snapshot["meta"]["date"]
    env = _env()
    data_dir = output_dir / "data"

    # ensure this snapshot is discoverable by the index and by _render_one
    atomic_write_json(data_dir / f"{date}.json", snapshot)
    dates = sorted(p.stem for p in data_dir.glob("*.json"))

    _render_one(date, cfg, env, output_dir, dates, force=force)

    # sync the (possibly updated) rendered-hash stamp back to the caller's
    # snapshot file, so resumability holds across separate run_render calls
    # even when snapshot_path lives outside output_dir/data.
    atomic_write_json(snapshot_path, load_snapshot(data_dir / f"{date}.json"))

    # A new date shifts the prev/next neighbors of its adjacent dates, whose
    # own content hash is unchanged — force-refresh just those two so their
    # nav links stay current (digests otherwise accrete daily and are never
    # revisited).
    idx = dates.index(date)
    prev_date = dates[idx - 1] if idx > 0 else None
    next_date = dates[idx + 1] if idx < len(dates) - 1 else None
    for neighbor in (prev_date, next_date):
        if neighbor:
            _render_one(neighbor, cfg, env, output_dir, dates, force=True)

    # index is a pure function of existing data snapshots — always safe to rebuild
    atomic_write_text(output_dir / "index.html", render_index(output_dir, env, cfg))
    atomic_write_text(output_dir / "styles.css", (_TEMPLATES / "styles.css").read_text())
    log.info("render: hub index rebuilt → %s/index.html", output_dir)
