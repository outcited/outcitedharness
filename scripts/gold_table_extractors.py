#!/usr/bin/env python3
"""Generate dw-recon::table_extractors gold from the LIVE
`harness/electronics/table_extractors.py`.

Every case is computed by the reference itself: kernel cases call the pure
parsers over the same table shapes the pytest pins (plus edge cases), with
`None`/`0`/numeric cells exactly as `extract()` returns them.

Cases cover the traps the parsers exist for: printed physical ids stay
verbatim (`1`, `A1`, `12(1)`, numeric `0` is a real identifier), a `Pin
name` column is never a pin number, semantic fields are field-aligned and
value-filtered (`-`/`—`/`N/A` print no value), parametric rows keep every
printed column verbatim and expand to conversion-free facts with the
`description` field preferred over `symbol`, unselected package columns
drop while shared semantics stay, and headers are scored (the best header
row wins, `MAX`/`TYP`/`MIN` roles require 2+ value headers).

Usage: python3 scripts/gold_table_extractors.py > tests/fixtures/gold/table_extractors_v1.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness.electronics.table_extractors import (  # noqa: E402
    normalize_parametric_facts,
    parse_parametric_table,
    parse_pin_table,
    physical_pin_identifiers,
    pin_identity_rows,
    pin_semantic_roles,
    pin_semantic_values,
    project_pin_table,
)

SHA = "a" * 64


def cases() -> list[dict]:
    out: list[dict] = []

    def add(kind: str, name: str, inputs: dict, expected) -> None:
        out.append({"kind": kind, "name": name, "input": inputs, "expected": expected})

    # The pytest pin table: two package columns, `-` prints no identifier.
    pin_table = {
        "table_index": 2,
        "rows": [
            ["", "LQFP64", "LQFP100", "", "", ""],
            ["Pin name", "Pin number", "Pin number", "Type", "Direction", "Function"],
            ["PA0", "1", "2", "GPIO", "I/O", "ADC0; USART2"],
            ["PA1", "-", "3", "GPIO", "I/O", "ADC1"],
        ],
    }
    add(
        "parse_pin_table",
        "packages_and_verbatim",
        {"table": pin_table, "document_sha256": SHA, "page_1based": 10},
        parse_pin_table(pin_table, document_sha256=SHA, page_1based=10),
    )

    # A Pin name column is never a pin number.
    no_id_table = {
        "table_index": 0,
        "rows": [
            ["Pin name", "Type", "Description"],
            ["PA0", "GPIO", "ADC0"],
        ],
    }
    add(
        "parse_pin_table",
        "pin_name_not_pin_number",
        {"table": no_id_table, "document_sha256": SHA, "page_1based": 1},
        parse_pin_table(no_id_table, document_sha256=SHA, page_1based=1),
    )

    # Numeric 0 is a real identifier; ball ids and footnoted numbers too.
    zero_table = {
        "table_index": 0,
        "rows": [
            ["Pin Name", "Pin(s)", "Description"],
            ["VSS", 0, "Ground"],
        ],
    }
    for name, value in [
        ("numeric_zero", 0),
        ("ball", "A1"),
        ("footnote", "12(1)"),
        ("multi", "A1, B2"),
        ("dash", "-"),
        ("na", "N/A"),
        ("long", "X" * 25),
    ]:
        add(
            "physical_pin_identifiers",
            name,
            {"value": value},
            list(physical_pin_identifiers(value)),
        )
    add(
        "pin_identity_rows",
        "numeric_zero",
        {"table": zero_table},
        pin_identity_rows(zero_table),
    )
    add(
        "pin_semantic_values",
        "numeric_zero",
        {"table": zero_table, "row_index": 1, "pin_no": 0, "name": "VSS"},
        {key: list(value) for key, value in pin_semantic_values(zero_table, 1, pin_no=0, name="VSS").items()},
    )

    # Semantic roles: printed cells only, value-filtered.
    roles_table = {
        "table_index": 0,
        "rows": [
            ["Pin name", "Pin number", "Type", "Dir", "I/O supply domain", "Description"],
            ["PA0", "1", "GPIO", "I/O", "VDD", "ADC0"],
            ["PA1", "2", "-", "-", "—", "N/A"],
        ],
    }
    add("pin_semantic_roles", "printed_only", {"table": roles_table}, list(pin_semantic_roles(roles_table)))
    add(
        "pin_semantic_values",
        "field_aligned",
        {"table": roles_table, "row_index": 1, "pin_no": "1", "name": "PA0"},
        {
            key: list(value)
            for key, value in pin_semantic_values(roles_table, 1, pin_no="1", name="PA0").items()
        },
    )

    # Parametric rows keep printed columns verbatim.
    parametric = {
        "table_index": 1,
        "rows": [
            ["Parameter", "Condition", "Min", "Typ", "Max", "Unit"],
            ["Supply voltage", "TA = 25 C", "1.7", "", "3.6", "V"],
        ],
    }
    add(
        "parse_parametric_table",
        "printed_columns",
        {"table": parametric, "document_sha256": "b" * 64, "page_1based": 42},
        parse_parametric_table(parametric, document_sha256="b" * 64, page_1based=42),
    )
    # Fewer than 2 value headers: rejected.
    add(
        "parse_parametric_table",
        "insufficient_value_headers",
        {
            "table": {
                "rows": [
                    ["Parameter", "Typ"],
                    ["Supply voltage", "3.6"],
                ]
            },
            "document_sha256": "b" * 64,
            "page_1based": 1,
        },
        parse_parametric_table(
            {"rows": [["Parameter", "Typ"], ["Supply voltage", "3.6"]]},
            document_sha256="b" * 64,
            page_1based=1,
        ),
    )

    # Normalizer: conversion-free facts, description preferred, markers
    # omitted.
    for name, row in [
        (
            "expands_values",
            {
                "values_verbatim": {
                    "parameter": "Supply voltage",
                    "condition": "TA = 25 C",
                    "min": "1.7",
                    "max": "3.6",
                    "unit": "V",
                }
            },
        ),
        (
            "description_over_symbol",
            {
                "values_verbatim": {
                    "parameter": "I PVDDQ",
                    "column_1": "PVDD sleep mode current",
                    "typ.": "2.25",
                    "unit": "uA",
                }
            },
        ),
        (
            "markers_omitted",
            {
                "values_verbatim": {
                    "parameter": "Supply voltage",
                    "min": "\u2014",
                    "typ": "N/A",
                    "max": "3.6",
                    "unit": "V",
                }
            },
        ),
    ]:
        # The gold wire sorts object keys; the reference iterates the
        # ORIGINAL insertion order, which travels separately.
        verbatim = row.get("values_verbatim", row)
        add(
            "normalize_parametric_facts",
            name,
            {"row": row, "key_order": list(verbatim)},
            normalize_parametric_facts(row),
        )

    # Projection: unselected package columns drop, shared semantics stay.
    for name, table, header in [
        ("drops_other_packages", pin_table, "LQFP64"),
        ("keeps_unmatched", no_id_table, "Missing"),
    ]:
        add(
            "project_pin_table",
            name,
            {"table": table, "selected_header": header},
            project_pin_table(table, header),
        )
    return out


def main() -> int:
    json.dump({"cases": cases()}, sys.stdout, indent=1, sort_keys=True)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
