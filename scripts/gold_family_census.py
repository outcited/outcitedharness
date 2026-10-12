#!/usr/bin/env python3
"""Generate dw-recon::family_census gold from the LIVE
`harness/electronics/family_census.py` reference.

Every case is computed by the reference itself: census cases render a real
PDF (pymupdf) and run `census_document` end to end, kernel cases call the
pure functions the unit tests pin. The inputs the Rust port consumes travel
in the gold — page texts as `page.get_text()` returns them and table grids as
`find_tables().extract()` returns them — so the port replays identical input.
Volatile keys (`source_path`, `document_sha256`) are stripped.

Cases cover the traps the census exists for: the family-matrix device table
(parts-as-columns, GD32/ST style), the selection-guide table (parts-as-rows),
electrical-characteristics and absolute-maximum tables that must NOT read as
device tables, caption rows merged into grids, ordering-variant documents,
single-OPN documents, reference-manual class, cpu-core subject, the
no-text-layer instrument failure, pin bogeys from package tokens, and the
code-flash/data-flash qualifier trap.

Usage: python3 scripts/gold_family_census.py > tests/fixtures/gold/family_census_v1.json
"""
from __future__ import annotations

import contextlib
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pymupdf  # noqa: E402
from harness.electronics.family_census import (  # noqa: E402
    above_opn_kind,
    attributes_in_label,
    census_document,
    classify_grain,
    classify_table,
    is_concrete_part_token,
    is_wildcard_token,
    packages_from_text,
    parts_from_text,
    stems_from_tokens,
    strip_title_rows,
    summarize,
    wildcards_from_text,
)

VOLATILE = {"source_path", "document_sha256"}


def build_pdf(pages: list[dict], path: Path) -> None:
    """Render one PDF: each page is {"text": ..., "table": [[...]] | None}."""
    doc = pymupdf.open()
    for spec in pages:
        page = doc.new_page(width=612, height=792)
        page.insert_textbox(pymupdf.Rect(72, 48, 540, 180), spec.get("text", ""), fontsize=11)
        table = spec.get("table")
        if table:
            cols = max(len(r) for r in table)
            width = 468.0 / cols
            top = 220.0
            height = 22.0
            xs = [72.0 + i * width for i in range(cols + 1)]
            ys = [top + r * height for r in range(len(table) + 1)]
            for y in ys:
                page.draw_line(
                    pymupdf.Point(xs[0], y), pymupdf.Point(xs[-1], y), width=0.7
                )
            for x in xs:
                page.draw_line(
                    pymupdf.Point(x, ys[0]), pymupdf.Point(x, ys[-1]), width=0.7
                )
            for r, row in enumerate(table):
                for c, val in enumerate(row):
                    if val is None:
                        continue
                    page.insert_textbox(
                        pymupdf.Rect(xs[c] + 3, ys[r] + 5, xs[c + 1] - 2, ys[r + 1] - 2),
                        str(val),
                        fontsize=8,
                    )
    doc.save(path)
    doc.close()


def pdf_inputs(path: Path) -> tuple[list[str], list[dict]]:
    """The PDF layer's outputs: page texts and per-page table grids."""
    doc = pymupdf.open(path)
    texts = [p.get_text() for p in doc]
    tables = []
    with contextlib.redirect_stdout(sys.stderr):
        for i, p in enumerate(doc):
            grids = []
            for t in p.find_tables().tables:
                try:
                    grids.append(t.extract())
                except Exception:  # noqa: BLE001
                    pass
            tables.append({"page": i + 1, "tables": grids})
    doc.close()
    return texts, tables


CENSUS_CASES = [
    (
        "family_matrix_columns",
        {"registry_stems": 3, "vendor": "st", "category": "mcu", "lane_pages": None},
        [
            {
                "text": "GD32F405xx Datasheet\nGigaDevice Semiconductor\nProduct information\n"
                "For GD32F405xx, GD32F407xx and GD32F450xx ARM Cortex-M4 32-bit microcontrollers.",
            },
            {
                "text": "Device comparison table. Available in LQFP64 and LQFP100 packages.",
                "table": [
                    ["Part Number", "", "GD32F405xx", "", ""],
                    ["", "", "RE", "RG", "RK"],
                    ["Flash", "Code area (KB)", "512", "512", "512"],
                    ["", "Data area (KB)", "0", "512", "2560"],
                    ["SRAM (KB)", "", "192", "192", "192"],
                    ["Timers", "General timer", "8", "8", "8"],
                    ["Package", "", "LQFP64", "LQFP64", "LQFP64"],
                ],
            },
        ],
    ),
    (
        "selection_rows",
        {"registry_stems": 0, "vendor": "nxp", "category": "mcu", "lane_pages": None},
        [
            {
                "text": "S32K1xx Series Selection Guide\nAutomotive general purpose MCUs\n"
                "For S32K142, S32K144 and S32K146 automotive microcontrollers.",
            },
            {
                "text": "Device selection table.",
                "table": [
                    ["Device", "Flash (KB)", "SRAM (KB)", "Pins", "Max freq (MHz)"],
                    ["S32K142", "256", "32", "64", "112"],
                    ["S32K144", "512", "64", "100", "112"],
                    ["S32K146", "1024", "128", "144", "112"],
                ],
            },
        ],
    ),
    (
        "caption_row_stripped",
        {"registry_stems": 0, "vendor": "renesas", "category": "mcu", "lane_pages": None},
        [
            {
                "text": "RA2E2 Group Datasheet\nFor R7FA2E2A72DNK, R7FA2E2A73CFL and "
                "R7FA2E2A74CFL 32-bit microcontrollers.",
            },
            {
                "text": "Table 1.12 Product list (1 of 2)",
                "table": [
                    ["Table 1.12 Product list (1 of 2)", None, None, None],
                    ["Product part number", "Code flash", "SRAM", "Temp"],
                    ["R7FA2E2A72DNK", "128", "16", "-40 to +105"],
                    ["R7FA2E2A73CFL", "64", "16", "-40 to +85"],
                    ["R7FA2E2A74CFL", "32", "8", "-40 to +85"],
                ],
            },
        ],
    ),
    (
        "electrical_chars_rejected",
        {"registry_stems": 0, "vendor": "st", "category": "mcu", "lane_pages": None},
        [
            {
                "text": "STM32C011x4 Datasheet\nFor STM32C011x4/x6 microcontrollers.",
            },
            {
                "text": "Electrical characteristics.",
                "table": [
                    ["Symbol", "Parameter", "Conditions", "Min", "Typ", "Max", "Unit"],
                    ["fHSE", "External clock frequency", "", "4", "", "48", "MHz"],
                    ["TA", "Ambient temperature", "", "-40", "", "85", "C"],
                    ["VDD", "Operating voltage", "", "2.0", "", "3.6", "V"],
                ],
            },
        ],
    ),
    (
        "abs_max_ratings_rejected",
        {"registry_stems": 0, "vendor": "ti", "category": "power", "lane_pages": None},
        [
            {
                "text": "TPS7A47 Datasheet\nFor TPS7A47xx low-noise linear regulators.",
            },
            {
                "text": "Absolute maximum ratings.",
                "table": [
                    ["", "", "MIN", "MAX", "UNIT"],
                    ["Input voltage", "VBUS", "-0.3", "18", "V"],
                    ["Operating junction temperature", "TJ", "-40", "125", "C"],
                    ["Pin voltage", "PG, CE", "-0.3", "7", "V"],
                    ["Timer", "TMR", "", "10", "s"],
                ],
            },
        ],
    ),
    (
        "ordering_variants",
        {"registry_stems": 0, "vendor": "ti", "category": "power", "lane_pages": None},
        [
            {
                "text": "BQ2000T Dual Output Regulator\nOrdering information: BQ2000TPN and "
                "BQ2000TSN switching regulators. Available in SOIC-8 and PDIP-8.",
            },
        ],
    ),
    (
        "single_opn",
        {"registry_stems": 0, "vendor": "ti", "category": "power", "lane_pages": None},
        [
            {
                "text": "BQ2000T Switching Regulator\nFeatures: 100 kHz fixed frequency "
                "operation, cycle-by-cycle current limiting.",
            },
        ],
    ),
    (
        "reference_manual_class",
        {"registry_stems": 2, "vendor": "st", "category": "mcu", "lane_pages": None},
        [
            {
                "text": "RM0390 Reference manual\nSTM32F446xx advanced Arm-based 32-bit MCUs\n"
                "This manual describes how to use the memory and peripherals of "
                "STM32F446xx microcontrollers.",
            },
        ],
    ),
    (
        "cpu_core_subject",
        {"registry_stems": 0, "vendor": "st", "category": "mcu", "lane_pages": None},
        [
            {
                "text": "PM0214 Programming manual\nSTM32 Cortex-M4 MCUs and MPUs programming manual\n"
                "This programming manual describes the instruction set core programming "
                "model for the Arm Cortex-M4 core.",
            },
        ],
    ),
    (
        "no_text_layer",
        {"registry_stems": 0, "vendor": "acme", "category": "mcu", "lane_pages": None},
        [{"text": "tiny"}],
    ),
    (
        "pin_bogeys_and_packages",
        {"registry_stems": 0, "vendor": "acme", "category": "mcu", "lane_pages": None},
        [
            {
                "text": "ACME1000 Dual LDO Family\nOrdering information: ACME1000MP and "
                "ACME1000TP. Available in LQFP64, LQFP100 and UFBGA144 packages; 48-pin QFN.",
            },
        ],
    ),
    (
        "lane_pages_hint",
        {
            "registry_stems": 0,
            "vendor": "gigadevice",
            "category": "mcu",
            "lane_pages": {"series_summary": [2], "opn_decoder": []},
        },
        [
            {
                "text": "GD32F103xx Datasheet\nFor GD32F103xx and GD32F153xx microcontrollers.",
            },
            {
                "text": "Ordering information. No keyword heading here at all.",
                "table": [
                    ["Device name", "Flash", "SRAM", "Package"],
                    ["GD32F103C8", "64", "20", "LQFP48"],
                    ["GD32F103CB", "128", "20", "LQFP48"],
                ],
            },
        ],
    ),
]


def kernel_cases() -> list[dict]:
    cases: list[dict] = []

    def add(kind: str, name: str, inputs: dict, expected) -> None:
        cases.append({"kind": kind, "name": name, **inputs, "expected": expected})

    add("is_wildcard_token", "wildcard_gd32", {"token": "GD32F405xx"}, True)
    add("is_wildcard_token", "wildcard_st_g0", {"token": "STM32G0x1"}, True)
    add("is_wildcard_token", "concrete_not_wildcard", {"token": "STM32C011F4"}, False)

    add(
        "wildcards_from_text",
        "three_series",
        {"text": "For GD32F405xx, GD32F407xx and GD32F450xx"},
        wildcards_from_text("For GD32F405xx, GD32F407xx and GD32F450xx"),
    )
    add(
        "parts_from_text",
        "wildcard_is_not_a_part",
        {"text": "For GD32F405xx microcontrollers", "stems": None},
        parts_from_text("For GD32F405xx microcontrollers", None),
    )

    for token in ("STM32C011F4", "R7FA0E1073CFJ", "S32K144"):
        add(
            "is_concrete_part_token",
            f"concrete_{token}",
            {"token": token},
            is_concrete_part_token(token),
        )
    for junk in (
        "RM0090",
        "DS40002183B",
        "LQFP64",
        "VQFN48",
        "OSC32K",
        "XOSC32K",
        "SERNUM15",
        "YUV444",
        "R01DS0427EJ0120",
        "PLQP0032GB-A",
        "REV1",
        "PA10",
    ):
        add(
            "is_concrete_part_token",
            f"junk_{junk}",
            {"token": junk},
            is_concrete_part_token(junk),
        )

    add(
        "parts_from_text",
        "anchored_to_family_stems",
        {
            "text": "Ordering: STM32C011F4, STM32C011J6 and companion LDO XC6206P332MR",
            "stems": ["STM32"],
        },
        parts_from_text(
            "Ordering: STM32C011F4, STM32C011J6 and companion LDO XC6206P332MR",
            {"STM32"},
        ),
    )
    add(
        "stems_from_tokens",
        "family_stems",
        {"tokens": ["STM32C011x4", "GD32F405xx", "AVR128DA28"]},
        sorted(stems_from_tokens(["STM32C011x4", "GD32F405xx", "AVR128DA28"])),
    )

    add(
        "packages_from_text",
        "pin_bogeys",
        {"text": "Available in LQFP64, LQFP100 and UFBGA144 packages; 48-pin QFN"},
        packages_from_text(
            "Available in LQFP64, LQFP100 and UFBGA144 packages; 48-pin QFN"
        ),
    )

    for label in (
        "Flash Data area (KB)",
        "Flash Code area (KB)",
        "Flash memory (Kbyte)",
        "Data flash memory",
        "EEPROM",
        "Standby mode current (uA)",
        "",
    ):
        add(
            "attributes_in_label",
            f"label_{label!r}",
            {"label": label},
            attributes_in_label(label),
        )

    add(
        "strip_title_rows",
        "caption_stripped",
        {
            "rows": [
                ["Table 1.12 Product list (1 of 2)", None, None, None],
                ["Product part number", "Code flash", "SRAM", "Operating temperature"],
                ["R7FA2E1A93CFM", "128", "16", "-40 to +105C"],
                ["R7FA2E1A72DFL", "64", "16", "-40 to +85C"],
            ]
        },
        strip_title_rows(
            [
                ["Table 1.12 Product list (1 of 2)", None, None, None],
                ["Product part number", "Code flash", "SRAM", "Operating temperature"],
                ["R7FA2E1A93CFM", "128", "16", "-40 to +105C"],
                ["R7FA2E1A72DFL", "64", "16", "-40 to +85C"],
            ]
        ),
    )

    add(
        "classify_table",
        "parts_as_columns",
        {
            "rows": [
                ["Part Number", "", "GD32F405xx", "", ""],
                ["", "", "RE", "RG", "RK"],
                ["Flash", "Code area (KB)", "512", "512", "512"],
                ["", "Data area (KB)", "0", "512", "2560"],
                ["SRAM (KB)", "", "192", "192", "192"],
                ["Timers", "General timer", "8", "8", "8"],
                ["Package", "", "LQFP64", "LQFP64", "LQFP64"],
            ]
        },
        classify_table(
            [
                ["Part Number", "", "GD32F405xx", "", ""],
                ["", "", "RE", "RG", "RK"],
                ["Flash", "Code area (KB)", "512", "512", "512"],
                ["", "Data area (KB)", "0", "512", "2560"],
                ["SRAM (KB)", "", "192", "192", "192"],
                ["Timers", "General timer", "8", "8", "8"],
                ["Package", "", "LQFP64", "LQFP64", "LQFP64"],
            ]
        ),
    )
    add(
        "classify_table",
        "parts_as_rows",
        {
            "rows": [
                ["Device", "Flash (KB)", "SRAM (KB)", "Pins", "Max freq (MHz)"],
                ["S32K142", "256", "32", "64", "112"],
                ["S32K144", "512", "64", "100", "112"],
                ["S32K146", "1024", "128", "144", "112"],
            ]
        },
        classify_table(
            [
                ["Device", "Flash (KB)", "SRAM (KB)", "Pins", "Max freq (MHz)"],
                ["S32K142", "256", "32", "64", "112"],
                ["S32K144", "512", "64", "100", "112"],
                ["S32K146", "1024", "128", "144", "112"],
            ]
        ),
    )
    add(
        "classify_table",
        "electrical_chars",
        {
            "rows": [
                ["Symbol", "Parameter", "Conditions", "Min", "Typ", "Max", "Unit"],
                ["fHSE", "External clock frequency", "", "4", "", "48", "MHz"],
                ["TA", "Ambient temperature", "", "-40", "", "85", "C"],
                ["VDD", "Operating voltage", "", "2.0", "", "3.6", "V"],
            ]
        },
        classify_table(
            [
                ["Symbol", "Parameter", "Conditions", "Min", "Typ", "Max", "Unit"],
                ["fHSE", "External clock frequency", "", "4", "", "48", "MHz"],
                ["TA", "Ambient temperature", "", "-40", "", "85", "C"],
                ["VDD", "Operating voltage", "", "2.0", "", "3.6", "V"],
            ]
        ),
    )
    add(
        "classify_table",
        "abs_max",
        {
            "rows": [
                ["", "", "MIN", "MAX", "UNIT"],
                ["Input voltage", "VBUS", "-0.3", "18", "V"],
                ["Operating junction temperature", "TJ", "-40", "125", "C"],
                ["Pin voltage", "PG, CE", "-0.3", "7", "V"],
                ["Timer", "TMR", "", "10", "s"],
            ]
        },
        classify_table(
            [
                ["", "", "MIN", "MAX", "UNIT"],
                ["Input voltage", "VBUS", "-0.3", "18", "V"],
                ["Operating junction temperature", "TJ", "-40", "125", "C"],
                ["Pin voltage", "PG, CE", "-0.3", "7", "V"],
                ["Timer", "TMR", "", "10", "s"],
            ]
        ),
    )
    add(
        "classify_table",
        "too_small",
        {"rows": [["a", "b", "c"], ["d", "e", "f"]]},
        classify_table([["a", "b", "c"], ["d", "e", "f"]]),
    )

    grain_cases = [
        (
            "grain_above_opn",
            {
                "scope_status": "read_ok",
                "document_subject": "device_family",
                "document_class": "datasheet",
                "series_tokens": ["STM32C011x4"],
                "parts_named": ["STM32C011F4", "STM32C011J6"],
                "device_tables": [{"variant_count": 5}],
                "registry_stems": 3,
            },
        ),
        (
            "grain_single_opn",
            {
                "scope_status": "read_ok",
                "document_subject": "device_family",
                "document_class": "datasheet",
                "series_tokens": [],
                "parts_named": ["BQ2000T"],
                "device_tables": [],
                "registry_stems": 1,
            },
        ),
        (
            "grain_not_device_family",
            {
                "scope_status": "read_ok",
                "document_subject": "cpu_core",
                "document_class": "programming_manual",
                "series_tokens": ["STM32F4xx"],
                "parts_named": [],
                "device_tables": [],
                "registry_stems": 1,
            },
        ),
        (
            "grain_extraction_failed",
            {
                "scope_status": "extraction_failed",
                "document_subject": "unknown",
                "document_class": "unknown",
                "series_tokens": [],
                "parts_named": [],
                "device_tables": [],
                "registry_stems": 0,
            },
        ),
        (
            "grain_reference_manual",
            {
                "scope_status": "read_ok",
                "document_subject": "device_family",
                "document_class": "reference_manual",
                "series_tokens": [],
                "parts_named": [],
                "device_tables": [],
                "registry_stems": 0,
            },
        ),
    ]
    for name, args in grain_cases:
        add(
            "classify_grain",
            name,
            {"args": args},
            list(classify_grain(**args)),
        )

    kind_cases = [
        (
            "kind_family_matrix",
            {
                "document_class": "datasheet",
                "series_tokens": [],
                "parts_named": [],
                "device_tables": [
                    {
                        "variant_count": 4,
                        "attributes_addressed": ["code_flash_kb", "sram_kb", "pin_count"],
                    }
                ],
            },
        ),
        (
            "kind_manual",
            {
                "document_class": "reference_manual",
                "series_tokens": ["STM32F4xx"],
                "parts_named": [],
                "device_tables": [],
            },
        ),
        (
            "kind_ordering_variants",
            {
                "document_class": "datasheet",
                "series_tokens": [],
                "parts_named": ["BQ2000TPN", "BQ2000TSN"],
                "device_tables": [],
            },
        ),
        (
            "kind_wildcard_only",
            {
                "document_class": "datasheet",
                "series_tokens": ["GD32F405xx"],
                "parts_named": [],
                "device_tables": [],
            },
        ),
        (
            "kind_thin_matrix_unclassified",
            {
                "document_class": "datasheet",
                "series_tokens": [],
                "parts_named": [],
                "device_tables": [
                    {"variant_count": 4, "attributes_addressed": ["freq_mhz", "temp_range"]}
                ],
            },
        ),
    ]
    for name, args in kind_cases:
        add("above_opn_kind", name, {"args": args}, above_opn_kind(**args))

    summary_rows = [
        {
            "vendor": "st",
            "grain": "above_opn",
            "above_opn_kind": "family_matrix",
            "document_class": "datasheet",
            "device_table_located": True,
            "scope_statement": None,
            "parts_named_count": 6,
            "pin_bogeys": [20],
            "denominator_cells": 78,
            "attributes_addressed": ["code_flash_kb", "sram_kb"],
        },
        {"vendor": "st", "grain": "single_opn", "document_class": "datasheet"},
        {
            "vendor": "ti",
            "grain": "above_opn",
            "above_opn_kind": "ordering_variants",
            "document_class": "datasheet",
            "device_table_located": False,
            "scope_statement": "For TPS7A47xx",
            "parts_named_count": 3,
            "pin_bogeys": [],
            "denominator_cells": 0,
            "attributes_addressed": [],
        },
    ]
    add("summarize", "by_vendor_and_attribute", {"rows": summary_rows}, summarize(summary_rows))
    return cases


def main() -> int:
    out: dict = {"cases": []}
    with tempfile.TemporaryDirectory() as td:
        for name, params, page_specs in CENSUS_CASES:
            pdf = Path(td) / f"{name}.pdf"
            build_pdf(page_specs, pdf)
            with contextlib.redirect_stdout(sys.stderr):
                row = census_document(
                    pdf,
                    lane_pages=params.get("lane_pages"),
                    registry_stems=params.get("registry_stems", 0),
                    vendor=params.get("vendor"),
                    category=params.get("category"),
                )
            row = {k: v for k, v in row.items() if k not in VOLATILE}
            texts, tables = pdf_inputs(pdf)
            out["cases"].append(
                {
                    "kind": "census",
                    "name": name,
                    "registry_stems": params.get("registry_stems", 0),
                    "vendor": params.get("vendor"),
                    "category": params.get("category"),
                    "lane_pages": params.get("lane_pages"),
                    "pages": texts,
                    "tables": tables,
                    "expected": row,
                }
            )
    out["cases"].extend(kernel_cases())
    json.dump(out, sys.stdout, indent=1, sort_keys=True)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
