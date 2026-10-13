#!/usr/bin/env python3
"""Generate dw-recon::family_document gold from the LIVE
`harness/electronics/family_document.py` reference.

Every case is computed by the reference itself: document cases render a real
PDF and run `read_family_document` end to end, kernel cases call the pure
readers the unit tests pin. The inputs the Rust port consumes travel in the
gold — page texts as `get_text()` returns them, table grids as
`find_tables().extract()` returns them, bookmarks, the PDF title, the file
stem — so the port replays identical input. Volatile keys are stripped from
`_meta` (`extracted_at`, `document_sha256`, `source_path`).

Cases cover the traps the reader exists for: the chapter-title vocabulary
with its look-alike exclusions (DCC is not comparator, Trip-Zone is not
TrustZone, Message RAM is not sram), instance names counted only with page
support, the Renesas cover hierarchy (■ sections over \\u{f0b7} bullets over
dashes), single-glyph bullet blocks, the count split ("SCI x 4"), the
code-flash/data-flash... no wait, that's family_census — here: the
description-paragraph fact reader, the RZ/G includes card, the Item/
Description spec list, prose facts that collapse core spellings and pick the
stated supply over the widest, and GPIO paired with FC-BGA rather than the
ballout figure.

Usage: python3 scripts/gold_family_document.py > tests/fixtures/gold/family_document_v1.json
"""
from __future__ import annotations

import contextlib
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pymupdf  # noqa: E402
from harness.electronics.family_document import (  # noqa: E402
    _bullet_items,
    _bullets,
    _feature,
    classify_chapter_title,
    read_chapter_features,
    read_description_facts,
    read_family_document,
    read_includes_list,
    read_instances,
    read_io_by_package,
    read_item_description_specs,
    read_prose_facts,
    split_count,
)

VOLATILE_META = {"extracted_at", "document_sha256", "source_path"}


def build_pdf(pages: list[dict], path: Path, toc: list[list], title: str | None) -> None:
    """Render one PDF: each page is {"text": ..., "table": [[...]] | None}."""
    doc = pymupdf.open()
    for spec in pages:
        page = doc.new_page(width=612, height=792)
        # insert_textbox silently drops text that overflows its rect; one
        # insert_text per line guarantees the page carries every line.
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
            for y in ys:
                page.draw_line(pymupdf.Point(xs[0], y), pymupdf.Point(xs[-1], y), width=0.7)
            for x in xs:
                page.draw_line(pymupdf.Point(x, ys[0]), pymupdf.Point(x, ys[-1]), width=0.7)
            for r, row in enumerate(table):
                for c, val in enumerate(row):
                    if val is None:
                        continue
                    page.insert_textbox(
                        pymupdf.Rect(xs[c] + 3, ys[r] + 5, xs[c + 1] - 2, ys[r + 1] - 2),
                        str(val),
                        fontsize=8,
                    )
    if toc:
        doc.set_toc(toc)
    if title:
        doc.set_metadata({"title": title})
    doc.save(path)
    doc.close()


def pdf_inputs(path: Path) -> dict:
    """The PDF layer's outputs: page texts, table grids, bookmarks, title, stem."""
    doc = pymupdf.open(path)
    texts = {i + 1: p.get_text() for i, p in enumerate(doc)}
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
    toc = doc.get_toc() or []
    meta = doc.metadata or {}
    page_count = doc.page_count
    doc.close()
    return {
        "pages": texts,
        "tables": tables,
        "toc": [[lvl, title, page] for lvl, title, page in toc],
        "pdf_title": meta.get("title") or None,
        "source_stem": Path(path).stem,
        "page_count": page_count,
    }


CENSUS_CASES = [
    (
        "renesas_reference_manual",
        {
            "vendor": "renesas",
            "max_text_pages": None,
            "title": "RA4C1 Group Datasheet",
            "toc": [
                [1, "1. Overview", 1],
                [1, "2. Memory", 1],
                [2, "2.1 Flash", 1],
                [1, "30 Universal synchronous/asynchronous receiver transmitter (USART/UART)", 2],
                [1, "31 Inter-integrated circuit (I2C)", 2],
                [1, "32 Serial peripheral interface (SPI)", 2],
            ],
        },
        [
            {
                "text": "R01DS0001EU0100\nRA4C1 Group Datasheet\nRev. 1.00\n"
                "1. Overview\nThe RA4C1 Group is a family of 32-bit microcontrollers.\n"
                "Memory\n\uf0b7 512-KB code flash memory\n\uf0b7 96-KB SRAM\n"
                "Connectivity\n\uf0b7 Serial Communications Interface (SCI) x 4\n"
                "- UART\n- Simple SPI\n\uf0b7 I2C bus interface (IIC) x 2\n",
            },
            {
                "text": "1.1 Features\nMain features\n"
                "\u25a0 Arm Cortex-M33 core running at 48 MHz\n"
                "\u25a0 Up to 2 Mbytes of Flash memory with ECC\n"
                "- 640 Kbytes of SRAM\n",
            },
        ],
        [],
    ),
    (
        "st_datasheet_with_device_table",
        {"vendor": "st", "max_text_pages": None, "title": "STM32C011 Datasheet"},
        [
            {
                "text": "DS13543 Rev 3\nSTM32C011x4/x6 Datasheet\n"
                "For STM32C011x4/x6 microcontrollers.\n"
                "Contents\n3 CPU .......... 430\n4 Memories .......... 512\n",
            },
            {
                "text": "Features\n• Up to 32-Kbyte Flash memory\n• 12-Kbyte SRAM\n"
                "USART1, USART2 and SPI1 peripherals with DMA1.\n"
                "Operating voltage 2.0 to 3.6 V -40 to +85 C temperature range\n",
            },
            {
                "text": "Device summary. Available in LQFP48 and SOIC-8 packages.",
                "table": [
                    ["Feature", "Description"],
                    ["Communication Interfaces", ""],
                    ["I2C", "Up to 1 Mbit/s I2C interface"],
                    ["Timers", "16-bit general purpose timers"],
                    ["Operating Range (Ambient)", "2.0 to 3.6 V operating voltage"],
                ],
            },
        ],
        [],
    ),
    (
        "ti_features_table",
        {"vendor": "ti", "max_text_pages": None, "title": "TM4C123 Datasheet"},
        [
            {
                "text": "SLMS715\nTM4C123GH6PM Microcontroller\n"
                "Product Overview\nThe TM4C123GH6PM is a 32-bit microcontroller.\n"
                "Core: ARM Cortex-M4F with FPU running up to 80 MHz.\n",
            },
            {
                "text": "Table 2-1. TM4C123GH6PM Microcontroller Features\n",
                "table": [
                    ["Feature", "Description"],
                    ["Communication Interfaces", ""],
                    ["UART", "Eight UARTs with IrDA support"],
                    ["Analog Support", ""],
                    ["ADC", "Two 12-bit ADC modules"],
                ],
            },
        ],
        [],
    ),
    (
        "renesas_item_spec_list",
        {"vendor": "renesas", "max_text_pages": None, "title": "RZ/G1H"},
        [
            {
                "text": "Contents\n1.3 List of Specifications ............................. 1-3\n",
            },
            {
                "text": "1.3 List of Specifications\n1.3.1 ARM Core\n"
                "Item\nDescription\n"
                "System CPU Cortex-A15\n- ARM Cortex-A15 Quad MPCore 1.4 GHz\n"
                "- L1 I/D cache 32/32 KBytes, L2 cache 2 MBytes\n"
                "System CPU Cortex-A7\n- ARM Cortex-A7 Quad MPCore 780 MHz\n",
            },
        ],
        [],
    ),
    (
        "no_text_layer",
        {"vendor": "acme", "max_text_pages": None, "title": None},
        [{"text": "tiny"}],
        [],
    ),
]


def kernel_cases() -> list[dict]:
    cases: list[dict] = []

    def add(kind: str, name: str, inputs: dict, expected) -> None:
        cases.append({"kind": kind, "name": name, **inputs, "expected": expected})

    for title in (
        "30 Universal synchronous/asynchronous receiver transmitter (USART/UART)",
        "6 NVM (Flash)",
        "31 CAN-FD",
        "11 TinyEngineTM NPU",
        "9 Dual-Clock Comparator (DCC) Module",
        "22.10 Trip-Zone (TZ) Submodule",
        "25.14 Message RAM",
        "3.9 32-Bit CPU Timers 0/1/2",
    ):
        add(
            "classify_chapter_title",
            f"chapter_{title[:24]}",
            {"title": title},
            classify_chapter_title(title),
        )

    add(
        "bullets",
        "wrapped_lines_stop_at_heading",
        {
            "text": "• Full-duplex synchronous transfers on three lines\n• 4-bit to 16-bit data size\nselection\n• Up to 8 slaves\n30.4 SPI implementation\n• not part of features\n"
        },
        _bullets("• Full-duplex synchronous transfers on three lines\n• 4-bit to 16-bit data size\nselection\n• Up to 8 slaves\n30.4 SPI implementation\n• not part of features\n"),
    )
    add(
        "bullet_items",
        "single_glyph_no_header_level",
        {"text": "• Up to 2 Mbytes of Flash\n• 640 Kbytes of SRAM\n"},
        _bullet_items("• Up to 2 Mbytes of Flash\n• 640 Kbytes of SRAM\n"),
    )
    renesas_text = (
        "Features\n■ Memory\n\uf0b7512-KB code flash memory\n\uf0b796-KB SRAM\n■ Connectivity\n"
        "\uf0b7Serial Communications Interface (SCI) × 4\n- UART\n- Simple SPI\n\uf0b7I2C bus interface (IIC) × 2\n"
    )
    add("bullet_items", "renesas_cover_hierarchy", {"text": renesas_text}, _bullet_items(renesas_text))

    for text in (
        "Serial Communications Interface (SCI) × 4",
        "8-bit D/A Converter (DAC8) ×2 (for ACMPLP)",
        "DMA x8",
        "Temperature Sensor",
    ):
        add("split_count", f"count_{text[:20]}", {"text": text}, list(split_count(text)))

    add(
        "feature",
        "typed_numbers_and_qualifier",
        {"text": "Up to 2 Mbytes of Flash memory with ECC", "page": 1, "section": None, "level": None, "parent": None},
        _feature("Up to 2 Mbytes of Flash memory with ECC", 1),
    )
    add(
        "feature",
        "bare_numbers",
        {"text": "640 Kbytes of SRAM", "page": 1, "section": None, "level": None, "parent": None},
        _feature("640 Kbytes of SRAM", 1),
    )

    instances_pages = {
        1: "SPI1 SPI2 SPI3 TIM1 TIM2 USART1 GPIOA GPIOB FDCAN1 USB_OTG_HS",
        2: "SPI1, SPI2 and SPI3 support I2S. TIM1 TIM2 TIM15. USART1 GPIOA GPIOB FDCAN1",
        3: "SPI6 is quoted once here only. TIM17 too.",
    }
    add("read_instances", "multi_page_names_only", {"pages": instances_pages, "weak_pages": 2}, read_instances(instances_pages, weak_pages=2))

    chapters = {
        "all_entries": [
            {"level": 1, "title": "30 Serial peripheral interface (SPI)", "page": 100},
            {"level": 1, "title": "31 Inter-integrated circuit (I2C)", "page": 140},
        ]
    }
    chapter_pages = {
        101: "30.1 Introduction\ntext\n30.3 SPI main features\n• Full-duplex synchronous transfers\n• Up to 8 slaves\n30.4 SPI implementation\n",
        141: "Refer to the data sheet for device-specific features\n• stray bullet\n",
    }
    add(
        "read_chapter_features",
        "section_and_enclosing_chapter",
        {"pages": chapter_pages, "chapters": chapters, "page_count": 200},
        read_chapter_features(chapter_pages, chapters, 200),
    )

    prose_pages = {
        1: "Arm® Cortex®-M4 with FPU running up to 168 MHz\nCortex®-M4\n",
        2: "ARM Cortex-M4 core. Operating voltage VDD: 1.8 to 3.6 V. ADC range 2.4 to 3.6 V (not a supply line).",
        3: "The supply voltage range is 2.7 to 3.6 V for the USB block. -40 to +105 °C\n",
    }
    add("read_prose_facts", "core_freq_supply_temp", {"pages": prose_pages, "max_pages": 160}, read_prose_facts(prose_pages))

    paragraph = (
        "1. \nGeneral description \n"
        "The GD32F403xx device belongs to the performance line of GD32 MCU Family. It is a new 32-bit general-purpose "
        "microcontroller based on the Arm® Cortex®-M4 RISC core with best cost-performance ratio in terms of enhanced "
        "processing capacity. The GD32F403xx device incorporates the Arm® Cortex®-M4 32-bit processor core operating at "
        "168 MHz frequency with Flash accesses zero wait states to obtain maximum efficiency. It provides up to 3072 KB "
        "on-chip Flash memory and 128 KB SRAM memory. The devices offer up to three 12-bit 2.6M MSPS ADCs, two 12-bit DACs, "
        "up to eight general-purpose 16-bit timers, as well as standard and advanced communication interfaces: up to three "
        "SPIs, two I2Cs, two CANs, a SDIO, and an USBFS. The device operates from a 2.6 to 3.6 V power supply and available "
        "in –40 to +85 °C temperature range. Each TC can be configured to perform frequency generation. The above features "
        "make GD32F403xx devices suitable for a wide range of applications."
    )
    add("read_description_facts", "gd32_description", {"pages": {1: paragraph}, "max_pages": 14}, read_description_facts({1: paragraph}))

    rzg_pages = {
        9: (
            "1.1 Introduction\n"
            "The RZ/G1H includes:\n"
            "•\n"
            "Four 1.4-GHz ARM Cortex®-A15 MPCore® cores,\n"
            "•\n"
            "Four 780-MHz ARM Cortex®-A7 MPCore® cores,\n"
            "•\n"
            "3 channels Display Output,\n"
            "1.2 System Configuration Diagram\n"
        )
    }
    add("read_includes_list", "rzg_product_card", {"pages": rzg_pages, "max_pages": 20}, read_includes_list(rzg_pages))

    spec_pages = {
        7: (
            "Contents\n"
            "1.3 List of Specifications ......................................................... 1-3\n"
            "1.4 Power Supply Voltages and Temperature Range ............. 1-24\n"
        ),
        11: (
            "1.3 List of Specifications\n"
            "1.3.1 ARM Core\n"
            "Item\n"
            "Description\n"
            "System CPU Cortex-A15\n"
            "• ARM Cortex-A15 Quad MPCore 1.4 GHz\n"
            "• L1 I/D cache 32/32 KBytes, L2 cache 2 MBytes\n"
            "System CPU Cortex-A7\n"
            "• ARM Cortex-A7 Quad MPCore 780 MHz\n"
            "• L1 I/D cache 32/32 KBytes, L2 cache 512 KBytes\n"
        ),
        12: (
            "Item\n"
            "Description\n"
            "General-purpose I/O (GPIO)\n"
            "• General-purpose I/O ports: 188 ports\n"
            "• Supports GPIO interrupts.\n"
        ),
        31: (
            "Item\n"
            "Description\n"
            "Process\n"
            "28-nm Si-CMOS\n"
            "Package\n"
            "FC-BGA2727-831\n"
        ),
        32: "1.4 Power Supply Voltages and Temperature Range\n• Temperature range\n",
    }
    add("read_item_description_specs", "cores_cache_gpio", {"pages": spec_pages, "max_pages": 40}, read_item_description_specs(spec_pages))

    gpio_pages = {
        12: "General-purpose I/O (GPIO)\n• General-purpose I/O ports: 188 ports\n",
        31: "Package\nFC-BGA2727-831\n",
        35: (
            "3.1 Top View (Left)\n"
            "A1 A2 A3 B1 B2 B3\n"
            "VSS VDD GPIO0 GPIO1\n"
        ),
    }
    add("read_io_by_package", "stated_gpio_fcbga", {"pages": gpio_pages}, read_io_by_package(gpio_pages))
    return cases


def main() -> int:
    out: dict = {"cases": []}
    with tempfile.TemporaryDirectory() as td:
        for name, params, page_specs, toc in CENSUS_CASES:
            pdf = Path(td) / f"{name}.pdf"
            build_pdf(page_specs, pdf, params.get("toc") or toc, params.get("title"))
            with contextlib.redirect_stdout(sys.stderr):
                row = read_family_document(
                    pdf,
                    vendor=params.get("vendor"),
                    max_text_pages=params.get("max_text_pages"),
                )
            meta = row.get("_meta") or {}
            row["_meta"] = {k: v for k, v in meta.items() if k not in VOLATILE_META}
            inp = pdf_inputs(pdf)
            out["cases"].append(
                {
                    "kind": "census",
                    "name": name,
                    "vendor": params.get("vendor"),
                    "max_text_pages": params.get("max_text_pages"),
                    "input": inp,
                    "expected": row,
                }
            )
    out["cases"].extend(kernel_cases())
    json.dump(out, sys.stdout, indent=1, sort_keys=True)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
