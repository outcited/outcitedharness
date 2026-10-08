"""Deterministic power-mode current extraction (low-power lanes).

Reads printed power-mode rows — ``mode, current, unit, conditions, role`` —
from parametric-shaped tables. Nothing inferred: the mode name must match
the printed mode grammar (shared with the family key-features reader), the
value must be a printed current with its unit in the same row, and every
condition is kept verbatim with only cleanly parseable ``VDD``/``f``/``TA``
anchors typed for selection and cross-substrate checks.

Competing candidates for the same ``(mode, value_role, vdd_v, freq_mhz)``
collapse to one fill row with the industry-standard ``TA = 25 C`` report
preferred (matching the selector-gold rule of the TI parametrics wave);
the winner keeps its verbatim evidence and a count of the collapsed rows.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from harness.electronics.key_features_grid import _LP_MODE, _MODE_WORDS


MODE_CURRENT_SCHEMA = "harness.electronics-mode-current-rows.v1"

_MODE_NAME = re.compile(rf"\b(?:{_MODE_WORDS})\b", re.I)
_CURRENT_CELL = re.compile(
    r"([-+]?\d+(?:\.\d+)?)\s*(nA|µA|uA|μA|mA|A)(?:\s*/\s*(MHz|kHz))?\s*$",
    re.I,
)
_BARE_NUMBER = re.compile(r"^[-+]?\d+(?:\.\d+)?$")
_VDD_ANCHOR = re.compile(
    r"V\s*(?:DD|CC)(?:\s*\w+)?\s*=?\s*([-+]?\d+(?:\.\d+)?)\s*V", re.I
)
_FREQ_ANCHOR = re.compile(
    r"(?:f\s*[A-Za-z]{0,6}|CPU\s*clock|MCLK|HCLK|system\s*clock)"
    r"(?:\s*=\s*f[A-Za-z]{0,6}){0,3}"
    r"\s*=?\s*(\d+(?:\.\d+)?)\s*(MHz|kHz)",
    re.I,
)
_TA_ANCHOR = re.compile(r"T\s*A\s*=?\s*([-+]?\d+(?:\.\d+)?)\s*°?\s*C", re.I)
_UNIT_IN_TEXT = re.compile(r"(nA|µA|uA|μA|mA|A)(?:\s*/\s*(?:MHz|kHz))?\s*$", re.I)
_BRACKET_UNIT = re.compile(r"\s*[\[(][^)\]]*[\])]?\s*$")
_ROLE_HEADER = re.compile(r"^\s*(min|typ(?:\.|cal)?|max|value|nom)\s*$", re.I)
_ROLE_INLINE = re.compile(
    r"(?:\(|—|-|\s)\s*(min|typ(?:\.|cal)?|max|nom)\s*\)?\s*$"
    r"|\b(typ(?:\.|cal)?|min|max|nom)\b(?=[^a-z]{0,3}(?:current|mode|consumption))",
    re.I,
)
_MODE_OR_PARAMETER_HEADER = re.compile(
    r"^\s*(mode|operating\s+mode|parameter|characteristic|symbol|description|current"
    r"|current\s+(?:consumption|draw))\s*$",
    re.I,
)
_CONDITIONS_HEADER = re.compile(r"^\s*(conditions?|test\s+conditions?|remarks?)\s*$", re.I)
_VDD_COLUMN_HEADER = re.compile(r"^V\s*(?:DD|CC|IN)$", re.I)
_UNIT_HEADER = re.compile(r"^\s*units?\s*$", re.I)
_BLANK = re.compile(r"^\s*(?:-|—|–|N/?A|\.)\s*$")
_PROSE_ROW = re.compile(r"\b(?:figure|fig\.|note|see\s+section|typical\s+characteristics)\b", re.I)


def _cell(value: Any) -> str:
    return " ".join(str(value or "").split())


def _role_of(header: str) -> str | None:
    cleaned = _cell(header)
    match = _ROLE_HEADER.match(cleaned) or _ROLE_HEADER.match(
        _BRACKET_UNIT.sub("", cleaned)
    )
    if not match:
        match = _ROLE_INLINE.search(cleaned)
    if not match:
        return None
    token = match.group(1).lower()
    if token.startswith("typ") or token.startswith("nom"):
        return "typ"
    return token


def _parse_current(text: str) -> tuple[float, str] | None:
    cleaned = _cell(text)
    if not cleaned or _BLANK.match(cleaned):
        return None
    match = _CURRENT_CELL.search(cleaned)
    if not match:
        return None
    value = float(match.group(1).replace("−", "-").replace("–", "-"))
    unit = match.group(2)
    if match.group(3):
        unit = f"{unit}/{match.group(3)}"
    return value, unit


def _anchors(text: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    vdd = _VDD_ANCHOR.search(text)
    if vdd:
        out["vdd_v"] = float(vdd.group(1))
    freq = _FREQ_ANCHOR.search(text)
    if freq:
        out["freq_mhz"] = (
            float(freq.group(1))
            if freq.group(2).lower() == "mhz"
            else float(freq.group(1)) / 1000.0
        )
    ta = _TA_ANCHOR.search(text)
    if ta:
        out["ta_c"] = float(ta.group(1))
    return out


_MODE_FILLER = re.compile(
    r"[\s,;/]*(?:mode|current|consumption|level|typ\.?|typical|only|typ\b|@\s*[\d.]+\s*[Vv])\b",
    re.I,
)


def _mode_name(text: str) -> str | None:
    cleaned = _cell(text)
    if not cleaned or _PROSE_ROW.search(cleaned):
        return None
    for pattern in (_LP_MODE, _MODE_NAME):
        match = pattern.search(cleaned)
        if not match:
            continue
        residue = _MODE_FILLER.sub("", cleaned.replace(match.group(0), " ", 1))
        residue = re.sub(r"[\s,;:()\-–—]+", "", residue)
        if not residue:
            return cleaned
        return _cell(match.group(0))
    return None


def _mode_from_cells(cells: Sequence[str], label_index: int | None) -> str | None:
    if label_index is not None and label_index < len(cells):
        direct = _mode_name(cells[label_index])
        if direct is not None:
            return direct
    for text in cells:
        direct = _mode_name(text)
        if direct is not None:
            return direct
    return None


def _header_roles(header: Sequence[Any]) -> dict[str, int]:
    roles: dict[str, int] = {}
    for index, raw in enumerate(header):
        text = _cell(raw)
        role = _role_of(text)
        if role:
            if f"value:{role}" not in roles:
                roles[f"value:{role}"] = index
        elif _MODE_OR_PARAMETER_HEADER.match(text) and "label" not in roles:
            roles["label"] = index
        elif _CONDITIONS_HEADER.match(text) and "conditions" not in roles:
            roles["conditions"] = index
        elif _VDD_COLUMN_HEADER.match(text) and "vdd_column" not in roles:
            roles["vdd_column"] = index
        elif _UNIT_HEADER.match(text) and "unit" not in roles:
            roles["unit"] = index
    return roles


_TA_CLAUSE = re.compile(r"T\s*A\s*=?\s*[-+]?\d+(?:\.\d+)?\s*°?\s*C", re.I)


def _mode_key(mode: str) -> str:
    return re.sub(r"[^a-z0-9]", "", mode.lower()).removesuffix("mode")


def _condition_key(row: Mapping[str, Any]) -> str | None:
    text = row.get("conditions_verbatim")
    if not text:
        return None
    return _TA_CLAUSE.sub("", text).strip(" ,;|") or None


def _signature(row: Mapping[str, Any]) -> tuple[Any, ...]:
    anchors = row["anchors"]
    return (
        _mode_key(row["mode"]),
        row["value_role"],
        anchors.get("vdd_v"),
        anchors.get("freq_mhz"),
        _condition_key(row),
    )


def _weight(row: Mapping[str, Any]) -> tuple[int, int]:
    ta = row["anchors"].get("ta_c")
    return (
        2 if ta == 25 else 0,
        -row.get("_source_order", 0),
    )


def mode_current_rows(
    table: Mapping[str, Any],
    *,
    document_sha256: str,
    page_1based: int,
    source_order: int = 0,
) -> list[dict[str, Any]]:
    """One fill row per printed mode current; blanks and prose never emit."""

    rows = table.get("rows")
    if not isinstance(rows, list) or len(rows) < 2:
        return []
    header_index = None
    roles: dict[str, int] = {}
    for index, row in enumerate(rows[:6]):
        if not isinstance(row, list):
            continue
        candidate = _header_roles(row)
        if "label" in candidate and any(
            key.startswith("value:") for key in candidate
        ):
            header_index = index
            roles = candidate
            break
    if header_index is None:
        return []
    unit_index = roles.get("unit")
    label_index = roles.get("label")
    conditions_index = roles.get("conditions")
    output: list[dict[str, Any]] = []
    for row_index, row in enumerate(rows[header_index + 1 :], header_index + 1):
        if not isinstance(row, list):
            continue
        cells = [_cell(value) for value in row]
        mode = _mode_from_cells(cells, label_index)
        if mode is None:
            continue
        condition_text = cells[conditions_index] if conditions_index is not None and conditions_index < len(cells) else ""
        inline = " ".join(
            text
            for index, text in enumerate(cells)
            if index not in (label_index, conditions_index)
        )
        anchors = _anchors(f"{condition_text} {' '.join(cells)}")
        vdd_index = roles.get("vdd_column")
        if anchors.get("vdd_v") is None and vdd_index is not None and vdd_index < len(cells):
            vdd_cell = cells[vdd_index]
            vdd_match = re.search(r"([-+]?\d+(?:\.\d+)?)\s*V", vdd_cell, re.I)
            if vdd_match:
                anchors["vdd_v"] = float(vdd_match.group(1))
        header_row = [_cell(value) for value in rows[header_index]]
        for key, value_index in sorted(roles.items()):
            if not key.startswith("value:"):
                continue
            if value_index >= len(cells):
                continue
            parsed = _parse_current(cells[value_index])
            if parsed is None and _BARE_NUMBER.match(cells[value_index]):
                unit_sources = []
                if unit_index is not None and unit_index < len(cells):
                    unit_sources.append(cells[unit_index])
                if value_index < len(header_row):
                    unit_sources.append(header_row[value_index])
                for source in unit_sources:
                    unit_match = _UNIT_IN_TEXT.search(source)
                    if unit_match:
                        per = re.search(r"/\s*(MHz|kHz)", source, re.I)
                        parsed = (
                            float(cells[value_index].replace("−", "-").replace("–", "-")),
                            unit_match.group(1) + (f"/{per.group(1)}" if per else ""),
                        )
                        break
            if parsed is None:
                continue
            value, unit = parsed
            verbatim = " | ".join(
                text
                for text in (cells[value_index], condition_text, inline)
                if text and not _BLANK.match(text)
            )
            output.append(
                {
                    "schema": MODE_CURRENT_SCHEMA,
                    "document_sha256": document_sha256,
                    "page_1based": page_1based,
                    "table_index": table.get("table_index"),
                    "row_index": row_index,
                    "mode": mode,
                    "value": value,
                    "unit": unit,
                    "value_role": key.split(":", 1)[1],
                    "conditions_verbatim": condition_text or None,
                    "anchors": anchors,
                    "verbatim": verbatim[:220],
                    "method": "deterministic_mode_currents_v1",
                    "_source_order": source_order,
                }
            )
    return output


def collapse_competing(
    rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """One winner per (mode, role, vdd, freq); TA = 25 C preferred."""

    best: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in rows:
        key = _signature(row)
        current = best.get(key)
        if current is None or _weight(row) > _weight(current):
            best[key] = dict(row)
    output = []
    for row in best.values():
        row.pop("_source_order", None)
        output.append(row)
    return output


__all__ = [
    "MODE_CURRENT_SCHEMA",
    "collapse_competing",
    "mode_current_rows",
]
