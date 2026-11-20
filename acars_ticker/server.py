"""
FastAPI + WebSocket server for acars-ticker.

Serves the single-page UI, pushes live messages / enrichment / status over a
WebSocket, and exposes small REST endpoints for history and stats. The server
owns the pipeline and the ingest source (live collector or replay).

Localhost only, no auth by design.

Author: iceo96
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from .collector import Collector
from .enrichment import Enricher
from .pipeline import Pipeline
from .replay import replay_file
from .storage import Storage

log = logging.getLogger("acars_ticker.server")
WEB_DIR = Path(__file__).parent / "web"


class ConnectionManager:
    def __init__(self) -> None:
        self._conns: set[WebSocket] = set()
        self._lock = asyncio.Lock()
        self.receiver_state: dict[str, str] = {"state": "starting", "detail": ""}

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._conns.add(ws)

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._conns.discard(ws)

    async def broadcast(self, message: dict[str, Any]) -> None:
        async with self._lock:
            targets = list(self._conns)
        dead = []
        for ws in targets:
            try:
                await ws.send_json(message)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self._conns.discard(ws)


def create_app(
    cfg,
    mode: str = "live",
    replay_path: Path | None = None,
    replay_speed: float = 1.0,
    replay_loop: bool = False,
) -> FastAPI:
    app = FastAPI(title="acars-ticker", docs_url=None, redoc_url=None)
    manager = ConnectionManager()
    storage = Storage(cfg.db_path)
    enricher = Enricher(
        cfg.resolved_adsb_base_url,
        cache_seconds=cfg.enrich_cache_seconds,
        timeout=cfg.enrich_timeout,
    )
    pipeline = Pipeline(storage, enricher, manager.broadcast)

    state: dict[str, Any] = {"tasks": [], "collector": None}

    async def on_status(st: str, detail: str) -> None:
        manager.receiver_state = {"state": st, "detail": detail}
        await manager.broadcast({"type": "status", "data": manager.receiver_state})

    async def prune_loop() -> None:
        while True:
            try:
                removed = storage.prune(cfg.retention_days)
                if removed:
                    log.info("pruned %s old messages", removed)
            except Exception:  # noqa: BLE001
                log.exception("prune failed")
            await asyncio.sleep(6 * 3600)

    @app.on_event("startup")
    async def _startup() -> None:
        state["tasks"].append(asyncio.create_task(prune_loop()))
        if mode == "replay":
            manager.receiver_state = {"state": "replay", "detail": str(replay_path)}

            async def _run_replay() -> None:
                await on_status("replay", f"replaying {Path(replay_path).name}")
                n = await replay_file(
                    replay_path, pipeline.handle,
                    speed=replay_speed, realtime=True, loop_forever=replay_loop,
                )
                log.info("replay finished after %s messages", n)
                if not replay_loop:
                    await on_status("replay-done", f"replayed {n} messages")

            state["tasks"].append(asyncio.create_task(_run_replay()))
        else:
            collector = Collector(cfg, pipeline.handle, on_status)
            state["collector"] = collector
            state["tasks"].append(asyncio.create_task(collector.run()))

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        if state["collector"]:
            await state["collector"].stop()
        for t in state["tasks"]:
            t.cancel()
        for t in state["tasks"]:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        await enricher.close()
        storage.close()

    # ---- REST ----
    @app.get("/api/recent")
    async def api_recent(limit: int = 200, bucket: str | None = None) -> dict[str, Any]:
        limit = max(1, min(limit, 1000))
        rows = storage.recent(limit=limit, bucket=bucket)
        rows.reverse()  # oldest first so client prepends newest correctly
        return {"messages": [pipeline.payload_for(r) for r in rows]}

    @app.get("/api/stats")
    async def api_stats() -> dict[str, Any]:
        s = storage.stats()
        s["receiver"] = manager.receiver_state
        s["channels"] = [f"{f:g}" for f in cfg.channels]
        return s

    @app.get("/api/config")
    async def api_config() -> dict[str, Any]:
        return {
            "home": {"lat": cfg.home_lat, "lon": cfg.home_lon, "zoom": cfg.home_zoom},
            "geolocate": cfg.geolocate,
            "show_traffic": cfg.show_traffic,
            "traffic_radius_nm": cfg.traffic_radius_nm,
            "channels": [f"{f:g}" for f in cfg.channels],
            "mode": mode,
        }

    # Cache the area query briefly so many browser tabs share one upstream call.
    _traffic_cache: dict[str, Any] = {"at": 0.0, "lat": None, "lon": None, "data": []}

    @app.get("/api/traffic")
    async def api_traffic(lat: float | None = None, lon: float | None = None) -> dict[str, Any]:
        import time as _t
        if not cfg.show_traffic:
            return {"aircraft": [], "enabled": False}
        clat = lat if lat is not None else cfg.home_lat
        clon = lon if lon is not None else cfg.home_lon
        now = _t.time()
        moved = (_traffic_cache["lat"] is None
                 or abs(clat - _traffic_cache["lat"]) > 0.2
                 or abs(clon - _traffic_cache["lon"]) > 0.2)
        if now - _traffic_cache["at"] < 10 and not moved:
            return {"aircraft": _traffic_cache["data"], "enabled": True, "cached": True}
        ac = await enricher.nearby(clat, clon, cfg.traffic_radius_nm)
        _traffic_cache.update(at=now, lat=clat, lon=clon, data=ac)
        return {"aircraft": ac, "enabled": True}

    @app.get("/api/aircraft")
    async def api_aircraft() -> dict[str, Any]:
        rows = storage.aircraft(window_seconds=3600)
        out = []
        for r in rows:
            p = pipeline.payload_for(r)
            p["count"] = r.get("cnt")
            out.append(p)
        return {"aircraft": out}

    @app.get("/api/thread/{tail}")
    async def api_thread(tail: str) -> dict[str, Any]:
        rows = storage.thread_for_tail(tail.upper(), limit=10)
        return {"tail": tail.upper(), "messages": [pipeline.payload_for(r) for r in rows]}

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return (WEB_DIR / "index.html").read_text(encoding="utf-8")

    # ---- WebSocket ----
    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket) -> None:
        await manager.connect(ws)
        try:
            # Prime the new client with current state + recent history.
            await ws.send_json({"type": "status", "data": manager.receiver_state})
            recent = storage.recent(limit=100)
            recent.reverse()
            await ws.send_json(
                {"type": "backfill",
                 "data": [pipeline.payload_for(r) for r in recent]}
            )
            while True:
                # We don't expect client messages; keep the socket alive.
                await ws.receive_text()
        except WebSocketDisconnect:
            pass
        except Exception:  # noqa: BLE001
            log.debug("ws error", exc_info=True)
        finally:
            await manager.disconnect(ws)

    if WEB_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")

    return app
