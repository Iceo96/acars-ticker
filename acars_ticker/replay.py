"""
Replay mode: feed a stored acarsdec JSONL file through the same pipeline so
development and testing don't need the radio.

Each line is one acarsdec JSON datagram. We can replay at real time (honouring
inter-message gaps from timestamps) or as fast as possible.

Author: iceo96
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Awaitable, Callable

from .collector import parse_acarsdec_json

log = logging.getLogger("acars_ticker.replay")

MessageHandler = Callable[[dict[str, Any]], Awaitable[None]]


async def replay_file(
    path: Path,
    on_message: MessageHandler,
    speed: float = 1.0,
    realtime: bool = True,
    loop_forever: bool = False,
) -> int:
    """Replay a JSONL file. Returns count of messages fed.

    speed: multiplier for inter-message gaps (realtime only). 2.0 = twice as fast.
    realtime: if False, feed as fast as possible.
    loop_forever: restart from the top when the file ends (demo mode).
    """
    path = Path(path).expanduser()
    count = 0
    while True:
        prev_ts: float | None = None
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    log.warning("skipping bad JSON line")
                    continue
                msg = parse_acarsdec_json(obj)
                if not msg:
                    continue

                if realtime and prev_ts is not None and msg.get("ts"):
                    gap = (msg["ts"] - prev_ts) / max(speed, 0.001)
                    if 0 < gap < 30:  # cap long gaps so replay stays watchable
                        await asyncio.sleep(gap)
                if msg.get("ts"):
                    prev_ts = msg["ts"]

                # When looping, restamp to "now-ish" so stats/time windows work.
                if loop_forever:
                    import time as _t
                    msg["ts"] = _t.time()

                await on_message(msg)
                count += 1
        if not loop_forever:
            break
        await asyncio.sleep(1.0)
    return count
