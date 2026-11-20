"""
Configuration loading for acars-ticker.

Reads ~/.config/acars-ticker.ini (override with ACARS_TICKER_CONFIG), falling
back to sane European defaults so the app runs even before you write a config.

Author: iceo96
"""
from __future__ import annotations

import configparser
import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CONFIG_PATH = Path(
    os.environ.get("ACARS_TICKER_CONFIG", "~/.config/acars-ticker.ini")
).expanduser()

# Europe default channel plan. Swap to US (130.025, 131.125, 131.550) in the ini.
DEFAULT_CHANNELS = [131.525, 131.725, 131.825, 136.900]


@dataclass
class Config:
    # --- radio / acarsdec ---
    device_index: int = 0
    ppm: int = 0
    gain: float | None = None  # None => acarsdec autogain
    channels: list[float] = field(default_factory=lambda: list(DEFAULT_CHANNELS))
    udp_port: int = 5555
    acarsdec_bin: str = "acarsdec"

    # --- storage ---
    db_path: Path = field(
        default_factory=lambda: Path("~/.local/share/acars-ticker/acars.db").expanduser()
    )
    retention_days: int = 30

    # --- enrichment ---
    adsb_provider: str = "adsb.fi"  # or "adsb.lol" (we fall back to the other)
    adsb_base_url: str = ""  # derived from provider if blank
    enrich_cache_seconds: int = 600  # 10 minutes
    enrich_timeout: float = 4.0

    # --- web ---
    host: str = "127.0.0.1"
    port: int = 8080

    # --- location (map home) ---
    # Default: resolved from the operator's shared pin (Amman, Jordan).
    home_lat: float = 31.962958
    home_lon: float = 35.812154
    home_zoom: int = 7
    # Let the browser try to geolocate and recenter the map on the real user.
    geolocate: bool = True
    # Show ALL nearby aircraft on the map (FR24-style), from the ADS-B area API.
    show_traffic: bool = True
    traffic_radius_nm: int = 250  # capped at 250 by the API

    # --- scan ---
    scan_range: str = "130M:137M:25k"
    scan_integration: str = "15"

    # Base URL per provider. Note adsb.fi's v2 API lives under /api.
    _PROVIDER_URLS = {
        "adsb.fi": "https://opendata.adsb.fi/api",
        "adsb.lol": "https://api.adsb.lol",
    }

    @property
    def resolved_adsb_base_url(self) -> str:
        if self.adsb_base_url:
            return self.adsb_base_url.rstrip("/")
        return self._PROVIDER_URLS.get(self.adsb_provider, "https://opendata.adsb.fi/api")

    def adsb_base_urls(self) -> list[str]:
        """Primary provider first, then the other as an automatic fallback,
        so one provider rate-limiting (HTTP 429) doesn't empty the map."""
        primary = self.resolved_adsb_base_url
        urls = [primary]
        for u in self._PROVIDER_URLS.values():
            if u != primary:
                urls.append(u)
        return urls


def _parse_channels(raw: str) -> list[float]:
    chans: list[float] = []
    for token in raw.replace(",", " ").split():
        token = token.strip()
        if not token:
            continue
        chans.append(round(float(token), 3))
    if not chans:
        raise ValueError("no channels parsed")
    if len(chans) > 4:
        raise ValueError(
            f"acarsdec supports at most 4 channels from one dongle, got {len(chans)}"
        )
    return chans


def load_config(path: Path | None = None) -> Config:
    """Load config from ini, applying defaults for anything missing."""
    cfg = Config()
    path = Path(path).expanduser() if path else DEFAULT_CONFIG_PATH
    if not path.exists():
        return cfg

    parser = configparser.ConfigParser()
    parser.read(path)

    if parser.has_section("radio"):
        r = parser["radio"]
        cfg.device_index = r.getint("device_index", cfg.device_index)
        cfg.ppm = r.getint("ppm", cfg.ppm)
        if r.get("gain", "").strip().lower() in ("", "auto"):
            cfg.gain = None
        else:
            cfg.gain = r.getfloat("gain")
        cfg.udp_port = r.getint("udp_port", cfg.udp_port)
        cfg.acarsdec_bin = r.get("acarsdec_bin", cfg.acarsdec_bin)
        if r.get("channels", "").strip():
            cfg.channels = _parse_channels(r["channels"])

    if parser.has_section("storage"):
        s = parser["storage"]
        cfg.db_path = Path(s.get("db_path", str(cfg.db_path))).expanduser()
        cfg.retention_days = s.getint("retention_days", cfg.retention_days)

    if parser.has_section("enrichment"):
        e = parser["enrichment"]
        cfg.adsb_provider = e.get("provider", cfg.adsb_provider)
        cfg.adsb_base_url = e.get("base_url", cfg.adsb_base_url)
        cfg.enrich_cache_seconds = e.getint("cache_seconds", cfg.enrich_cache_seconds)
        cfg.enrich_timeout = e.getfloat("timeout", cfg.enrich_timeout)

    if parser.has_section("web"):
        w = parser["web"]
        cfg.host = w.get("host", cfg.host)
        cfg.port = w.getint("port", cfg.port)

    if parser.has_section("location"):
        loc = parser["location"]
        cfg.home_lat = loc.getfloat("lat", cfg.home_lat)
        cfg.home_lon = loc.getfloat("lon", cfg.home_lon)
        cfg.home_zoom = loc.getint("zoom", cfg.home_zoom)
        cfg.geolocate = loc.getboolean("geolocate", cfg.geolocate)
        cfg.show_traffic = loc.getboolean("show_traffic", cfg.show_traffic)
        cfg.traffic_radius_nm = loc.getint("traffic_radius_nm", cfg.traffic_radius_nm)

    if parser.has_section("scan"):
        sc = parser["scan"]
        cfg.scan_range = sc.get("range", cfg.scan_range)
        cfg.scan_integration = sc.get("integration", cfg.scan_integration)

    return cfg
