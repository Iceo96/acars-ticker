"""
Collector: launch acarsdec and ingest its JSON datagrams.

acarsdec is the demodulator black box. We run it in JSON/UDP output mode and
receive datagrams on a local UDP socket, parse them into our normalized message
shape, and hand them to an async callback.

If acarsdec dies or the dongle disappears, we relaunch with exponential backoff
and report "receiver down" via the status callback so the UI can show a banner.

We do NOT demodulate anything in Python — acarsdec does the MSK work.

Author: iceo96
"""
from __future__ import annotations

import asyncio
import json
import logging
import socket
from typing import Any, Awaitable, Callable

log = logging.getLogger("acars_ticker.collector")

MessageHandler = Callable[[dict[str, Any]], Awaitable[None]]
StatusHandler = Callable[[str, str], Awaitable[None]]  # (state, detail)


def build_acarsdec_cmd(cfg) -> list[str]:
    """Construct the acarsdec argv from config. Matches the spec's CLI:
    acarsdec [-p ppm] [-g gain] -r <idx> <freqs...> -j -N <port>
    """
    cmd = [cfg.acarsdec_bin]
    if cfg.ppm:
        cmd += ["-p", str(cfg.ppm)]
    if cfg.gain is not None:
        cmd += ["-g", str(cfg.gain)]
    cmd += ["-r", str(cfg.device_index)]
    cmd += [f"{f:g}" for f in cfg.channels]
    cmd += ["-j", "-N", str(cfg.udp_port)]
    return cmd


def parse_acarsdec_json(obj: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize an acarsdec JSON datagram into our message dict.

    acarsdec versions vary: some put ACARS fields at top level, some nest them.
    We look in both and coalesce.
    """
    if not isinstance(obj, dict):
        return None

    # Some versions wrap the decoded frame under a sub-key.
    inner = obj
    for key in ("acars", "vdl2", "data"):
        if isinstance(obj.get(key), dict):
            inner = {**obj, **obj[key]}
            break

    def pick(*keys, default=None):
        for k in keys:
            if k in obj and obj[k] not in (None, ""):
                return obj[k]
            if k in inner and inner[k] not in (None, ""):
                return inner[k]
        return default

    ts = pick("timestamp", "t", default=None)
    try:
        ts = float(ts) if ts is not None else None
    except (TypeError, ValueError):
        ts = None

    def as_float(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    def as_int(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    text = pick("text", "message", "msg_text")
    if isinstance(text, str):
        text = text.rstrip("\r\n")

    msg = {
        "ts": ts,
        "channel": as_int(pick("channel", "chn")),
        "freq": as_float(pick("freq", "frequency")),
        "level": as_float(pick("level", "sig_level")),
        "error": as_int(pick("error", "err")) or 0,
        "mode": pick("mode"),
        "label": pick("label"),
        "block_id": pick("block_id", "bid"),
        "ack": (str(pick("ack")) if pick("ack") not in (None, "") else None),
        "msgno": pick("msgno", "msg_no"),
        "tail": pick("tail", "registration", "reg"),
        "flight": pick("flight", "flight_id", "callsign"),
        "text": text,
        "raw": json.dumps(obj, separators=(",", ":")),
    }
    # Normalize registration: strip dots acarsdec sometimes includes (".G-EUYY")
    if msg["tail"]:
        msg["tail"] = str(msg["tail"]).strip().lstrip(".").upper() or None
    if msg["flight"]:
        msg["flight"] = str(msg["flight"]).strip().upper() or None
    return msg


class Collector:
    def __init__(self, cfg, on_message: MessageHandler, on_status: StatusHandler):
        self.cfg = cfg
        self.on_message = on_message
        self.on_status = on_status
        self._proc: asyncio.subprocess.Process | None = None
        self._sock: socket.socket | None = None
        self._stop = asyncio.Event()

    async def _emit_status(self, state: str, detail: str = "") -> None:
        try:
            await self.on_status(state, detail)
        except Exception:  # noqa: BLE001 - status must never crash the loop
            log.exception("status handler raised")

    def _open_socket(self) -> socket.socket:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", self.cfg.udp_port))
        s.setblocking(False)
        return s

    async def _reader_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        assert self._sock is not None
        buf = bytearray()
        while not self._stop.is_set():
            try:
                data = await loop.sock_recv(self._sock, 65535)
            except (BlockingIOError, InterruptedError):
                await asyncio.sleep(0.05)
                continue
            if not data:
                continue
            # Datagrams are usually one JSON object each, but handle NDJSON too.
            for line in data.split(b"\n"):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    log.debug("undecodable datagram: %r", line[:120])
                    continue
                msg = parse_acarsdec_json(obj)
                if msg:
                    await self.on_message(msg)

    async def run(self) -> None:
        """Supervise acarsdec with exponential backoff and feed the reader."""
        loop = asyncio.get_running_loop()
        backoff = 1.0
        try:
            self._sock = self._open_socket()
        except OSError as exc:
            await self._emit_status("down", f"cannot bind UDP {self.cfg.udp_port}: {exc}")
            raise

        reader_task = asyncio.create_task(self._reader_loop(loop))

        cmd = build_acarsdec_cmd(self.cfg)
        log.info("acarsdec command: %s", " ".join(cmd))

        while not self._stop.is_set():
            try:
                self._proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE,
                )
                await self._emit_status("up", "acarsdec running")
                backoff = 1.0  # reset once it starts cleanly

                rc = await self._proc.wait()
                if self._stop.is_set():
                    break
                stderr = b""
                if self._proc.stderr:
                    stderr = await self._proc.stderr.read()
                detail = stderr.decode(errors="replace").strip()[-200:]
                log.warning("acarsdec exited rc=%s: %s", rc, detail)
                await self._emit_status("down", f"acarsdec exited (rc={rc})")
            except FileNotFoundError:
                await self._emit_status(
                    "down", f"'{self.cfg.acarsdec_bin}' not found — is acarsdec installed?"
                )
            except Exception as exc:  # noqa: BLE001
                log.exception("acarsdec supervisor error")
                await self._emit_status("down", str(exc))

            if self._stop.is_set():
                break
            await self._emit_status("retry", f"relaunching in {backoff:.0f}s")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=backoff)
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2, 30.0)

        reader_task.cancel()
        try:
            await reader_task
        except asyncio.CancelledError:
            pass

    async def stop(self) -> None:
        self._stop.set()
        if self._proc and self._proc.returncode is None:
            try:
                self._proc.terminate()
                await asyncio.wait_for(self._proc.wait(), timeout=5)
            except (asyncio.TimeoutError, ProcessLookupError):
                try:
                    self._proc.kill()
                except ProcessLookupError:
                    pass
        if self._sock:
            self._sock.close()
