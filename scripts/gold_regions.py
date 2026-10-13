#!/usr/bin/env python3
"""Generate dw-recon::regions gold from the LIVE `harness/electronics/regions.py`.

Every case is computed by the reference itself: kernel cases call the pure
structural-discovery readers over table rows and page-evidence mappings
(`None` cells exactly as `extract()` returns them). The pymupdf render half
(`render_*`, `pdftotext_layout_page`) is process I/O and stays Python-side;
the 11 pytest cases pin the scoring half, which is what this gold covers.

Cases cover the traps the scorer exists for: register maps and ordering
guides vetoed (but a real pin header overrides the register veto),
`PHYSICAL_ID` accepting large BGA ball coordinates (`AA10`), physical
identifier columns needing 3+ hits at 45% density, missing pin-definition
headers dropping the score to 0, parametric tables needing a parameter
header AND 2+ value-role headers, text regions bounding only visible
evidence (blocks + tables) and requiring the capability's signal, and
`printed_package_mentions` deduplicating case-insensitively.

Usage: python3 scripts/gold_regions.py > tests/fixtures/gold/regions_v1.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness.electronics.regions import (  # noqa: E402
    parametric_table_score,
    pin_table_score,
    printed_package_mentions,
    structural_parametric_regions,
    structural_pin_regions,
    structural_text_regions,
)


def cases() -> list[dict]:
    out: list[dict] = []

    def add(kind: str, name: str, inputs: dict, expected) -> None:
        out.append({"kind": kind, "name": name, "input": inputs, "expected": expected})

    # --- pin_table_score ----------------------------------------------------
    single = [
        ["Pin Number", "Pin Name", "Type", "Description"],
        ["1", "VIN", "Power", "Input supply"],
        ["2", "GND", "Power", "Ground"],
        ["3", "EN", "Input", "Enable input"],
    ]
    register = [
        ["Register", "Offset", "Reset Value"],
        ["CTRL", "0x00", "0x0000"],
        ["STATUS", "0x04", "0x0000"],
        ["DATA", "0x08", "0x0000"],
    ]
    ordering = [
        ["Ordering Part Number", "Flash Memory (kB)", "Digital Port I/Os (Total)", "Package"],
        ["EFM8BB10F8G-A-QSOP24", "8", "18", "QSOP24"],
        ["EFM8BB10F8G-A-QFN20", "8", "16", "QFN20"],
        ["EFM8BB10F8G-A-SOIC16", "8", "13", "SOIC16"],
    ]
    bga = [
        ["Ball", "Signal Name", "Description"],
        ["AA10", "DDR_D0", "Data"],
        ["AB11", "DDR_D1", "Data"],
        ["AC12", "DDR_D2", "Data"],
    ]
    package_table = [
        ["LQFP 64", "Pin Name", "Description"],
        ["1", "PA0", "GPIO"],
        ["2", "PA1", "GPIO"],
        ["3", "VDD", "Supply"],
    ]
    for name, rows in [
        ("single_package", single),
        ("register_veto", register),
        ("ordering_missing_header", ordering),
        ("bga_balls", bga),
        ("package_table", package_table),
        ("too_few_rows", [["Pin", "Name"], ["1", "PA0"]]),
        ("pin_header_overrides_register_veto", [
            ["Pin Number", "Pin Name", "Offset"],
            ["1", "VIN", "0x00"],
            ["2", "GND", "0x04"],
            ["3", "EN", "0x08"],
        ]),
    ]:
        score, reasons = pin_table_score(rows)
        add(
            "pin_table_score",
            name,
            {"rows": rows},
            [score, list(reasons)],
        )

    # --- parametric_table_score --------------------------------------------
    electrical = [
        ["Parameter", "Conditions", "Min", "Typ", "Max", "Unit"],
        ["Supply voltage", "", "1.7", "3.3", "3.6", "V"],
        ["Current", "", "", "8", "", "mA"],
    ]
    for name, rows in [
        ("electrical", electrical),
        ("register_veto", register),
        ("ordering_veto", [
            ["Ordering Part Number", "Min", "Max"],
            ["X", "1", "2"],
        ]),
        ("missing_parameter_header", [
            ["Symbol", "Min", "Max"],
            ["VDD", "1.7", "3.6"],
        ]),
        ("insufficient_value_headers", [
            ["Parameter", "Min"],
            ["Supply voltage", "1.7"],
        ]),
    ]:
        score, reasons = parametric_table_score(rows)
        add(
            "parametric_table_score",
            name,
            {"rows": rows},
            [score, list(reasons)],
        )

    # --- structural regions ------------------------------------------------
    add(
        "structural_pin_regions",
        "geometry_and_headers",
        {
            "page_evidence": {
                "tables": [
                    {"table_index": 2, "bbox": [10, 20, 300, 500], "rows": package_table}
                ]
            },
        },
        structural_pin_regions({"tables": [{"table_index": 2, "bbox": [10, 20, 300, 500], "rows": package_table}]}),
    )
    add(
        "structural_parametric_regions",
        "electrical",
        {
            "page_evidence": {
                "tables": [
                    {"table_index": 3, "bbox": [10, 20, 400, 500], "rows": electrical},
                    {"table_index": 4, "bbox": [10, 600, 400, 700], "rows": register},
                ]
            },
        },
        structural_parametric_regions({"tables": [
            {"table_index": 3, "bbox": [10, 20, 400, 500], "rows": electrical},
            {"table_index": 4, "bbox": [10, 600, 400, 700], "rows": register},
        ]}),
    )

    # --- text regions ------------------------------------------------------
    summary_page = {
        "blocks": [{"bbox": [20, 30, 300, 100], "text": "Features\nLow power"}],
        "tables": [
            {"bbox": [20, 120, 500, 300], "rows": [["Description", "MCU family"]]}
        ],
    }
    add(
        "structural_text_regions",
        "series_summary",
        {"page_evidence": summary_page, "capability": "series_summary"},
        structural_text_regions(summary_page, "series_summary"),
    )
    ordering_page = {
        "blocks": [{"bbox": [20, 30, 500, 300], "text": "Ordering information"}],
        "tables": [],
    }
    add(
        "structural_text_regions",
        "opn_decoder",
        {"page_evidence": ordering_page, "capability": "opn_decoder"},
        structural_text_regions(ordering_page, "opn_decoder"),
    )
    add(
        "structural_text_regions",
        "no_signal",
        {
            "page_evidence": {"blocks": [{"bbox": [0, 0, 10, 10], "text": "zzz"}], "tables": []},
            "capability": "opn_decoder",
        },
        structural_text_regions({"blocks": [{"bbox": [0, 0, 10, 10], "text": "zzz"}], "tables": []}, "opn_decoder"),
    )

    # --- printed package mentions -----------------------------------------
    add(
        "printed_package_mentions",
        "dedupe_casefold",
        {
            "page_evidence": {
                "blocks": [
                    {"bbox": [0, 0, 100, 20], "text": "40-pin TQFN and 40-pin tqfn"},
                    {"bbox": [0, 30, 100, 50], "text": "Available in LQFP64"},
                ]
            },
        },
        printed_package_mentions({"blocks": [
            {"bbox": [0, 0, 100, 20], "text": "40-pin TQFN and 40-pin tqfn"},
            {"bbox": [0, 30, 100, 50], "text": "Available in LQFP64"},
        ]}),
    )
    return out


def main() -> int:
    json.dump({"cases": cases()}, sys.stdout, indent=1, sort_keys=True)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
