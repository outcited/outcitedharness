"""Connector series parametrics (Concentric demo P1).

Five axes per series from the held datasheet pages — positions, pitch,
rated current, rated voltage, operating temperature — printed evidence
only. Label-anchored line reading over the recorded pages; dielectric
withstanding voltage stays a separate axis (a test stress, never the
operating rating); blank or unlabeled axes stay null, never guessed.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence


CONNECTOR_SCHEMA = "harness.electronics-connector-parametrics.v1"

_NUM = r"(-?\d+(?:\.\d+)?)"

_LABELS: dict[str, tuple[re.Pattern[str], ...]] = {
    "positions": (
        re.compile(rf"number\s+of\s+positions?\D{{0,20}}({_NUM})", re.I),
        re.compile(rf"positions?\s*[:=]\s*({_NUM})", re.I),
        re.compile(rf"\b({_NUM})\s*(?:-|\s)\s*pos(?:itions?)?\b", re.I),
    ),
    "pitch_mm": (
        re.compile(rf"pitch\D{{0,12}}({_NUM})\s*mm", re.I),
        re.compile(rf"({_NUM})\s*mm\s*pitch", re.I),
    ),
    "current_rating_a": (
        re.compile(rf"rated?\s+current\D{{0,16}}({_NUM})\s*(?:a|a/dc|ac/dc)\b", re.I),
        re.compile(rf"current\s+rating\D{{0,16}}({_NUM})\s*(?:a|a/dc|ac/dc)\b", re.I),
    ),
    "voltage_rating_v": (
        re.compile(rf"rated?\s+voltage\D{{0,16}}({_NUM})\s*v", re.I),
        re.compile(rf"voltage\s+rating\D{{0,16}}({_NUM})\s*v", re.I),
    ),
    "dielectric_withstanding_v": (
        re.compile(rf"(?:withstanding|withstand|dielectric)\D{{0,30}}({_NUM})\s*v", re.I),
    ),
    "temp_range_c": (
        re.compile(
            r"operating\s+temperature\D{0,40}?(-?\d+(?:\.\d+)?)\s*°?\s*c?\s*"
            r"(?:to|–|-|~)\s*([+-]?\d+(?:\.\d+)?)\s*°?\s*c",
            re.I,
        ),
    ),
}

_UNIT_CONTEXT = re.compile(r"\b(?:v|volt)\b", re.I)


def connector_parametric_rows(
    page_texts: Sequence[str],
) -> list[dict[str, Any]]:
    """One fill row per printed axis hit; verbatim line kept for evidence."""

    rows: list[dict[str, Any]] = []
    for page_index, text in enumerate(page_texts, 1):
        lines = [" ".join(raw.split()) for raw in (text or "").splitlines()]
        for line in lines:
            if not line or not re.search(r"\d", line):
                continue
            for axis, patterns in _LABELS.items():
                for pattern in patterns:
                    match = pattern.search(line)
                    if not match:
                        continue
                    groups = match.groups()
                    if axis == "temp_range_c":
                        value = [float(groups[0]), float(groups[1])]
                    else:
                        value = float(groups[0])
                        if axis == "current_rating_a" and value > 100:
                            break
                        if axis in ("voltage_rating_v", "dielectric_withstanding_v") and not _UNIT_CONTEXT.search(line):
                            break
                    rows.append(
                        {
                            "schema": CONNECTOR_SCHEMA,
                            "axis": axis,
                            "value": value,
                            "page_1based": page_index,
                            "verbatim": line[:220],
                            "method": "deterministic_connector_parametrics_v1",
                        }
                    )
                    break
    return rows


def collapse_series(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Fill-only best per axis — most-common printed value wins; ties keep
    the first-printed; contradictory prints stay out (flagged)."""

    by_axis: dict[str, list[float | list]] = {}
    verbatim: dict[str, str] = {}
    for row in rows:
        by_axis.setdefault(row["axis"], []).append(row["value"])
        verbatim.setdefault(row["axis"], row["verbatim"])
    out: dict[str, Any] = {}
    for axis, values in by_axis.items():
        counts: dict[float | list, int] = {}
        for value in values:
            key = tuple(value) if isinstance(value, list) else value
            counts[key] = counts.get(key, 0) + 1
        best = max(counts.items(), key=lambda item: item[1])
        out[axis] = list(best[0]) if isinstance(best[0], tuple) else best[0]
        out[f"{axis}_verbatim"] = verbatim[axis]
        out[f"{axis}_prints"] = len(values)
    return out


__all__ = [
    "CONNECTOR_SCHEMA",
    "collapse_series",
    "connector_parametric_rows",
]
