"""Power axes v2: output-voltage span and quiescent current (fill-only).

The TI parametrics wave shipped gold-required axes only; vout_min/max_v and
iq were held for this detector. Same law as the P3 reread: specified-
operation rows only (ROC/EC/front-page headline), never the Absolute
Maximum Ratings table.

- vout_min_v / vout_max_v: the regulator's printed output span. Range rows
  ("Output voltage range | MIN 0.8 | MAX 5.5" or a "0.8 to 5.5 V" cell)
  fill both; fixed-output rows fill both with the printed value.
- iq: quiescent / no-load / idle supply current, unit-normalized to amps.
  Leakage and operating (load) currents never satisfy the iq axis. When
  competing prints exist, TA = 25 C and the typ role win; ties keep the
  first printed.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence


POWER_AXES_SCHEMA = "harness.electronics-power-axes-v2.v1"

_VOUT_VOCAB = re.compile(
    r"output\s+voltage(?:\s+range)?|vout(?:\s*(?:min|max|range))?|"
    r"VREGOUT|voltage\s+range\s+out|adjustable\s+output|regulated\s+output\s+voltage",
    re.I,
)
_VOUT_REJECT = re.compile(
    r"\bOVP\b|\bUVP\b|threshold|dropout|rising|falling|over\s*voltage|"
    r"under\s*voltage|overvoltage|undervoltage|hysteresis|OCP|current\s+limit",
    re.I,
)
_IQ_VOCAB = re.compile(
    r"\bIQ\b|Iq\b|quiescent|no[- ]?load|idle\s+current|"
    r"supply\s+current.{0,24}(?:quiescent|no[- ]?load)",
    re.I,
)
_IQ_REJECT = re.compile(
    r"leakage|output\s+current|load\s+current|switching|gate\s+charge|"
    r"short[- ]?circuit|current\s+limit|inrush",
    re.I,
)
_RANGE_CELL = re.compile(
    r"(-?\d+(?:\.\d+)?)\s*(?:to|–|-|~)\s*(-?\d+(?:\.\d+)?)\s*(V|volts?)\b",
    re.I,
)
_FRONT_VOUT = re.compile(
    r"(?:adjustable\s+)?output\s+voltage(?:\s+range)?\D{0,24}?"
    r"(-?\d+(?:\.\d+)?)\s*V?\s*(?:to|–|-|~)\s*(-?\d+(?:\.\d+)?)\s*V",
    re.I,
)
_FRONT_VOUT_FIXED = re.compile(
    r"\b(\d+(?:\.\d+)?)\s*-?\s*V\s+(?:fixed\s+)?output|"
    r"output\s+voltage(?:\s+of)?\D{0,12}(\d+(?:\.\d+)?)\s*V",
    re.I,
)
_ABS_MAX_TITLE = re.compile(r"absolute\s+maximum", re.I)
_CURRENT_UNIT = re.compile(r"nA|µA|uA|μA|mA|^A$|\bA\b", re.I)

_UNIT_TO_A = {"a": 1.0, "ma": 1e-3, "µa": 1e-6, "ua": 1e-6, "na": 1e-9}


def _norm_unit(unit: str | None) -> str:
    return re.sub(r"\s+", "", (unit or "")).replace("μ", "µ").lower()


def _to_amps(value: float, unit: str | None) -> float | None:
    factor = _UNIT_TO_A.get(_norm_unit(unit))
    return value * factor if factor is not None else None


def vout_rows(facts: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Fill rows vout_min_v / vout_max_v from printed output spans."""

    rows: list[dict[str, Any]] = []
    for fact in facts:
        if _ABS_MAX_TITLE.search(str(fact.get("table_title") or "")) or (
            fact.get("table_kind") == "absmax"
        ):
            continue
        label = f"{fact.get('symbol') or ''} {fact.get('parameter') or ''}"
        if not _VOUT_VOCAB.search(label):
            continue
        if _VOUT_REJECT.search(label):
            continue
        verbatim = str(fact.get("verbatim") or "")[:220]
        condition = fact.get("condition_verbatim")
        # range printed in one cell ("0.8 to 5.5 V")
        value = fact.get("value")
        if isinstance(value, list) and len(value) == 2:
            rows.append(_vout_row(fact, float(value[0]), float(value[1]), verbatim, condition))
            continue
        cell_match = _RANGE_CELL.search(f"{label} {verbatim}")
        if cell_match:
            rows.append(
                _vout_row(
                    fact,
                    float(cell_match.group(1)),
                    float(cell_match.group(2)),
                    verbatim,
                    condition,
                )
            )
            continue
        low = fact.get("min")
        high = fact.get("max")
        if isinstance(low, (int, float)) and isinstance(high, (int, float)):
            rows.append(_vout_row(fact, float(low), float(high), verbatim, condition))
            continue
        single = value if isinstance(value, (int, float)) else fact.get("typ")
        if isinstance(single, (int, float)):
            rows.append(_vout_row(fact, float(single), float(single), verbatim, condition))
    return rows


def _vout_row(
    fact: Mapping[str, Any], low: float, high: float, verbatim: str, condition: Any
) -> dict[str, Any]:
    return {
        "schema": POWER_AXES_SCHEMA,
        "field": "vout",
        "vout_min_v": low,
        "vout_max_v": high,
        "unit": "V",
        "value_role": "range" if low != high else "fixed",
        "page_1based": fact.get("page"),
        "verbatim": verbatim,
        "condition_verbatim": condition,
        "table_kind": fact.get("table_kind"),
        "method": "deterministic_power_axes_v2_v1",
    }


def iq_rows(facts: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Fill rows for the quiescent-current axis; leakage/load never fills."""

    rows: list[dict[str, Any]] = []
    for fact in facts:
        label = f"{fact.get('symbol') or ''} {fact.get('parameter') or ''}"
        if not _IQ_VOCAB.search(label) or _IQ_REJECT.search(label):
            continue
        if _ABS_MAX_TITLE.search(str(fact.get("table_title") or "")) or (
            fact.get("table_kind") == "absmax"
        ):
            continue
        unit = fact.get("unit")
        if not (unit and _CURRENT_UNIT.search(str(unit))):
            continue
        for role in ("typ", "min", "max", "value"):
            raw = fact.get(role)
            if isinstance(raw, (int, float)):
                amps = _to_amps(float(raw), str(unit))
                if amps is None:
                    continue
                rows.append(
                    {
                        "schema": POWER_AXES_SCHEMA,
                        "field": "iq",
                        "value": amps,
                        "value_role": role,
                        "unit_as_printed": unit,
                        "unit": "A",
                        "page_1based": fact.get("page"),
                        "verbatim": str(fact.get("verbatim") or "")[:220],
                        "condition_verbatim": fact.get("condition_verbatim"),
                        "table_kind": fact.get("table_kind"),
                        "method": "deterministic_power_axes_v2_v1",
                    }
                )
                break
    return rows


def front_vout_rows(front_texts: Sequence[str]) -> list[dict[str, Any]]:
    """Headline output spans from front-page prose ('adjustable output
    voltage from 0.8 V to 15 V'), fill-only."""

    rows: list[dict[str, Any]] = []
    for page_index, text in enumerate(front_texts, 1):
        flat = " ".join((text or "").split())
        for match in _FRONT_VOUT.finditer(flat):
            context = flat[max(0, match.start() - 40) : match.end() + 40]
            if _VOUT_REJECT.search(context):
                continue
            rows.append(
                {
                    "schema": POWER_AXES_SCHEMA,
                    "field": "vout",
                    "vout_min_v": float(match.group(1)),
                    "vout_max_v": float(match.group(2)),
                    "unit": "V",
                    "value_role": "range",
                    "page_1based": page_index,
                    "verbatim": flat[match.start() : match.end()][:220],
                    "condition_verbatim": None,
                    "table_kind": "front_page",
                    "method": "deterministic_power_axes_v2_v1",
                }
            )
        for match in _FRONT_VOUT_FIXED.finditer(flat):
            context = flat[max(0, match.start() - 40) : match.end() + 40]
            if _VOUT_REJECT.search(context):
                continue
            number = match.group(1) or match.group(2)
            rows.append(
                {
                    "schema": POWER_AXES_SCHEMA,
                    "field": "vout",
                    "vout_min_v": float(number),
                    "vout_max_v": float(number),
                    "unit": "V",
                    "value_role": "fixed",
                    "page_1based": page_index,
                    "verbatim": flat[match.start() : match.end()][:220],
                    "condition_verbatim": None,
                    "table_kind": "front_page",
                    "method": "deterministic_power_axes_v2_v1",
                }
            )
    return rows


def collapse_rows(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """One winner per field; TA = 25 C and the typ role win, ties keep the
    first printed. Range rows set both axes together."""

    best: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        key = row["field"]
        weight = (
            2 if "TA = 25" in str(row.get("condition_verbatim") or "") else 0,
            1 if row.get("value_role") in ("typ", "range") else 0,
            1 if row.get("table_kind") != "front_page" else 0,
        )
        current = best.get(key)
        if current is None or weight > current[0]:
            best[key] = (weight, row)
    return {key: pair[1] for key, pair in best.items()}


__all__ = [
    "POWER_AXES_SCHEMA",
    "collapse_rows",
    "front_vout_rows",
    "iq_rows",
    "vout_rows",
]
