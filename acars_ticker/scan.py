"""
`--scan` helper: run rtl_power across the VHF ACARS band, rank candidate
channels by power, and suggest a config block.

ACARS is narrowband (per-channel ~25 kHz spacing on the 8.33/25 kHz plan). We
bin rtl_power output, pick the strongest bins near known ACARS channels, and
print a ranked table plus a ready-to-paste [radio] channels line.

Author: iceo96
"""
from __future__ import annotations

import csv
import io
import subprocess
import sys
import tempfile
from pathlib import Path

# Known worldwide VHF ACARS channels (MHz) to snap detected peaks onto.
KNOWN_CHANNELS = [
    129.125, 130.025, 130.425, 130.450, 131.125, 131.425, 131.450,
    131.475, 131.525, 131.550, 131.725, 131.825, 131.850, 136.700,
    136.750, 136.800, 136.850, 136.900, 136.975,
]


def _nearest_known(freq_mhz: float, tol_khz: float = 12.0) -> float | None:
    best = None
    best_d = tol_khz / 1000.0
    for ch in KNOWN_CHANNELS:
        d = abs(ch - freq_mhz)
        if d <= best_d:
            best_d = d
            best = ch
    return best


def run_scan(cfg, duration_each: str | None = None) -> int:
    """Run rtl_power and print a ranked channel suggestion. Returns exit code."""
    integration = duration_each or cfg.scan_integration
    with tempfile.NamedTemporaryFile("w+", suffix=".csv", delete=False) as tf:
        out_path = Path(tf.name)

    cmd = [
        "rtl_power",
        "-d", str(cfg.device_index),
        "-f", cfg.scan_range,
        "-i", integration,
        "-1",  # single shot
        "-g", "40" if cfg.gain is None else str(cfg.gain),
        str(out_path),
    ]
    if cfg.ppm:
        cmd[1:1] = ["-p", str(cfg.ppm)]

    print(f"Running: {' '.join(cmd)}", file=sys.stderr)
    print("Scanning 130-137 MHz — this takes about "
          f"{integration}s of integration...", file=sys.stderr)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except FileNotFoundError:
        print("ERROR: rtl_power not found. Install rtl-sdr tools.", file=sys.stderr)
        return 2
    except subprocess.TimeoutExpired:
        print("ERROR: rtl_power timed out.", file=sys.stderr)
        return 2

    if proc.returncode != 0:
        print(proc.stderr.strip() or "rtl_power failed", file=sys.stderr)
        return proc.returncode

    peaks = _parse_rtl_power(out_path.read_text())
    out_path.unlink(missing_ok=True)

    if not peaks:
        print("No data parsed from rtl_power output.", file=sys.stderr)
        return 1

    # Collapse to known channels, keep max power seen per channel.
    by_channel: dict[float, float] = {}
    for freq, power in peaks:
        ch = _nearest_known(freq)
        if ch is None:
            continue
        by_channel[ch] = max(by_channel.get(ch, -999.0), power)

    ranked = sorted(by_channel.items(), key=lambda kv: kv[1], reverse=True)

    print("\n  Ranked active ACARS channels (strongest first)")
    print("  " + "-" * 42)
    print(f"  {'Freq (MHz)':>11}   {'Power (dB)':>10}")
    for ch, power in ranked:
        print(f"  {ch:>11.3f}   {power:>10.1f}")

    top = [ch for ch, _ in ranked[:4]]
    if top:
        print("\n  Suggested config — paste into ~/.config/acars-ticker.ini:\n")
        print("  [radio]")
        print("  channels = " + ", ".join(f"{c:g}" for c in sorted(top)))
    else:
        print("\n  No known ACARS channels stood out; try a longer integration "
              "(--scan-time 30) or check your antenna.")
    return 0


def _parse_rtl_power(text: str) -> list[tuple[float, float]]:
    """Parse rtl_power CSV into (freq_mhz, power_db) sample points.

    rtl_power rows: date, time, Hz_low, Hz_high, Hz_step, samples, db, db, ...
    """
    points: list[tuple[float, float]] = []
    reader = csv.reader(io.StringIO(text))
    for row in reader:
        if len(row) < 7:
            continue
        try:
            hz_low = float(row[2])
            hz_step = float(row[4])
            dbs = [float(x) for x in row[6:] if x.strip()]
        except ValueError:
            continue
        for i, db in enumerate(dbs):
            freq_mhz = (hz_low + i * hz_step) / 1e6
            points.append((freq_mhz, db))
    return points
