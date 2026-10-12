#!/usr/bin/env python3
"""Generate dw-recon::document_scope gold from the LIVE
`harness/electronics/document_scope.py` reader.

Each case is rendered into a real PDF (pymupdf) so the reference runs its
own code path end to end, then the row is captured with the volatile keys
(source_path, document_sha256) stripped. The front-page texts travel in the
gold so the Rust port replays identical input.

Cases cover the corpus traps the reader exists for: the GD32F4xx
filename-binding near-miss, ST composite series tokens, inverted phrasings,
the PM0214 class trap (cover says user manual, body cross-references a
reference manual), the no-text-layer instrument failure, and the
control-token gate.

Usage: python3 scripts/gold_document_scope.py > tests/fixtures/gold/document_scope_v1.json
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pymupdf  # noqa: E402
from harness.electronics.document_scope import read_scope  # noqa: E402

VOLATILE = {"source_path", "document_sha256"}

CASES = [
    (
        "gd32_filename_binding_near_miss",
        None,
        [
            "GD32F4xx User Manual\nGigaDevice Semiconductor\nRev 2.1\n"
            "For GD32F405xx, GD32F407xx and GD32F470xx ARM Cortex-M4 32-bit\n"
            "microcontrollers. This user manual describes the memory and peripherals."
        ],
    ),
    (
        "st_composite_series",
        None,
        [
            "STM32F405xx/407xx Datasheet production data\n"
            "It provides complete information on how to use the STM32F405xx/07xx,\n"
            "STM32F415xx/17xx, STM32F42xxx and STM32F43xxx microcontroller memory\n"
            "and peripherals."
        ],
    ),
    (
        "st_inverted_phrasing",
        None,
        [
            "RM0390 Reference manual\nSTM32F446xx advanced Arm-based 32-bit MCUs\n"
            "This manual describes how to use the memory and peripherals of\n"
            "STM32F446xx microcontrollers."
        ],
    ),
    (
        "prose_scope",
        None,
        [
            "Application Note AN1234\nAcme Devices Inc\n"
            "Applicable products: GD32F103xx, GD32F153xx.\n"
            "This application note describes board layout guidance."
        ],
    ),
    (
        "scope_absent",
        None,
        [
            "Quick Start Guide\nSome Vendor Inc\n"
            "This guide covers development kit setup for evaluation boards\n"
            "and shows how to load a first example program quickly."
        ],
    ),
    (
        "no_text_layer",
        None,
        ["tiny"],
    ),
    (
        "control_token_absent",
        "STM32",
        [
            "GD32F4xx User Manual\n"
            "For GD32F405xx and GD32F407xx microcontrollers. This user manual\n"
            "describes the memory and peripherals."
        ],
    ),
    (
        "pm0214_class_trap",
        None,
        [
            "User manual\nUM0421\n"
            "Reference manuals of STM32F3 Series describe the peripherals.\n"
            "For STM32F303xB and STM32F303xC microcontrollers."
        ],
    ),
    (
        "cpu_core_subject",
        None,
        [
            "PM0214 Programming manual\nSTM32 Cortex-M4 MCUs and MPUs programming manual\n"
            "This programming manual describes the instruction set core programming\n"
            "model for the Arm Cortex-M4 core."
        ],
    ),
    (
        "peripheral_subject",
        None,
        [
            "dsPIC33/PIC24 FRM\nI2C\nFramework Reference Manual\n"
            "This reference manual describes the I2C peripheral module."
        ],
    ),
]


def build_pdf(pages: list[str], path: Path) -> None:
    doc = pymupdf.open()
    for text in pages:
        page = doc.new_page(width=612, height=792)
        page.insert_textbox(pymupdf.Rect(72, 72, 540, 720), text, fontsize=11)
    doc.save(path)
    doc.close()


def main() -> int:
    out = {"cases": []}
    with tempfile.TemporaryDirectory() as td:
        for name, control, pages in CASES:
            pdf = Path(td) / f"{name}.pdf"
            build_pdf(pages, pdf)
            row = read_scope(pdf, control)
            row = {k: v for k, v in row.items() if k not in VOLATILE}
            out["cases"].append({"name": name, "pages": pages, "expected": row})
    json.dump(out, sys.stdout, indent=1, sort_keys=True)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
