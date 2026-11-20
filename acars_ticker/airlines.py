"""
Airline derivation helpers.

The ACARS `flight` field is usually an ICAO callsign (e.g. BAW123) whose first
three letters are the ICAO airline designator. We map a subset to human names
so the UI can show "British Airways" instead of "BAW" and colour-code rows.
Unknown codes degrade to the raw 3-letter code.

Author: iceo96
"""
from __future__ import annotations

import re

_FLIGHT_RE = re.compile(r"^([A-Z]{3})(\d.*)?$")

# Small built-in table of common ICAO airline designators. Not exhaustive;
# enrichment can supply the operator name from ADS-B when available.
ICAO_AIRLINES = {
    "BAW": "British Airways",
    "SHT": "BA Shuttle",
    "EZY": "easyJet",
    "EJU": "easyJet Europe",
    "RYR": "Ryanair",
    "RUK": "Ryanair UK",
    "DLH": "Lufthansa",
    "CLH": "Lufthansa CityLine",
    "AFR": "Air France",
    "KLM": "KLM",
    "VLG": "Vueling",
    "IBE": "Iberia",
    "IBS": "Iberia Express",
    "TAP": "TAP Air Portugal",
    "SWR": "Swiss",
    "AUA": "Austrian",
    "SAS": "SAS",
    "FIN": "Finnair",
    "TUI": "TUI Airways",
    "WZZ": "Wizz Air",
    "WUK": "Wizz Air UK",
    "NAX": "Norwegian",
    "NOZ": "Norwegian Air",
    "UAE": "Emirates",
    "QTR": "Qatar Airways",
    "ETD": "Etihad",
    "THY": "Turkish Airlines",
    "AAL": "American Airlines",
    "UAL": "United Airlines",
    "DAL": "Delta Air Lines",
    "SWA": "Southwest",
    "JBU": "JetBlue",
    "ACA": "Air Canada",
    "FDX": "FedEx",
    "UPS": "UPS Airlines",
    "GEC": "Lufthansa Cargo",
    "CLX": "Cargolux",
    "ICE": "Icelandair",
    "AEE": "Aegean",
    "EIN": "Aer Lingus",
    "BEL": "Brussels Airlines",
    "LOT": "LOT Polish",
    "CSA": "Czech Airlines",
    "ROT": "TAROM",
    "MSR": "EgyptAir",
    "ELY": "El Al",
    "SIA": "Singapore Airlines",
    "QFA": "Qantas",
    "ANA": "All Nippon",
    "JAL": "Japan Airlines",
    "CPA": "Cathay Pacific",
}

# Deterministic palette assignment so each airline keeps a stable colour.
_PALETTE = [
    "#4fc3f7", "#81c784", "#ffb74d", "#e57373", "#ba68c8",
    "#4dd0e1", "#aed581", "#ff8a65", "#f06292", "#9575cd",
    "#64b5f6", "#dce775", "#ffd54f", "#a1887f", "#90a4ae",
]


def airline_code(flight: str | None) -> str | None:
    """Extract the 3-letter ICAO operator code from a flight/callsign."""
    if not flight:
        return None
    m = _FLIGHT_RE.match(flight.strip().upper())
    if m:
        return m.group(1)
    return None


def airline_name(code: str | None) -> str | None:
    if not code:
        return None
    return ICAO_AIRLINES.get(code, code)


def airline_color(code: str | None) -> str:
    """Stable colour for an airline code (hashed into the palette)."""
    if not code:
        return "#90a4ae"
    idx = sum(ord(c) for c in code) % len(_PALETTE)
    return _PALETTE[idx]
