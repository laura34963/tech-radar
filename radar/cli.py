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
                        format="%(levelname)s %(name)s: %(message)s")

    try:
        cfg = load_config(Path(args.config))
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2

    now = datetime.now(tz=timezone.utc)
    output = Path(args.output)
    snap_path = _snapshot_path(output, now)

    with httpx.Client() as client:
        if args.command in ("fetch", "run"):
            run_fetch(cfg, snap_path, now=now, client=client,
                      force=args.force, fresh=args.fresh)
        if args.command in ("enrich", "run"):
            provider = make_provider(cfg.llm, client=client)
            run_enrich(cfg, snap_path, provider=provider, force=args.force)
        if args.command in ("render", "run"):
            run_render(cfg, snap_path, output, force=args.force)
    return 0
