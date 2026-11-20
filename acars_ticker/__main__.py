"""
acars-ticker command line.

    python -m acars_ticker                 # live: launch acarsdec + web UI
    python -m acars_ticker --replay f.jsonl  # offline replay through the pipeline
    python -m acars_ticker --scan          # rank active ACARS channels
    python -m acars_ticker --prune         # prune old rows and exit

Author: iceo96
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import uvicorn

from .config import DEFAULT_CONFIG_PATH, load_config
from .scan import run_scan
from .server import create_app


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="acars-ticker",
        description="Live ACARS ticker: decode VHF ACARS via acarsdec + RTL-SDR, "
                    "enrich with ADS-B, and serve a web dashboard.",
    )
    p.add_argument("-c", "--config", type=Path, default=None,
                   help=f"config file (default: {DEFAULT_CONFIG_PATH})")
    p.add_argument("-v", "--verbose", action="store_true", help="debug logging")

    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--scan", action="store_true",
                      help="run rtl_power, rank active channels, suggest config")
    mode.add_argument("--replay", type=Path, metavar="FILE.jsonl",
                      help="replay stored acarsdec JSON through the pipeline (no radio)")
    mode.add_argument("--prune", action="store_true",
                      help="prune messages older than retention and exit")

    p.add_argument("--scan-time", default=None,
                   help="rtl_power integration seconds for --scan (default from config)")
    p.add_argument("--replay-speed", type=float, default=1.0,
                   help="replay speed multiplier (default 1.0 = realtime)")
    p.add_argument("--replay-loop", action="store_true",
                   help="loop the replay file forever (demo mode)")

    p.add_argument("--host", default=None, help="web bind host (overrides config)")
    p.add_argument("--port", type=int, default=None, help="web port (overrides config)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _setup_logging(args.verbose)
    cfg = load_config(args.config)
    if args.host:
        cfg.host = args.host
    if args.port:
        cfg.port = args.port

    if args.scan:
        return run_scan(cfg, duration_each=args.scan_time)

    if args.prune:
        from .storage import Storage
        store = Storage(cfg.db_path)
        removed = store.prune(cfg.retention_days)
        store.close()
        print(f"Pruned {removed} messages older than {cfg.retention_days} days.")
        return 0

    if args.replay:
        if not args.replay.exists():
            print(f"Replay file not found: {args.replay}", file=sys.stderr)
            return 2
        app = create_app(cfg, mode="replay", replay_path=args.replay,
                         replay_speed=args.replay_speed, replay_loop=args.replay_loop)
    else:
        app = create_app(cfg, mode="live")

    logging.getLogger("acars_ticker").info(
        "Starting web UI on http://%s:%s", cfg.host, cfg.port
    )
    uvicorn.run(app, host=cfg.host, port=cfg.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
