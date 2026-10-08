"""Thermal metrics for power devices (RθJA, RθJC, ΨJT/ΨJB, Zth) — fill-only.

Printed evidence only: the metric symbol (or its spelled form) with a
value and a °C/W or K/W unit from the same printed row. Condition text
(JEDEC reference, layer count, still air) stays verbatim; the package
scope is kept when the row prints it. Rows without a thermal unit never
emit — a dimensionless 'RθJA' mention is not a value.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence


THERMAL_SCHEMA = "harness.electronics-thermal-metrics.v1"

_THERMAL_SYMBOL = re.compile(
    r"R\s*θ\s*JA|R\s*θ\s*JC|R\s*θ\s*JB|Rth\s*JA|Rth\s*JC|Rth\s*JB|"
    r"Θ\s*JA|Θ\s*JC|θ\s*JA|θ\s*JC|theta\s*[- ]?\s*JA|theta\s*[- ]?\s*JC|"
    r"Ψ\s*JT|Ψ\s*JB|psi\s*[- ]?\s*JT|psi\s*[- ]?\s*JB|"
    r"Z\s*θ\s*JA|Zth\s*JA|Z\s*th\s*JA",
    re.I,
)
_THERMAL_WORDS = re.compile(
    r"thermal\s+resistance.{0,32}(?:ambient|case|board|top)|"
    r"junction[- ]to[- ](?:ambient|case|board|top)|"
    r"characterization\s+parameter.{0,24}(?:top|board)|"
    r"transient\s+thermal\s+impedance",
    re.I,
)
_THERMAL_UNIT = re.compile(r"(?:°\s*C|C|K)\s*/\s*(?:W|watt)", re.I)
_VALUE_UNIT = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:°\s*C|C|K)\s*/\s*(?:W|watt)", re.I
)
_KIND = {
    "ja": "RthetaJA",
    "jc": "RthetaJC",
    "jb": "RthetaJB",
    "jt": "PsiJT",
    "jb2": "PsiJB",
    "zthja": "ZthJA",
}


def _kind(text: str) -> str | None:
    flat = re.sub(r"\s+", "", text.lower())
    flat = flat.replace("θ", "theta").replace("Θ", "theta").replace("ψ", "psi")
    if re.search(r"zth|ztheta", flat):
        return "ZthJA"
    if "psijb" in flat:
        return "PsiJB"
    if "psijt" in flat:
        return "PsiJT"
    if re.search(r"rtheta?ja|rthja", flat):
        return "RthetaJA"
    if re.search(r"rtheta?jc|rthjc", flat):
        return "RthetaJC"
    if re.search(r"rtheta?jb|rthjb", flat):
        return "RthetaJB"
    words = re.sub(r"[\s_]+", "-", text.lower())
    if "junction-to-ambient" in words or re.search(r"thermal-resistance.{0,32}ambient", words):
        return "RthetaJA"
    if "junction-to-case" in words or re.search(r"thermal-resistance.{0,32}case", words):
        return "RthetaJC"
    if "junction-to-board" in words:
        return "PsiJB"
    if "junction-to-top" in words:
        return "PsiJT"
    return None


def thermal_rows(facts: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """One row per printed thermal value; mentions without values stay out."""

    rows: list[dict[str, Any]] = []
    for fact in facts:
        label = f"{fact.get('symbol') or ''} {fact.get('parameter') or ''}"
        if not (_THERMAL_SYMBOL.search(label) or _THERMAL_WORDS.search(label)):
            continue
        unit = str(fact.get("unit") or "")
        verbatim = str(fact.get("verbatim") or "")[:220]
        if not _THERMAL_UNIT.search(unit) and not _THERMAL_UNIT.search(verbatim):
            continue
        kind = _kind(label) or _kind(verbatim)
        if kind is None:
            continue
        for role in ("typ", "value", "min", "max"):
            raw = fact.get(role)
            if isinstance(raw, (int, float)):
                rows.append(
                    {
                        "schema": THERMAL_SCHEMA,
                        "metric": kind,
                        "value": float(raw),
                        "unit": "C/W",
                        "value_role": role,
                        "condition_verbatim": fact.get("condition_verbatim"),
                        "table_title": fact.get("table_title"),
                        "page_1based": fact.get("page"),
                        "verbatim": verbatim,
                        "method": "deterministic_thermal_metrics_v1",
                    }
                )
                break
        else:
            match = _VALUE_UNIT.search(verbatim)
            if match:
                rows.append(
                    {
                        "schema": THERMAL_SCHEMA,
                        "metric": kind,
                        "value": float(match.group(1)),
                        "unit": "C/W",
                        "value_role": "printed",
                        "condition_verbatim": fact.get("condition_verbatim"),
                        "table_title": fact.get("table_title"),
                        "page_1based": fact.get("page"),
                        "verbatim": verbatim,
                        "method": "deterministic_thermal_metrics_v1",
                    }
                )
    return rows


def collapse_thermal(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """One winner per metric; JEDEC-referenced and typ rows win, ties keep
    the first printed."""

    best: dict[str, tuple[tuple[int, int], Mapping[str, Any]]] = {}
    for row in rows:
        weight = (
            2 if re.search(r"JEDEC|51-\d|JESD", str(row.get("condition_verbatim") or ""), re.I) else 0,
            1 if row.get("value_role") in ("typ", "printed") else 0,
        )
        current = best.get(row["metric"])
        if current is None or weight > current[0]:
            best[row["metric"]] = (weight, row)
    return {metric: pair[1] for metric, pair in best.items()}


__all__ = [
    "THERMAL_SCHEMA",
    "collapse_thermal",
    "thermal_rows",
]
