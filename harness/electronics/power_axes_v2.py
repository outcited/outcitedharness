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


# ---------------------------------------------------------------------------
# Bounded decision-critical axes (E3 selection journey) — vin / iout / fsw /
# vds / rds. Same law: specified-operation rows only; the Absolute Maximum
# Ratings table NEVER fills an axis; protection thresholds, dropout,
# leakage, load/operating currents and gate charge are rejected.
# ---------------------------------------------------------------------------

_VIN_VOCAB = re.compile(
    r"input\s+voltage(?:\s+range)?|\bVIN\b|supply\s+voltage(?:\s+range)?|"
    r"operating\s+(?:input\s+)?voltage\s+range|voltage\s+range\s+in",
    re.I,
)
_VIN_REJECT = re.compile(
    r"\bUVLO\b|undervoltage\s+lock|\bOVP\b|\bUVP\b|threshold|rising|falling|"
    r"hysteresis|dropout|overvoltage|undervoltage|absolute|"
    r"logic|enable|shutdown|\bEN\b|leakage|start-?up\s+current",
    re.I,
)
_IOUT_VOCAB = re.compile(
    r"output\s+current|load\s+current|continuous\s+output|"
    r"\bIOUT\b|\bILOAD\b|max(?:imum)?\s+output\s+current|current\s+rating",
    re.I,
)
_IOUT_REJECT = re.compile(
    r"quiescent|\bIQ\b|leakage|shutdown|short[- ]?circuit|current\s+limit|"
    r"inrush|reverse|sink|source|limit|threshold|hiccup|fold",
    re.I,
)
_FSW_VOCAB = re.compile(
    r"switching\s+frequency|\bFSW\b|oscillator\s+frequency|\bFOSC\b|"
    r"frequency\s+range", re.I
)
_FSW_REJECT = re.compile(
    r"sync|external|clock|jitter|synchroniz|spread|crossover|bandwidth", re.I
)
_VDS_VOCAB = re.compile(
    r"drain[- ](?:to[- ])?source\s+(?:breakdown\s+)?voltage|\bVDSS?\b|"
    r"drain[- ]source\s+voltage", re.I
)
_VDS_REJECT = re.compile(
    r"on[- ]?(?:state\s+)?resistance|rds|threshold|gate|current|clamp|"
    r"leakage|absolute", re.I
)
_RDS_VOCAB = re.compile(
    r"drain[- ](?:to[- ])?source\s+on[- ]?(?:state\s+)?resistance|"
    r"on[- ]?resistance|\bRDS\(?ON\)?\b", re.I
)
_RDS_REJECT = re.compile(
    r"breakdown|threshold|gate|voltage|charge|capacitance|absolute|gate\s+", re.I
)


def _to_mohm(value: float, unit: str | None) -> float | None:
    u = _norm_unit(unit)
    if u in ("mohm", "mω", "mohm", "mΩ".lower()):
        return float(value)
    if u in ("ohm", "ω"):
        return float(value) * 1000.0
    return None


def _to_khz(value: float, unit: str | None) -> float | None:
    u = _norm_unit(unit)
    if u in ("khz",):
        return float(value)
    if u in ("mhz",):
        return float(value) * 1000.0
    if u in ("hz",):
        return float(value) / 1000.0
    return None


_AXIS_SPECS: tuple[tuple[str, re.Pattern, re.Pattern, str], ...] = (
    ("vin", _VIN_VOCAB, _VIN_REJECT, "V"),
    ("iout", _IOUT_VOCAB, _IOUT_REJECT, "A"),
    ("fsw", _FSW_VOCAB, _FSW_REJECT, "kHz"),
    ("vds", _VDS_VOCAB, _VDS_REJECT, "V"),
    ("rds", _RDS_VOCAB, _RDS_REJECT, "mOhm"),
)


def _axis_row(field: str, value: float, role: str, unit: str,
              fact: Mapping[str, Any], verbatim: str, unit_as_printed: Any) -> dict:
    return {
        "schema": POWER_AXES_SCHEMA,
        "field": field,
        "value": value,
        "value_role": role,
        "unit": unit,
        "unit_as_printed": unit_as_printed,
        "page_1based": fact.get("page"),
        "verbatim": verbatim,
        "condition_verbatim": fact.get("condition_verbatim"),
        "table_kind": fact.get("table_kind"),
        "method": "deterministic_power_axes_v2_v1",
    }


def _range_low_high(fact: Mapping[str, Any]) -> tuple[float, float] | None:
    value = fact.get("value")
    if isinstance(value, list) and len(value) == 2 and all(
        isinstance(v, (int, float)) for v in value
    ):
        return float(value[0]), float(value[1])
    low, high = fact.get("min"), fact.get("max")
    if isinstance(low, (int, float)) and isinstance(high, (int, float)):
        return float(low), float(high)
    return None


def bounded_axis_rows(facts: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Fill the bounded decision-critical axes: vin_min_v/vin_max_v, iout_max_a,
    fsw_max_khz, vds_v, rds_on_mohm. Specified-operation rows only; absmax never
    fills; protection/threshold/leakage/load look-alikes are rejected. A value
    whose printed unit is incompatible with the axis is dropped (fail-closed).
    Field names match the vout_min_v/vout_max_v convention so collapse_rows and
    the wave contract treat each axis independently.
    """

    rows: list[dict[str, Any]] = []
    for fact in facts:
        if _ABS_MAX_TITLE.search(str(fact.get("table_title") or "")) or (
            fact.get("table_kind") == "absmax"
        ):
            continue
        label = f"{fact.get('symbol') or ''} {fact.get('parameter') or ''}"
        verbatim = str(fact.get("verbatim") or "")[:220]
        unit = fact.get("unit")
        for field, vocab, reject, out_unit in _AXIS_SPECS:
            if not vocab.search(label) or reject.search(label):
                continue
            # range axes (vin, vds) may print min/max or a range cell
            rng = _range_low_high(fact) if field in ("vin", "vds") else None
            if rng is not None:
                if _norm_unit(unit) != "v":
                    break
                if field == "vin":
                    rows.append(_axis_row("vin_min_v", rng[0], "range", "V",
                                          fact, verbatim, unit))
                    rows.append(_axis_row("vin_max_v", rng[1], "range", "V",
                                          fact, verbatim, unit))
                else:  # vds: the rating is the max of the printed range
                    rows.append(_axis_row("vds_v", max(rng), "rating", "V",
                                          fact, verbatim, unit))
                break
            single = fact.get("value") if isinstance(fact.get("value"), (int, float)) \
                else fact.get("typ")
            if not isinstance(single, (int, float)):
                single = fact.get("max") if isinstance(fact.get("max"), (int, float)) \
                    else fact.get("min")
            if not isinstance(single, (int, float)):
                continue
            single = float(single)
            if field == "iout":
                amps = _to_amps(single, unit)
                if amps is None:
                    continue
                rows.append(_axis_row("iout_max_a", amps, "max", "A",
                                      fact, verbatim, unit))
            elif field == "fsw":
                khz = _to_khz(single, unit)
                if khz is None:
                    continue
                rows.append(_axis_row("fsw_max_khz", khz, "max", "kHz",
                                      fact, verbatim, unit))
            elif field == "rds":
                m = _to_mohm(single, unit)
                if m is None:
                    continue
                rows.append(_axis_row("rds_on_mohm", m, "max", "mOhm",
                                      fact, verbatim, unit))
            elif field == "vds":
                if _norm_unit(unit) != "v":
                    continue
                rows.append(_axis_row("vds_v", single, "rating", "V",
                                      fact, verbatim, unit))
            break
    return rows


__all__ = [
    "POWER_AXES_SCHEMA",
    "bounded_axis_rows",
    "collapse_rows",
    "front_vout_rows",
    "iq_rows",
    "vout_rows",
]
