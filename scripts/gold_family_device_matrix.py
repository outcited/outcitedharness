#!/usr/bin/env python3
"""Generate dw-recon::family_device_matrix gold from the LIVE
`harness/electronics/family_device_matrix.py` reference.

Every case is computed by the reference itself: document cases render a real
PDF and run `extract_family_matrix` end to end (with the live
`family_census.census_document` row as its input, exactly as the reference
consumes it), kernel cases call the pure readers the unit tests pin. The
inputs the Rust port consumes travel in the gold — table grids as
`extract()` returns them, cell boxes and page words for geometry binding —
so the port replays identical input. Volatile `_meta` keys are stripped.

Cases cover the traps the reader exists for: the strict label grammar
("GPIOs" is gpio_count not pin_count, "Tamper pins" is nothing, "Maximum CPU
frequency" is freq_mhz not core), the code-flash/data-flash and sram/backup
look-alike exclusions, composite rows ("SPI / I2S : 4/3" splits because the
document printed the pairing), header binding cases (newline-split tokens,
stem+code lists, wildcard joins through a printed device summary, ordering-
suffix variants stay unbound), merged cells flagged as merged, alternatives
staying unknown, qualified numbers keeping their printed words, the ST
memory-letter self-check, and bit-width group sums.

Usage: python3 scripts/gold_family_device_matrix.py > tests/fixtures/gold/family_device_matrix_v1.json
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pymupdf  # noqa: E402
from harness.electronics.family_census import census_document  # noqa: E402
from harness.electronics.family_device_matrix import (  # noqa: E402
    _cell_leaves,
    _header_token,
    _split_glued_runs,
    bind_columns,
    extract_family_matrix,
    parse_value,
    read_matrix_table,
    row_attributes,
    split_composite_label,
    split_composite_value,
    st_flash_code_kb,
)

VOLATILE_META = {"extracted_at", "document_sha256", "source_path"}


def build_pdf(pages: list[dict], path: Path) -> None:
    """Render one PDF: each page is {"text": ..., "table": [[...]] | None}."""
    doc = pymupdf.open()
    for spec in pages:
        page = doc.new_page(width=612, height=792)
        y = 60.0
        for line in spec.get("text", "").split("\n"):
            page.insert_text(pymupdf.Point(72, y), line, fontsize=11)
            y += 14
        table = spec.get("table")
        if table:
            cols = max(len(r) for r in table)
            width = 468.0 / cols
            top = 220.0
            height = 22.0
            xs = [72.0 + i * width for i in range(cols + 1)]
            ys = [top + r * height for r in range(len(table) + 1)]
            caption_rows = {
                r
                for r, row in enumerate(table)
                if len([v for v in row if v is not None]) == 1
            }
            for yy in ys:
                page.draw_line(pymupdf.Point(xs[0], yy), pymupdf.Point(xs[-1], yy), width=0.7)
            for x in xs:
                spans: list[tuple[float, float]] = []
                start = ys[0]
                for r in range(len(table)):
                    end = ys[r + 1]
                    if r not in caption_rows:
                        spans.append((start, end))
                        start = end
                if start < ys[-1]:
                    spans.append((start, ys[-1]))
                for (a, b) in spans:
                    page.draw_line(pymupdf.Point(x, a), pymupdf.Point(x, b), width=0.7)
            for r, row in enumerate(table):
                filled = [(c, v) for c, v in enumerate(row) if v is not None]
                if len(filled) == 1:
                    # A caption row spans the whole table in real PDFs; no
                    # verticals are drawn in its band so the extractor keeps
                    # the text in one cell.
                    c, val = filled[0]
                    # Keep the caption inside the first column's x-range:
                    # the extractor splits words across columns by geometry
                    # and strip_title_rows needs exactly one filled cell.
                    page.insert_text(
                        pymupdf.Point(xs[c] + 3, ys[r] + 13),
                        str(val),
                        fontsize=5,
                    )
                    continue
                for c, val in filled:
                    # insert_text never silently drops (insert_textbox does
                    # when the text does not fit its rect).
                    page.insert_text(
                        pymupdf.Point(xs[c] + 3, ys[r] + 15),
                        str(val),
                        fontsize=8,
                    )
    doc.save(path)
    doc.close()


def capture_inputs(path: Path, census_row: dict) -> dict:
    """The PDF layer's outputs: summary tables, matrix grids, words, boxes."""
    doc = pymupdf.open(path)
    summary_tables = []
    for pno in range(1, min(doc.page_count, 3) + 1):
        with contextlib.redirect_stdout(sys.stderr):
            found = doc[pno - 1].find_tables().tables
        for t in found:
            try:
                summary_tables.append({"page": pno, "rows": t.extract()})
            except Exception:  # noqa: BLE001
                pass
    matrices = []
    census_pages = sorted({t["page"] for t in census_row.get("device_tables") or []})
    for pno in census_pages:
        page = doc[pno - 1]
        words = [
            {"x0": w[0], "y0": w[1], "x1": w[2], "y1": w[3], "text": w[4]}
            for w in page.get_text("words")
        ]
        with contextlib.redirect_stdout(sys.stderr):
            found = page.find_tables().tables
        for t in found:
            try:
                rows = t.extract()
            except Exception:  # noqa: BLE001
                continue
            matrices.append(
                {
                    "page": pno,
                    "rows": rows,
                    "words": words,
                    "cells": [list(c) for c in (t.cells or [])],
                    "header_cells": [list(c) for c in (t.rows[0].cells if t.rows else [])],
                }
            )
    doc.close()
    return {"summary_tables": summary_tables, "matrices": matrices}


DOCUMENT_CASES = [
    (
        "stm32_family_matrix",
        {"vendor": "st"},
        [
            {
                "text": "DS8626 Rev 8\nSTM32F446xC/E Datasheet\nFor STM32F446xC/E microcontrollers.\n"
                "Device summary\n",
                "table": [
                    ["Reference", "Part number list"],
                    ["STM32F446xC", "STM32F446MC, STM32F446RC"],
                    ["STM32F446xE", "STM32F446ME, STM32F446RE, STM32F446VE"],
                ],
            },
            {
                "text": "2. Device overview\nDevice comparison table.\nAvailable in LQFP64 and LQFP100 packages.",
                "table": [
                    ["Peripherals", None, "STM32F446MC", "STM32F446ME", "STM32F446RC"],
                    ["Flash memory in Kbytes", None, "256", "512", "256"],
                    ["SRAM in Kbytes", "System", "128 (112+16)", None, None],
                    ["Timers", "General-purpose", "10", None, None],
                    ["Communication interfaces", "SPI / I2S", "4/3 (simplex)(2)", None, None],
                    ["GPIOs", None, "63", "63", "50"],
                    ["Operating voltage", None, "1.7 to 3.6 V", None, None],
                ],
            },
        ],
    ),
    (
        "renesas_product_list",
        {"vendor": "renesas"},
        [
            {
                "text": "R01DS0001EU0100\nRA2E2 Group Datasheet\nFor R7FA2E2A72DNK, R7FA2E2A93CFL and "
                "R7FA2E2A74CFL 32-bit microcontrollers.\nGeneral description\n"
                "The RA2E2 Group is a family of 32-bit microcontrollers.\n",
            },
            {
                "text": "Table 1.12 Product list (1 of 2)\n",
                "table": [
                    ["Table 1.12 Product list (1 of 2)", None, None, None, None],
                    ["Product part number", "Package code", "Code flash", "SRAM", "Operating temperature"],
                    ["R7FA2E2A72DNK", "PLQP0064KB-C", "128", "16", "-40 to +105C"],
                    ["R7FA2E2A93CFL", "PLQP0064GA-A", None, None, None],
                    ["R7FA2E2A74CFL", "PLQP0048KB-B", "64", None, "-40 to +85C"],
                ],
            },
        ],
    ),
    (
        "no_device_table",
        {"vendor": "acme"},
        [
            {
                "text": "ACME1000 Datasheet\nFor ACME1000 regulators.\nGeneral description\n"
                "The ACME1000 is a low-dropout regulator with 300 mA output and low noise.\n",
            },
            {
                "text": "Mode table. Not a device matrix.\n",
                "table": [
                    ["Mode", None, "Sleep", "Stop", "Standby"],
                    ["Flash", None, "on", "off", "off"],
                    ["SRAM", None, "on", "on", "off"],
                    ["Timers", None, "yes", "no", "no"],
                ],
            },
        ],
    ),
]


def kernel_cases() -> list[dict]:
    cases: list[dict] = []

    def add(kind: str, name: str, inputs: dict, expected) -> None:
        cases.append({"kind": kind, "name": name, **inputs, "expected": expected})

    def leaf(verbatim: str, attribute: str, unit) -> dict:
        return parse_value(verbatim, attribute, unit)

    add("parse_value", "int_footnote", {"verbatim": "256(1)", "attribute": "code_flash_kb", "label_unit": "KB"}, leaf("256(1)", "code_flash_kb", "KB"))
    add("parse_value", "breakdown", {"verbatim": "128 (112+16)", "attribute": "sram_kb", "label_unit": "KB"}, leaf("128 (112+16)", "sram_kb", "KB"))
    add("parse_value", "mbytes", {"verbatim": "1 M bytes", "attribute": "code_flash_kb", "label_unit": None}, leaf("1 M bytes", "code_flash_kb", None))
    add("parse_value", "m_short", {"verbatim": "1 M", "attribute": "code_flash_kb", "label_unit": None}, leaf("1 M", "code_flash_kb", None))
    add("parse_value", "bitwidth_sum", {"verbatim": "5 (16-bit) 2 (32-bit)", "attribute": "timer_general_purpose", "label_unit": None}, leaf("5 (16-bit) 2 (32-bit)", "timer_general_purpose", None))
    add("parse_value", "bitwidth_qualifier", {"verbatim": "1 (16-bit) high frequency", "attribute": "timer_advanced", "label_unit": None}, leaf("1 (16-bit) high frequency", "timer_advanced", None))
    add("parse_value", "explicit_none", {"verbatim": "-", "attribute": "spi_count", "label_unit": None}, leaf("-", "spi_count", None))
    add("parse_value", "presence", {"verbatim": "Yes", "attribute": "rtc", "label_unit": None}, leaf("Yes", "rtc", None))
    add("parse_value", "alternatives", {"verbatim": "16 (parity-protected) or 18 (not parity-protected)", "attribute": "sram_kb", "label_unit": "KB"}, leaf("16 (parity-protected) or 18 (not parity-protected)", "sram_kb", "KB"))
    add("parse_value", "range", {"verbatim": "1.8 to 3.6 V(4)", "attribute": "operating_voltage", "label_unit": None}, leaf("1.8 to 3.6 V(4)", "operating_voltage", None))
    add("parse_value", "temp_grades", {"verbatim": "Ambient: - 40 to 85 °C / -40 to 105 °C Junction: -40 to 125 °C", "attribute": "temp_range", "label_unit": None}, leaf("Ambient: - 40 to 85 °C / -40 to 105 °C Junction: -40 to 125 °C", "temp_range", None))
    add("parse_value", "junction_not_operating", {"verbatim": "Junction temperature: –40 to +130 °C", "attribute": "temp_range", "label_unit": None}, leaf("Junction temperature: –40 to +130 °C", "temp_range", None))
    add("parse_value", "boolean_note", {"verbatim": "Yes (6-Endpoints)", "attribute": "usb", "label_unit": None}, leaf("Yes (6-Endpoints)", "usb", None))
    add("parse_value", "boolean_no", {"verbatim": "No", "attribute": "usb", "label_unit": None}, leaf("No", "usb", None))
    add("parse_value", "instance_count", {"verbatim": "2", "attribute": "usb", "label_unit": None}, leaf("2", "usb", None))
    add("parse_value", "times_kinds", {"verbatim": "2×FD (0-1)", "attribute": "can_count", "label_unit": None}, leaf("2×FD (0-1)", "can_count", None))
    add("parse_value", "dc_to_max", {"verbatim": "DC – 40 MHz", "attribute": "freq_mhz", "label_unit": None}, leaf("DC – 40 MHz", "freq_mhz", None))
    add("parse_value", "qualified_words", {"verbatim": "Up to 2 Mbytes", "attribute": "code_flash_kb", "label_unit": None}, leaf("Up to 2 Mbytes", "code_flash_kb", None))
    add("parse_value", "qualified_short", {"verbatim": "Max. 4", "attribute": "spi_count", "label_unit": None}, leaf("Max. 4", "spi_count", None))
    add("parse_value", "no_qualifier", {"verbatim": "81", "attribute": "gpio_count", "label_unit": None}, leaf("81", "gpio_count", None))

    for name, group, sub, expected in [
        ("gpio", "GPIOs", "", [("gpio_count", None)]),
        ("tamper_nothing", "Tamper pins", "", []),
        ("gpio_tolerant", "GPIOs", "Normal I/Os (TC, TTa)", []),
        ("freq", "Maximum CPU frequency", "", [("freq_mhz", None)]),
        ("fmc_excluded", "FMC", "NOR flash memory/RAM controller", []),
        ("sram_axi", "SRAM in Kbytes", "SRAM mapped onto AXI bus", []),
        ("composite", "Communication interfaces", "SPI / I2S", [("spi_count", 0), ("i2s_count", 1)]),
        ("composite_slash", "Comm. interfaces", "USART/ UART", [("usart_count", 0), ("uart_count", 1)]),
        ("adc_channels", "12-bit ADC", "Number of channels", [("adc_channels", None)]),
        ("timer_advanced", "Timers", "Advanced-control (PWM)", [("timer_advanced", None)]),
        ("general_io_not_timer", "General-purpose input/outputs", "", [("gpio_count", None)]),
        ("usb_vdd", "Communication interfaces", "USB/(VDD USB)", [("usb", 0), ("operating_voltage", 1)]),
        ("sdadc_voltage", "16-bit SDADC operating voltage", "", [("adc_count", None)]),
        ("sram_instruction", "SRAM in Kbytes", "Instruction", [("sram_kb", None)]),
        ("sram_system", "SRAM in Kbytes", "System", [("sram_kb", None)]),
        ("shared_module", "eUSCI B: _ SPI, I2C", "", [("spi_count", None), ("i2c_count", None)]),
        ("shared_module_three", "eUSCI A: _ UART, IrDA, SPI", "", [("uart_count", None), ("spi_count", None)]),
        ("composite_slash_nospace", "Communication interfaces", "SPI/I2S", [("spi_count", 0), ("i2s_count", 1)]),
        ("temperature_sensor", "Temperature sensor", "", []),
        ("tamper_package", "Tamper pins (legacy/SMPS package)", "", []),
    ]:
        add(
            "row_attributes",
            name,
            {"group_label": group, "sub_label": sub},
            [[a, i] for a, i in row_attributes(group, sub)],
        )
        assert expected is not None

    for name, text, expected in [
        ("io_pins", "I/O pins", ["I/O pins"]),
        ("da_converters", "Number of 12-bit D/A converters", ["Number of 12-bit D/A converters"]),
        ("spi_i2s", "SPI / I2S", ["SPI", "I2S"]),
        ("usart_uart", "USART/ UART", ["USART", "UART"]),
        ("io_select", "I/O Pins(1)/ Peripheral Pin Select", ["I/O Pins(1)", "Peripheral Pin Select"]),
    ]:
        add("split_composite_label", name, {"sub_label": text}, split_composite_label(text))

    add("split_composite_value", "simplex", {"text": "4/3 (simplex)", "count": 2}, split_composite_value("4/3 (simplex)", 2))
    add("split_composite_value", "bracket", {"text": "2 [1]", "count": 2}, split_composite_value("2 [1]", 2))
    add("split_composite_value", "instance_lists", {"text": "3/2 (0-2)/(1-2)", "count": 2}, split_composite_value("3/2 (0-2)/(1-2)", 2))
    add("split_composite_value", "mismatch", {"text": "4/3 (simplex)", "count": 3}, split_composite_value("4/3 (simplex)", 3))

    add("header_token", "newline_join", {"cell": "STM32\nF446MC"}, _header_token("STM32\nF446MC"))
    add("header_token", "list_share_prefix", {"cell": "AVR64DB64\nAVR128DB64"}, _header_token("AVR64DB64\nAVR128DB64"))

    add("split_glued_runs", "two_values", {"values": [["2 2", False], ["", True]]}, [list(v) for v in _split_glued_runs([("2 2", False), ("", True)])])
    add("split_glued_runs", "mismatch", {"values": [["1 10", False]]}, [list(v) for v in _split_glued_runs([("1 10", False)])])
    add("split_glued_runs", "three_tokens_two_run", {"values": [["3 2 1", False], ["", True]]}, [list(v) for v in _split_glued_runs([("3 2 1", False), ("", True)])])

    for part, expected in [
        ("STM32F446MC", 256),
        ("STM32G0B1RE", 512),
        ("STM32F410RBI", 128),
        ("STM32WB15CC", None),
    ]:
        add("st_flash_code_kb", f"st_{part}", {"part_number": part}, st_flash_code_kb(part))

    bind_cases = [
        (
            "newline_tokens",
            [["Peripherals", "", "STM32 F446MC", "STM32 F446ME"], ["Flash", "", "256", "512"]],
            2, {}, None,
        ),
        (
            "code_list",
            [["Peripheral", "", "STM32G050 _ F6 K6 K8 C6 C8", "", "", "", ""], ["Flash", "", "32", "32", "64", "32", "64"]],
            2, {}, None,
        ),
        (
            "wildcard_join",
            [["Peripheral", "", "STM32F101Tx", "", "STM32F101Cx", ""], ["Flash - Kbytes", "", "64", "128", "64", "128"]],
            2,
            {"STM32F101x8": ["STM32F101T8", "STM32F101C8"], "STM32F101xB": ["STM32F101TB", "STM32F101CB"]},
            None,
        ),
        (
            "wildcard_unbound",
            [["Peripheral", "", "STM32F101Tx", ""], ["Flash", "", "64", "128"]],
            2, {}, None,
        ),
        (
            "header_list",
            [["Peripheral", "", "STM32L552CE,STM32L552CC/STM32L552CExxP", "STM32L552RE"], ["Flash", "", "512", "512"]],
            2, {}, None,
        ),
        (
            "heading_not_stem",
            [["Part Number", "IG", "IK", "VE"], ["Flash (KB)", "1024", "3072", "512"], ["SRAM (KB)", "256", "256", "128"]],
            1, {}, None,
        ),
        (
            "comma_list_parts",
            [["Feature", "ATmega1608,ATmega1609", "ATmega3208,ATmega3209"], ["Flash (KB)", "16", "32"], ["SRAM (KB)", "2", "4"]],
            1, {}, None,
        ),
        (
            "package_letter",
            [
                ["Peripherals", "", "STM32U3C5CI", "STM32U3C5VIYxxxx", "STM32U3C5VITxxxx"],
                ["Flash memory (Mbytes)", "", "2", "2", "2"],
                ["SRAM in Kbytes", "", "640", "640", "640"],
                ["Max. CPU frequency", "", "96 MHz", "96 MHz", "96 MHz"],
            ],
            2, {}, None,
        ),
    ]
    for name, grid, label_cols, summary, parts in bind_cases:
        rows, bindings = bind_columns(grid, label_cols, summary, parts)
        add(
            "bind_columns",
            name,
            {"grid": grid, "label_cols": label_cols, "device_summary": summary, "parts_named": parts},
            [rows, bindings],
        )

    matrix_cases = [
        (
            "shared_vs_variant",
            [
                ["Peripherals", None, "STM32F446MC", "STM32F446ME", "STM32F446RC"],
                ["Flash memory in Kbytes", None, "256", "512", "256"],
                ["SRAM in Kbytes", "System", "128 (112+16)", None, None],
                ["Timers", "General-purpose", "10", None, None],
                ["Communication interfaces", "SPI / I2S", "4/3 (simplex)(2)", None, None],
                ["GPIOs", None, "63", "63", "50"],
            ],
            14, {}, None,
        ),
        (
            "non_part_header",
            [
                ["Mode", None, "Sleep", "Stop", "Standby"],
                ["Flash", None, "on", "off", "off"],
                ["SRAM", None, "on", "on", "off"],
                ["Timers", None, "yes", "no", "no"],
            ],
            3, {}, None,
        ),
        (
            "product_list_merged",
            [
                ["Table 1.12 Product list (1 of 2)", None, None, None, None],
                ["Product part number", "Package code", "Code flash", "SRAM", "Operating\ntemperature"],
                ["R7FA2E1A93CFM", "PLQP0064KB-C", "128", "16", "-40 to +105°C"],
                ["R7FA2E1A93CFK", "PLQP0064GA-A", None, None, None],
                ["R7FA2E1A72DFL", "PLQP0048KB-B", "64", None, "-40 to +85°C"],
            ],
            8, {}, None,
        ),
        (
            "repeated_rows_sum",
            [
                ["Peripherals", None, "STM32F446MC"],
                ["Timers", "General purpose (32-bit)", "2"],
                ["Timers", "General purpose (16-bit)", "8"],
                ["GPIOs", None, "50"],
                ["SRAM in Kbytes", None, "128"],
            ],
            5, {}, None,
        ),
        (
            "conflicting_rows",
            [
                ["Peripherals", None, "STM32F446MC", "STM32F446ME"],
                ["Operating voltage", None, "1.7", "1.7"],
                ["Operating voltage", "USB", "3.6", "3.6"],
                ["GPIOs", None, "50", "50"],
                ["SRAM in Kbytes", None, "128", "256"],
            ],
            6, {}, None,
        ),
    ]
    for name, rows, page, summary, parts in matrix_cases:
        add(
            "read_matrix_table",
            name,
            {"rows": rows, "page": page, "device_summary": summary, "parts_named": parts},
            read_matrix_table(rows, page=page, device_summary=summary, parts_named=parts),
        )

    # _cell_leaves: composite rows.
    leaves = _cell_leaves("1/1", [("can_count", 0), ("can_count", 1)], None)
    add(
        "cell_leaves",
        "composite_sum",
        {
            "verbatim": "1/1",
            "attributes": [["can_count", 0], ["can_count", 1]],
            "label_unit": None,
            "label_components": None,
        },
        [[a, leaf_d] for a, leaf_d in leaves],
    )
    attrs = row_attributes("", "I/O Pins(1)/ Peripheral Pin Select")
    leaves = _cell_leaves("12/Y", attrs, None, 2)
    add(
        "cell_leaves",
        "io_pins_split",
        {
            "verbatim": "12/Y",
            "attributes": [[a, i] for a, i in attrs],
            "label_unit": None,
            "label_components": 2,
        },
        [[a, leaf_d] for a, leaf_d in leaves],
    )
    attrs = row_attributes("", "FLASH / SRAM (KB)")
    leaves = _cell_leaves("128 / 32", attrs, "KB")
    add(
        "cell_leaves",
        "header_split",
        {
            "verbatim": "128 / 32",
            "attributes": [[a, i] for a, i in attrs],
            "label_unit": "KB",
            "label_components": None,
        },
        [[a, leaf_d] for a, leaf_d in leaves],
    )
    return cases


def main() -> int:
    out: dict = {"cases": []}
    with tempfile.TemporaryDirectory() as td:
        for name, params, page_specs in DOCUMENT_CASES:
            pdf = Path(td) / f"{name}.pdf"
            build_pdf(page_specs, pdf)
            with contextlib.redirect_stdout(sys.stderr):
                census_row = census_document(pdf, vendor=params.get("vendor"), category="mcu")
                record = extract_family_matrix(pdf, census_row)
            meta = record.get("_meta") or {}
            record["_meta"] = {k: v for k, v in meta.items() if k not in VOLATILE_META}
            inp = capture_inputs(pdf, census_row)
            census_row = {
                k: v for k, v in census_row.items() if k not in VOLATILE_META
            }
            out["cases"].append(
                {
                    "kind": "census",
                    "name": name,
                    "document_sha256": hashlib.sha256(pdf.read_bytes()).hexdigest(),
                    "census_row": census_row,
                    "input": inp,
                    "expected": record,
                }
            )
    out["cases"].extend(kernel_cases())
    json.dump(out, sys.stdout, indent=1, sort_keys=True)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
