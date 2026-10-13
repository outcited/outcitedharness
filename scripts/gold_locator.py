#!/usr/bin/env python3
"""Generate dw-recon::locator gold from the LIVE `harness/electronics/locator.py`.

Every case is computed by the reference itself: `locate` cases feed a mock
document (TOC + per-page texts and extracted table grids) through
`locate_pin_definition_pages`, kernel cases call the pure readers. The
find_tables() layer is PDF-layer (the port receives the same grids the
reference extracts), so page tables travel in the gold as row grids.

Cases cover the traps the locator exists for: definition tables only (never
ballout figures, legends or alternate-function tables), `(continued)`
walk-back, consecutive definition pages stopping at the first gap, package
columns matched by identity never by cousin name, qualified-variant
withholding, the TI borderless single-package relaxation, JEDEC
corroboration counts, and `validate_physical_pin_truth`'s unique-identifier
and LQFP range gates.

Usage: python3 scripts/gold_locator.py > tests/fixtures/gold/locator_v1.json
"""
from __future__ import annotations

import contextlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness.electronics.locator import (  # noqa: E402
    _corroborating_jedec_ids,
    classify_title,
    locate_pin_definition_pages,
    match_package_column,
    package_headers,
    package_pin_count,
    validate_physical_pin_truth,
)


class _Table:
    def __init__(self, rows):
        self.rows = rows

    def extract(self):
        return self.rows


class _Tables:
    def __init__(self, tables):
        self.tables = tables


class _Page:
    def __init__(self, text: str, tables: list):
        self.text = text
        self.tables = tables

    def get_text(self, mode: str = "text"):
        return self.text

    def find_tables(self):
        return _Tables([_Table(rows) for rows in self.tables]) if self.tables is not None else None


class _Document:
    def __init__(self, toc, pages):
        self.toc = toc
        self.pages = pages
        self.page_count = len(pages)

    def get_toc(self):
        return self.toc

    def __getitem__(self, index):
        return self.pages[index]


def _hdr(package: str, count: int) -> list:
    return [[package, "Pin name"], [f"{count}-pin", ""]]


def _definition_rows(package: str, count: int, pins: list[tuple[str, str]]) -> list:
    rows = [[package, "Pin name"], ["", ""]]
    for number, name in pins:
        rows.append([number, name])
    return rows


def _run_locate(name: str, case: dict) -> dict:
    toc = [list(entry) for entry in case["toc"]]
    pages = [
        _Page(spec["text"], spec.get("tables")) for spec in case["pages"]
    ]
    document = _Document(toc, pages)
    captured_tables = [
        {"tables": spec.get("tables")} for spec in case["pages"]
    ]
    try:
        with contextlib.redirect_stdout(sys.stderr):
            result = locate_pin_definition_pages(
                document,
                document_id=case["document_id"],
                requested_package=case["requested_package"],
                expected_package_pins=case["expected_package_pins"],
                source_path=case["source_path"],
                text_section_single_package=case.get("text_section_single_package", False),
            )
        expected: object = {
            "document_id": result.document_id,
            "requested_package": result.requested_package,
            "expected_package_pins": result.expected_package_pins,
            "source_path": result.source_path,
            "status": result.status,
            "reason": result.reason,
            "toc_class": result.toc_class,
            "toc_title": result.toc_title,
            "pages_1based": list(result.pages_1based),
            "column_header": result.column_header,
            "column_headers_all": list(result.column_headers_all),
            "corroborating_jedec_ids": result.corroborating_jedec_ids,
        }
        error = None
    except ValueError as exc:
        expected = None
        error = str(exc)
    return {
        "kind": "locate",
        "name": name,
        "input": {
            **{key: value for key, value in case.items() if key != "tables"},
            "page_tables": captured_tables,
        },
        "expected": expected,
        "error": error,
    }


def locate_cases() -> list[dict]:
    cases: list[dict] = []
    pins64 = [(str(i), f"P{i}") for i in range(1, 65)]
    definition64 = _definition_rows("LQFP64", 64, pins64)

    # TOC locates a definition table; pages carry it; JEDEC ids corroborate.
    cases.append(
        _run_locate(
            "toc_definition_table_and_header_match",
            {
                "document_id": "d" * 64,
                "requested_package": "LQFP64",
                "expected_package_pins": 64,
                "source_path": "/corpus/atom.pdf",
                "toc": [[1, "Table 5. LQFP64 pin and ball definition", 3], [1, "Electrical", 9]],
                "pages": [
                    {"text": "cover"},
                    {"text": "intro"},
                    {"text": "def", "tables": [definition64]},
                    {"text": "more", "tables": [definition64]},
                    {"text": "x"}, {"text": "x"}, {"text": "x"}, {"text": "x"},
                    {"text": "elec"},
                ],
            },
        )
    )

    # A figure title is not a definition table; a legend neither.
    cases.append(
        _run_locate(
            "figure_and_legend_rejected",
            {
                "document_id": "d" * 64,
                "requested_package": "LQFP64",
                "expected_package_pins": 64,
                "source_path": "/corpus/fig.pdf",
                "toc": [[1, "Figure 3. LQFP64 ballout", 2], [1, "Table 4. Abbreviation definition pin name", 3]],
                "pages": [
                    {"text": "cover"},
                    {"text": "fig", "tables": []},
                    {
                        "text": "legend",
                        "tables": [[["Abbreviation", "Definition"], ["Pin name", "x"]]],
                    },
                ],
            },
        )
    )

    # Consecutive pages stop at the first gap.
    cases.append(
        _run_locate(
            "consecutive_pages_stop_at_gap",
            {
                "document_id": "d" * 64,
                "requested_package": "LQFP64",
                "expected_package_pins": 64,
                "source_path": "/corpus/gap.pdf",
                "toc": [[1, "Table 5. LQFP64 pin and ball definition", 2], [1, "Electrical", 7]],
                "pages": [
                    {"text": "cover"},
                    {"text": "def", "tables": [definition64]},
                    {"text": "def", "tables": [definition64]},
                    {"text": "not-a-table"},
                    {"text": "def", "tables": [definition64]},
                    {"text": "x"},
                    {"text": "elec"},
                ],
            },
        )
    )

    # (continued) walks back over earlier definition pages.
    cases.append(
        _run_locate(
            "continued_walks_back",
            {
                "document_id": "d" * 64,
                "requested_package": "LQFP64",
                "expected_package_pins": 64,
                "source_path": "/corpus/cont.pdf",
                "toc": [[1, "Table 5. LQFP64 pin definition (continued)", 4], [1, "Electrical", 8]],
                "pages": [
                    {"text": "cover"},
                    {"text": "def", "tables": [definition64]},
                    {"text": "def", "tables": [definition64]},
                    {"text": "def", "tables": [definition64]},
                    {"text": "def", "tables": [definition64]},
                    {"text": "x"}, {"text": "x"},
                    {"text": "elec"},
                ],
            },
        )
    )

    # Cousin packages must never be substituted; the column is missing.
    cases.append(
        _run_locate(
            "withhold_when_exact_column_missing",
            {
                "document_id": "d" * 64,
                "requested_package": "LQFP64",
                "expected_package_pins": 64,
                "source_path": "/corpus/cousin.pdf",
                "toc": [[1, "Table 5. LQFP pin and ball definition", 2], [1, "Electrical", 5]],
                "pages": [
                    {"text": "cover"},
                    {"text": "def", "tables": [_definition_rows("LQFP48", 48, pins64[:48])]},
                    {"text": "x"}, {"text": "x"},
                    {"text": "elec"},
                ],
            },
        )
    )

    # No ruled tables at all: withhold, unless single-package sends 3 pages.
    no_tables = {
        "document_id": "d" * 64,
        "requested_package": "LQFP64",
        "expected_package_pins": 64,
        "source_path": "/corpus/ti.pdf",
        "toc": [[1, "Pin descriptions", 3], [1, "Electrical", 20]],
        "pages": [{"text": "cover"}, {"text": "intro"}, *([{"text": "fn"}] * 17), {"text": "elec"}],
    }
    cases.append(_run_locate("withhold_empty_find_tables", {**no_tables}))
    cases.append(
        _run_locate(
            "single_package_sends_three_pages",
            {**no_tables, "text_section_single_package": True},
        )
    )

    # TOC has no definition table and no pin section: withhold.
    cases.append(
        _run_locate(
            "toc_has_nothing",
            {
                "document_id": "d" * 64,
                "requested_package": "LQFP64",
                "expected_package_pins": 64,
                "source_path": "/corpus/none.pdf",
                "toc": [[1, "Overview", 1], [1, "Electrical", 3]],
                "pages": [{"text": "a"}, {"text": "b"}, {"text": "c"}],
            },
        )
    )

    # A pin SECTION (not a table caption) locates the pages.
    cases.append(
        _run_locate(
            "pin_section_locates_pages",
            {
                "document_id": "d" * 64,
                "requested_package": "LQFP64",
                "expected_package_pins": 64,
                "source_path": "/corpus/sect.pdf",
                "toc": [
                    [1, "Pin configuration and functions", 2],
                    [1, "Electrical characteristics", 6],
                ],
                "pages": [
                    {"text": "cover"},
                    {"text": "def", "tables": [_definition_rows("LQFP64", 64, pins64)]},
                    {"text": "def", "tables": [_definition_rows("LQFP64", 64, pins64)]},
                    {"text": "x"}, {"text": "x"},
                    {"text": "elec"},
                ],
            },
        )
    )

    # JEDEC corroboration counts distinct identifiers in the selected column.
    jedec_rows = [
        ["LQFP64", "Pin name", "LQFP48"],
        ["", "", ""],
        *[[str(i), f"P{i}", chr(64 + (i % 26 or 26)) + str(i % 12 + 1)] for i in range(1, 65)],
    ]
    cases.append(
        _run_locate(
            "jedec_corroboration",
            {
                "document_id": "d" * 64,
                "requested_package": "LQFP64",
                "expected_package_pins": 64,
                "source_path": "/corpus/jedec.pdf",
                "toc": [[1, "Table 5. LQFP64 pin and ball definition", 2], [1, "Electrical", 4]],
                "pages": [
                    {"text": "cover"},
                    {"text": "def", "tables": [jedec_rows]},
                    {"text": "x"},
                    {"text": "elec"},
                ],
            },
        )
    )

    # Invalid requests.
    for name, request in [
        ("empty_document_id", {"document_id": ""}),
        ("empty_package", {"requested_package": ""}),
        ("zero_pins", {"expected_package_pins": 0}),
    ]:
        base = {
            "document_id": "d" * 64,
            "requested_package": "LQFP64",
            "expected_package_pins": 64,
            "source_path": "/corpus/bad.pdf",
            "toc": [[1, "Table 5. LQFP64 pin definition", 1]],
            "pages": [{"text": "def", "tables": [definition64]}],
        }
        cases.append(_run_locate(name, {**base, **request}))
    return cases


def kernel_cases() -> list[dict]:
    cases: list[dict] = []

    def add(kind: str, name: str, inputs: dict, expected) -> None:
        cases.append({"kind": kind, "name": name, "input": inputs, "expected": expected})

    for name, title in [
        ("definition", "Table 5. LQFP64 pin and ball definition"),
        ("fmc_other", "Table 3. FMC pin definition"),
        ("pin_defs", "Table 9. Pin definitions"),
        ("af_rejected", "Table 4. Alternate function mapping"),
        ("ballout", "Figure 2. LQFP64 ballout"),
        ("pinout_fig", "Figure 1. Pinout"),
        ("electrical", "NRST pin input characteristics"),
        ("outline", "Package information"),
        ("section_ti", "Pin configuration and functions"),
        ("section_nxp", "Pinning information"),
        ("nothing", "Overview of peripherals"),
    ]:
        add("classify_title", name, {"title": title}, classify_title(title))

    for name, value in [
        ("family_first", "LQFP64"),
        ("count_first", "64-pin LQFP"),
        ("smps", "QFN32 with SMPS"),
        ("qualified", "LQFP64 (PGE)"),
        ("none", "SOT-23"),
    ]:
        add("package_pin_count", name, {"value": value}, package_pin_count(value))

    for name, requested, headers, expected in [
        ("exact", "LQFP64", ["LQFP64", "LQFP48"], "LQFP64"),
        ("cousin_blocked", "LQFP64", ["LQFP48"], None),
        ("smps_match", "QFN32 with SMPS", ["QFN32 SMPS", "QFN32"], "QFN32 SMPS"),
        ("qualified_only", "LQFP64", ["LQFP64 (PGE)", "LQFP64 (YME)"], None),
    ]:
        add(
            "match_package_column",
            name,
            {"requested": requested, "headers": headers},
            match_package_column(requested, headers),
        )

    for name, rows in [
        ("definition", [["LQFP64", "Pin name"], ["", ""], ["1", "PA0"]]),
        ("legend", [["Abbreviation", "Definition"], ["Pin name", "x"]]),
        ("af_table", [["AF0", "Port A alternate"], ["PA0", "SPI"]]),
        ("no_headers", [["A", "B"], ["1", "2"]]),
    ]:
        add("package_headers", name, {"rows": rows}, package_headers(rows))

    pins = [{"pin_no": i, "name": f"P{i}"} for i in range(1, 65)]
    for name, value, package, count, error in [
        ("ok", pins, "LQFP64", 64, None),
        (
            "duplicate",
            [{"pin_no": 1, "name": "A"}, {"pin_no": 1, "name": "A"}],
            "QFN32",
            32,
            "physical pin records contain exact duplicates",
        ),
        (
            "lqfp_range",
            [{"pin_no": i, "name": f"P{i}"} for i in range(1, 63)],
            "LQFP64",
            64,
            "LQFP physical pins do not match the package range",
        ),
        (
            "lqfp_numeric",
            [{"pin_no": "A1", "name": "PA0"}],
            "LQFP64",
            64,
            "LQFP physical pin identifiers must be numeric",
        ),
        (
            "empty",
            [],
            "QFN32",
            32,
            "physical pins must be a non-empty list",
        ),
    ]:
        out: object = None
        try:
            validate_physical_pin_truth(
                value, package=package, expected_package_pins=count
            )
        except ValueError as exc:
            out = str(exc)
        add(
            "physical_truth",
            name,
            {"pins": value, "package": package, "expected_package_pins": count},
            {"error": error, "raised": out},
        )
    return cases


def main() -> int:
    out: dict = {"cases": []}
    out["cases"].extend(locate_cases())
    out["cases"].extend(kernel_cases())
    json.dump(out, sys.stdout, indent=1, sort_keys=True)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
