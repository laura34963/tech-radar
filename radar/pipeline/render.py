from __future__ import annotations
import hashlib
import json
from pathlib import Path
from jinja2 import Environment, FileSystemLoader, select_autoescape
from radar.item import IMPORTANCE_ORDER
from radar.store import load_snapshot, atomic_write_text, atomic_write_json

_TEMPLATES = Path(__file__).resolve().parent.parent / "templates"
_SEV = {"critical": 3, "high": 2, "medium": 1, "low": 0, None: -1}


def _env() -> Environment:
    return Environment(loader=FileSystemLoader(str(_TEMPLATES)),
                       autoescape=select_autoescape(["html", "j2"]))


def snapshot_hash(snapshot: dict) -> str:
    payload = json.dumps({"items": snapshot["items"],
                          "digest_summary": snapshot.get("digest_summary")},
                         sort_keys=True)
    return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()[:16]


def _group(snapshot: dict, cfg) -> dict:
    min_disp = cfg.general.get("min_display_importance", "high")
    threshold = IMPORTANCE_ORDER[min_disp]
    grouped: dict[str, dict] = {c: {"cards": [], "also_noted": []} for c in cfg.categories}
    for it in snapshot["items"]:
        bucket = grouped.setdefault(it["category"], {"cards": [], "also_noted": []})
        if IMPORTANCE_ORDER[it["importance"]] >= threshold:
            bucket["cards"].append(it)
        else:
            bucket["also_noted"].append(it)
    for b in grouped.values():
        b["cards"].sort(key=lambda it: _SEV.get(it.get("severity"), -1), reverse=True)
    return grouped


def render_digest(snapshot: dict, cfg, env: Environment,
                  prev: str | None = None, next: str | None = None) -> str:
    return env.get_template("digest.html.j2").render(
        snapshot=snapshot, cfg=cfg, grouped=_group(snapshot, cfg), prev=prev, next=next)


def render_index(output_dir: Path, env: Environment, cfg) -> str:
    data_dir = output_dir / "data"
    digests = []
    for f in sorted(data_dir.glob("*.json"), reverse=True):
        snap = load_snapshot(f)
        items = snap.get("items", [])
        digests.append({
            "date": snap["meta"]["date"],
            "count": len(items),
            "cats": sorted({it["category"] for it in items}),
            "security": sum(1 for it in items
                            if it.get("severity") in ("high", "critical")),
        })
    return env.get_template("index.html.j2").render(cfg=cfg, digests=digests)


def run_render(cfg, snapshot_path: Path, output_dir: Path, *, force: bool = False) -> None:
    snapshot = load_snapshot(snapshot_path)
    date = snapshot["meta"]["date"]
    h = snapshot_hash(snapshot)
    env = _env()
    dates = sorted(p.stem for p in (output_dir / "data").glob("*.json"))
    # ensure this snapshot is discoverable by the index
    atomic_write_json(output_dir / "data" / f"{date}.json", snapshot)
    if date not in dates:
        dates = sorted(set(dates) | {date})
    idx = dates.index(date)
    prev = dates[idx - 1] if idx > 0 else None
    nxt = dates[idx + 1] if idx < len(dates) - 1 else None

    already = snapshot["meta"].get("rendered", {}).get(date)
    if force or already != h:
        html = render_digest(snapshot, cfg, env, prev=prev, next=nxt)
        atomic_write_text(output_dir / "digests" / f"{date}.html", html)
        snapshot["meta"].setdefault("rendered", {})[date] = h
        atomic_write_json(snapshot_path, snapshot)
        atomic_write_json(output_dir / "data" / f"{date}.json", snapshot)

    # index is a pure function of existing data snapshots — always safe to rebuild
    atomic_write_text(output_dir / "index.html", render_index(output_dir, env, cfg))
    atomic_write_text(output_dir / "styles.css", (_TEMPLATES / "styles.css").read_text())
