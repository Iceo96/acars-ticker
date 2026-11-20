"""
Aircraft enrichment via a public ADS-B REST API (adsb.lol / adsb.fi v2).

Lookups are by registration. Results are cached per-reg with a TTL so we hit
the API at most once per 10 minutes per aircraft. Failures degrade gracefully:
the caller still shows the raw registration, never blocking the feed.

No ADS-B receive hardware is used — this is a read-only API client.

Author: iceo96
"""
from __future__ import annotations

import logging
import time
from typing import Any

import httpx

log = logging.getLogger("acars_ticker.enrichment")


class Enricher:
    def __init__(
        self,
        base_url: str,
        cache_seconds: int = 600,
        timeout: float = 4.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.cache_seconds = cache_seconds
        self.timeout = timeout
        # reg -> (expires_at, meta|None)
        self._cache: dict[str, tuple[float, dict[str, Any] | None]] = {}
        self._client = httpx.AsyncClient(
            timeout=timeout,
            headers={"User-Agent": "acars-ticker/1.0 (+iceo96)"},
            follow_redirects=True,
        )

    async def close(self) -> None:
        await self._client.aclose()

    @staticmethod
    def _normalize(ac: dict[str, Any]) -> dict[str, Any]:
        """Map a v2 aircraft object into our compact meta shape."""

        def num(v: Any) -> float | None:
            try:
                return float(v)
            except (TypeError, ValueError):
                return None

        alt = ac.get("alt_baro")
        if isinstance(alt, str):  # "ground"
            alt = None
        return {
            "icao_type": ac.get("t"),
            "registration": ac.get("r"),
            "operator": ac.get("ownOp") or ac.get("desc"),
            "description": ac.get("desc"),
            "hex": ac.get("hex"),
            "lat": num(ac.get("lat")),
            "lon": num(ac.get("lon")),
            "alt": num(alt),
            "track": num(ac.get("track")),
            "gs": num(ac.get("gs")),
            "squawk": ac.get("squawk"),
            "callsign": (ac.get("flight") or "").strip() or None,
            "source": "adsb",
        }

    async def nearby(self, lat: float, lon: float, dist_nm: int = 250) -> list[dict[str, Any]]:
        """All aircraft within dist_nm of a point (FR24-style area query).

        Uses the ADS-B v2 point/radius endpoint. Read-only, no hardware.
        Returns a list of normalized aircraft dicts that have a position.
        """
        dist_nm = max(1, min(int(dist_nm), 250))  # endpoint caps at 250 nm
        url = f"{self.base_url}/v2/lat/{lat:.4f}/lon/{lon:.4f}/dist/{dist_nm}"
        try:
            resp = await self._client.get(url)
            if resp.status_code != 200:
                log.debug("nearby lookup HTTP %s", resp.status_code)
                return []
            data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.debug("nearby lookup failed: %s", exc)
            return []
        out = []
        for ac in (data.get("ac") or data.get("aircraft") or []):
            m = self._normalize(ac)
            if m["lat"] is not None and m["lon"] is not None:
                out.append(m)
        return out

    async def lookup(self, reg: str) -> dict[str, Any] | None:
        """Look up an aircraft by registration. Returns meta dict or None."""
        if not reg:
            return None
        reg = reg.strip().upper()
        now = time.time()

        cached = self._cache.get(reg)
        if cached and cached[0] > now:
            return cached[1]

        meta: dict[str, Any] | None = None
        url = f"{self.base_url}/v2/registration/{reg}"
        try:
            resp = await self._client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                aclist = data.get("ac") or []
                if aclist:
                    meta = self._normalize(aclist[0])
            elif resp.status_code == 429:
                log.warning("ADS-B rate limited (429); backing off for reg %s", reg)
                # short cache so we retry soon but not immediately
                self._cache[reg] = (now + 30, None)
                return None
            else:
                log.debug("ADS-B lookup %s -> HTTP %s", reg, resp.status_code)
        except (httpx.HTTPError, ValueError) as exc:
            log.debug("ADS-B lookup failed for %s: %s", reg, exc)

        self._cache[reg] = (now + self.cache_seconds, meta)
        return meta
