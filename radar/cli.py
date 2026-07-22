from __future__ import annotations
import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
import httpx
from radar.config import load_config, ConfigError
from radar.pipeline.fetch import run_fetch
from radar.pipeline.enrich import run_enrich
from radar.pipeline.render import run_render
from radar.llm.provider import make_provider

# Sent on every outbound request. A descriptive UA is standard etiquette for a
# feed crawler and is more robust than httpx's default `python-httpx/x.y`, which
# some feeds reject. Deliberately NOT a browser string: spoofing a browser trips
# bot-detection on some sources (e.g. CISA returns 403 to a fake browser UA).
# Per-request headers (e.g. the Reddit adapter's) still override this default.
_USER_AGENT = "tech-radar/1.0 (+https://github.com/laura34963/tech-radar)"


def _snapshot_path(output: Path, now: datetime) -> Path:
    return output / "data" / f"{now.date().isoformat()}.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="radar")
    parser.add_argument("command", choices=["fetch", "enrich", "render", "run"])
    parser.add_argument("--config", default="config/radar.toml")
    parser.add_argument("--output", default="output")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--fresh", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(levelname)s %(message)s")
    log = logging.getLogger("radar")

    try:
        cfg = load_config(Path(args.config))
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2

    now = datetime.now(tz=timezone.utc)
    output = Path(args.output)
    snap_path = _snapshot_path(output, now)
    log.info("tech-radar: command=%s  date=%s  output=%s",
             args.command, now.date().isoformat(), output)

    total_failure = False
    with httpx.Client(headers={"User-Agent": _USER_AGENT}) as client:
        if args.command in ("fetch", "run"):
            log.info("── fetch ──")
            snap = run_fetch(cfg, snap_path, now=now, client=client,
                             force=args.force, fresh=args.fresh)
            sources_status = snap.get("meta", {}).get("sources", {})
            total_failure = bool(cfg.sources) and bool(sources_status) and all(
                s.get("status") == "failed" for s in sources_status.values())
        if args.command in ("enrich", "run"):
            log.info("── enrich ──")
            provider = make_provider(cfg.llm, client=client)
            if provider is None:
                log.info("llm: disabled or no credentials → rule-based digest")
            else:
                log.info("llm: provider=%s", cfg.llm.get("provider", "?"))
            run_enrich(cfg, snap_path, provider=provider, force=args.force)
        if args.command in ("render", "run"):
            log.info("── render ──")
            run_render(cfg, snap_path, output, force=args.force)

    if total_failure:
        log.error("all sources failed — digest is empty (exit 1)")
        return 1
    log.info("done → open %s/index.html", output)
    return 0
