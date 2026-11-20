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
        base_urls: list[str] | str,
        cache_seconds: int = 600,
        timeout: float = 4.0,
    ):
        if isinstance(base_urls, str):
            base_urls = [base_urls]
        # Try each in order; a 429 / error on one falls through to the next.
        self.base_urls = [u.rstrip("/") for u in base_urls if u]
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
        path = f"/v2/lat/{lat:.4f}/lon/{lon:.4f}/dist/{dist_nm}"
        last: list[dict[str, Any]] = []
        for base in self.base_urls:
            try:
                resp = await self._client.get(base + path)
                if resp.status_code != 200:  # e.g. 429 rate limit -> try next
                    log.debug("nearby %s -> HTTP %s", base, resp.status_code)
                    continue
                data = resp.json()
            except (httpx.HTTPError, ValueError) as exc:
                log.debug("nearby %s failed: %s", base, exc)
                continue
            out = []
            for ac in (data.get("ac") or data.get("aircraft") or []):
                m = self._normalize(ac)
                if m["lat"] is not None and m["lon"] is not None:
                    out.append(m)
            if out:
                return out
            last = out  # empty answer; remember but try the other provider
        return last

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
        for base in self.base_urls:
            try:
                resp = await self._client.get(f"{base}/v2/registration/{reg}")
                if resp.status_code == 200:
                    aclist = resp.json().get("ac") or []
                    if aclist:
                        meta = self._normalize(aclist[0])
                        break
                    # 200 but empty — not live here; try the other provider
                elif resp.status_code == 429:
                    log.debug("ADS-B 429 from %s for %s", base, reg)
                    continue
                else:
                    log.debug("ADS-B %s -> HTTP %s for %s", base, resp.status_code, reg)
            except (httpx.HTTPError, ValueError) as exc:
                log.debug("ADS-B lookup %s failed for %s: %s", base, reg, exc)

        # Found: cache for the full TTL. Not found: short TTL so we retry soon.
        self._cache[reg] = (now + (self.cache_seconds if meta else 60), meta)
        return meta
