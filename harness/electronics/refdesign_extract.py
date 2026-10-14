"""Reference-design / eval-board essence extraction (deterministic).

From L0-censused reference_design documents: design identity, the device
under evaluation, printed design requirements (Vin/Vout/Iout/fsw/Pout/
efficiency with ranges), and applications copy — all label-anchored line
reads, verbatim evidence kept, nulls stay null.
"""

from __future__ import annotations

import re
from typing import Any

REFDESIGN_SCHEMA = "harness.electronics-refdesign-facts.v1"

_DESIGN_ID = re.compile(
    r"\b(?:TIDA?-\d{4,6}[A-Z]?|TIDM-\d{4,6}[A-Z]?|TIDMDC-\d{4,6}[A-Z]?|"
    r"PMP\d{4,6}[A-Z]?\d*|[A-Z][A-Z0-9]{2,15}(?:EVKIT|EVB|EK\b|EVM\b|GEVB|AGEVB))")

_EVALUATES = re.compile(
    r"(?:evaluates?|evaluation (?:board|kit) (?:for|features)?|"
    r"features?|uses?|based on)[:\s]+([A-Z][A-Z0-9\-/]{2,24})", re.I)

_NUM = r"(-?\d+(?:\.\d+)?)"
_UNIT_MULT = {
    "v": 1.0, "mv": 1e-3, "kv": 1e3,
    "a": 1.0, "ma": 1e-3, "ua": 1e-6, "µa": 1e-6,
    "w": 1.0, "mw": 1e-3, "kw": 1e3,
    "hz": 1.0, "khz": 1e3, "mhz": 1e6, "ghz": 1e9,
}

# canonical key -> label patterns (searched on normalized lines)
_REQ_LABELS: dict[str, tuple[re.Pattern, str]] = {
    "vin_v": (re.compile(
        rf"\b(?:input|in)\s*(?:voltage|supply|v_?in|vin)\b[^0-9]{{0,30}}{_NUM}\s*(v|mv)\b(?:\s*(?:to|–|-|~)\s*{_NUM}\s*(v|mv)\b)?",
        re.I), "v"),
    "vout_v": (re.compile(
        rf"\b(?:output|out)\s*(?:voltage|v_?out|vout)\b[^0-9]{{0,30}}{_NUM}\s*(v|mv)\b(?:\s*(?:to|–|-|~)\s*{_NUM}\s*(v|mv)\b)?",
        re.I), "v"),
    "iout_a": (re.compile(
        rf"\b(?:output|load)\s*(?:current|i_?out|iout)\b[^0-9]{{0,30}}{_NUM}\s*(a|ma)\b(?:\s*(?:to|–|-|~)\s*{_NUM}\s*(a|ma)\b)?",
        re.I), "a"),
    "fsw_hz": (re.compile(
        rf"\b(?:switching|sw)\s*(?:frequency|f_?sw|fsw)\b[^0-9]{{0,30}}{_NUM}\s*(khz|mhz|hz)\b(?:\s*(?:to|–|-|~)\s*{_NUM}\s*(khz|mhz|hz)\b)?",
        re.I), "hz"),
    "pout_w": (re.compile(
        rf"\b(?:output|rated|total)\s*(?:power|p_?out)\b[^0-9]{{0,30}}{_NUM}\s*(w|kw|mw)\b(?:\s*(?:to|–|-|~)\s*{_NUM}\s*(w|kw|mw)\b)?",
        re.I), "w"),
    "efficiency_pct": (re.compile(
        rf"\befficiency\b[^0-9]{{0,40}}(?:up to\s+|>\s*|≥\s*)?{_NUM}\s*%",
        re.I), "percent"),
    "iio_max_a": (re.compile(
        rf"\b(?:up to|maximum)\b[^0-9]{{0,20}}{_NUM}\s*(a|ma)\b\s*(?:current|output|load)?",
        re.I), "a"),
}


def _scaled(num: str, unit: str | None) -> float:
    mult = _UNIT_MULT.get((unit or "").lower(), 1.0)
    return round(float(num) * mult, 6)


def refdesign_facts_from_text(pages_text: list[str]) -> dict[str, Any]:
    """pages_text: per-page text, 0-indexed. Returns one facts record."""
    first = pages_text[0] if pages_text else ""
    head = "\n".join(pages_text[:2])

    design_id = None
    m = _DESIGN_ID.search(head) or _DESIGN_ID.search(first)
    if m:
        design_id = m.group(0)

    evaluates = None
    m = re.search(r"Evaluates?:\s*([A-Z0-9\-/]{3,24})", head)
    if m:
        evaluates = m.group(1)

    requirements: dict[str, dict] = {}
    for page_index, text in enumerate(pages_text[:40], 1):
        for raw in (text or "").splitlines():
            line = " ".join(raw.split())
            if not line or not re.search(r"\d", line):
                continue
            for key, (pattern, _) in _REQ_LABELS.items():
                if key in requirements:
                    continue
                m = pattern.search(line)
                if not m:
                    continue
                groups = m.groups()
                if key == "efficiency_pct":
                    value = float(groups[0])
                    if not (30.0 <= value <= 99.9):
                        continue
                    requirements[key] = {"value": value, "unit": "%",
                                         "verbatim": line[:200],
                                         "page_1based": page_index}
                    continue
                lo = _scaled(groups[0], groups[1])
                if len(groups) >= 4 and groups[2]:
                    hi = _scaled(groups[2], groups[3])
                    val: Any = [lo, hi]
                else:
                    val = lo
                # plausibility guards
                if key.endswith("_v") and not (0.05 <= max(val if isinstance(val, list) else [val]) <= 1500):
                    continue
                if key.endswith("_a") and not (0.0005 <= max(val if isinstance(val, list) else [val]) <= 2000):
                    continue
                if key == "fsw_hz" and not (1e2 <= (val[1] if isinstance(val, list) else val) <= 1e8):
                    continue
                requirements[key] = {"value": val, "unit": key.split("_")[-1],
                                     "verbatim": line[:200],
                                     "page_1based": page_index}

    return {
        "schema": REFDESIGN_SCHEMA,
        "design_id": design_id,
        "evaluates": evaluates,
        "requirements": requirements,
        "requirements_filled": len(requirements),
    }


__all__ = ["REFDESIGN_SCHEMA", "refdesign_facts_from_text"]
