"""
Message classification into buckets using the 2-char ACARS label plus
regex heuristics on the message text.

Buckets: weather | ops | mechanical | ATC | misc

Author: iceo96
"""
from __future__ import annotations

import re

# ACARS labels are a well-known registry. These are coarse hints; the text
# regexes below refine or override them.
LABEL_HINTS = {
    # Weather / ATIS / METAR request & reply
    "H1": None,  # message block, needs text inspection
    "B9": "ATC",   # request oceanic clearance
    "BA": "ATC",   # CPDLC / clearance
    "A6": "ATC",
    "A9": "ATC",
    "A0": "ATC",
    "15": "ops",   # ETA
    "16": "ops",
    "10": "ops",   # flight plan / dispatch
    "12": "ops",
    "80": "mechanical",
    "81": "mechanical",
    "83": "mechanical",
    "5Z": "ops",   # airline-defined, usually company ops
    "4N": "ops",
    "Q0": "ops",
    "QA": "ops",
    "QB": "ops",
    "QC": "ops",
    "QD": "ops",
    "RA": "weather",
    "RB": "weather",
    "SA": "ops",   # media advisory / link mgmt (keep as ops-ish)
    "_d": "misc",  # link test / squitter
    "C1": "ops",
}

_WEATHER_RE = re.compile(
    r"\b(METAR|SPECI|TAF|ATIS|WX|WEATHER|D-ATIS|RVR|QNH|VRB\d|"
    r"\d{3}\d{2}KT|SCT\d{3}|BKN\d{3}|OVC\d{3}|FEW\d{3}|CAVOK)\b",
    re.IGNORECASE,
)
_OPS_RE = re.compile(
    r"\b(GATE|STAND|PUSHBACK|PUSH BACK|BLOCK|ETA|ETD|OOOI|OUT|OFF|ON|IN|"
    r"BOARDING|PAX|FUEL|CONNECT|CXN|CONNECTION|DELAY|SLOT|CTOT|CREW|"
    r"TAXI|DEICE|DE-ICE|LOADSHEET|ZFW|LOAD)\b",
    re.IGNORECASE,
)
_MECH_RE = re.compile(
    r"\b(FAULT|FAIL|FAILURE|MAINT|MAINTENANCE|ECAM|EICAS|ACMS|"
    r"ENGINE|OIL|HYD|HYDRAULIC|APU|SNAG|DEFECT|INOP|EXCEEDANCE|"
    r"PERF REPORT|SMOKE|OVERHEAT|WARNING MSG)\b",
    re.IGNORECASE,
)
_ATC_RE = re.compile(
    r"\b(CPDLC|CLEARED|CLIMB|DESCEND|MAINTAIN|POSITION REPORT|POS RPT|POS N|"
    r"OCEANIC|SELCAL|WILCO|UNABLE|DIRECT|FL\d{2,3}|"
    r"SQUAWK|VECTOR|LOGON|LOGOFF)\b",
    re.IGNORECASE,
)

BUCKETS = ("weather", "ops", "mechanical", "ATC", "misc")


def classify(label: str | None, text: str | None) -> str:
    """Return one of BUCKETS for a message given its label and text body."""
    text = text or ""

    # Text regexes win because they are more specific than label hints.
    if _WEATHER_RE.search(text):
        return "weather"
    if _MECH_RE.search(text):
        return "mechanical"
    if _ATC_RE.search(text):
        return "ATC"
    if _OPS_RE.search(text):
        return "ops"

    if label:
        hint = LABEL_HINTS.get(label.strip().upper()) or LABEL_HINTS.get(label.strip())
        if hint:
            return hint

    return "misc"
