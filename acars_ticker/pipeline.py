"""
The processing pipeline shared by live collection and replay.

For each normalized message: derive airline + bucket, persist, broadcast to
connected clients immediately, then enrich from ADS-B in the background and
patch the stored row + notify clients when enrichment arrives. Enrichment
never blocks the feed.

Author: iceo96
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable

from . import airlines
from .classify import classify
from .enrichment import Enricher
from .storage import Storage

log = logging.getLogger("acars_ticker.pipeline")

Broadcaster = Callable[[dict[str, Any]], Awaitable[None]]


class Pipeline:
    def __init__(
        self,
        storage: Storage,
        enricher: Enricher | None,
        broadcast: Broadcaster,
    ):
        self.storage = storage
        self.enricher = enricher
        self.broadcast = broadcast
        self._enrich_tasks: set[asyncio.Task] = set()

    async def handle(self, msg: dict[str, Any]) -> dict[str, Any]:
        if not msg.get("ts"):
            msg["ts"] = time.time()

        code = airlines.airline_code(msg.get("flight"))
        msg["airline"] = code
        msg["bucket"] = classify(msg.get("label"), msg.get("text"))

        stored = self.storage.insert(msg)

        # Build the wire payload for the UI (adds display fields).
        payload = self._to_payload(stored)
        await self.broadcast({"type": "message", "data": payload})

        # Fire-and-forget enrichment keyed by registration.
        if self.enricher and stored.get("tail"):
            task = asyncio.create_task(self._enrich(stored))
            self._enrich_tasks.add(task)
            task.add_done_callback(self._enrich_tasks.discard)

        return stored

    async def _enrich(self, stored: dict[str, Any]) -> None:
        tail = stored["tail"]
        try:
            meta = await self.enricher.lookup(tail)
        except Exception:  # noqa: BLE001
            log.debug("enrichment error for %s", tail, exc_info=True)
            return
        if not meta:
            return
        self.storage.update_meta(tail, meta, since_id=max(stored["id"] - 50, 0))
        await self.broadcast(
            {"type": "enrich", "data": {"id": stored["id"], "tail": tail, "meta": meta}}
        )

    @staticmethod
    def _to_payload(row: dict[str, Any]) -> dict[str, Any]:
        code = row.get("airline")
        meta = row.get("meta") or {}
        return {
            "id": row.get("id"),
            "ts": row.get("ts"),
            "freq": row.get("freq"),
            "channel": row.get("channel"),
            "level": row.get("level"),
            "error": row.get("error"),
            "label": row.get("label"),
            "tail": row.get("tail"),
            "flight": row.get("flight"),
            "text": row.get("text"),
            "bucket": row.get("bucket"),
            "airline_code": code,
            "airline_name": meta.get("operator") or airlines.airline_name(code),
            "airline_color": airlines.airline_color(code),
            "icao_type": meta.get("icao_type"),
            "meta": meta or None,
        }

    def payload_for(self, row: dict[str, Any]) -> dict[str, Any]:
        return self._to_payload(row)
