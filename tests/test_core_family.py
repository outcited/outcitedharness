from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

from harness.electronics.core_family import (
    CORE_FAMILY_SCHEMA,
    collapse_core_family_rows,
    core_family_rows,
)

SCRIPT_ROOT = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))


def _pages(*texts: str) -> list[tuple[int, str]]:
    return [(index + 1, text) for index, text in enumerate(texts)]


def _families(rows):
    return {row["value"] for row in rows if row["field"] == "core_family"}


def _bits(rows):
    return {row["value"] for row in rows if row["field"] == "core_bits"}


@pytest.mark.parametrize(
    "printed,expected",
    [
        ("Arm Cortex-M0 core", "cortex-m0"),
        ("Arm Cortex-M0+ core", "cortex-m0+"),
        ("ARM Cortex-M3 CPU", "cortex-m3"),
        ("32-bit Arm Cortex-M4F MCU", "cortex-m4"),
        ("Arm Cortex-M7 processor", "cortex-m7"),
        ("Cortex-M23 security core", "cortex-m23"),
        ("Cortex-M33 with TrustZone", "cortex-m33"),
        ("Cortex-M55 with Helium", "cortex-m55"),
        ("Cortex-M85 processor", "cortex-m85"),
    ],
)
def test_cortex_families(printed, expected):
    rows = core_family_rows(_pages(printed))
    assert _families(rows) == {expected}


def test_cortex_fpu_suffix_is_not_a_family():
    rows = core_family_rows(_pages("Arm Cortex-M4FPU floating point"))
    assert _families(rows) == {"cortex-m4"}


def test_avr_pic_msp430_riscv_xtensa_classes():
    rows = core_family_rows(
        _pages(
            "8-bit AVR microcontroller with 32 registers",
            "PIC16F877A 8-bit microcontroller",
            "dsPIC33EP512MC806 digital signal controller",
            "MSP430FR600x Mixed-Signal Microcontrollers",
            "RISC-V RV32IMC core",
            "Xtensa LX6 dual-core processor",
        )
    )
    assert _families(rows) == {"avr", "pic", "msp430", "risc-v", "xtensa"}


def test_avr_core_bits_attached_when_adjacent():
    rows = core_family_rows(_pages("8-bit AVR microcontroller"))
    family = next(row for row in rows if row["field"] == "core_family")
    assert family["core_bits"] == 8
    assert _bits(rows) == set()


def test_cortex_bits_attached_before_or_after():
    rows = core_family_rows(_pages("32-bit ARM Cortex-M4F MCU"))
    family = next(row for row in rows if row["field"] == "core_family")
    assert family["core_bits"] == 32
    after = core_family_rows(_pages("Cortex-M4 core, 32-bit MCU"))
    assert next(row for row in after if row["field"] == "core_family")["core_bits"] == 32


def test_peripheral_widths_never_become_core_bits():
    rows = core_family_rows(
        _pages("Cortex-M0+ MCU with 16-bit timers and 12-bit ADC")
    )
    assert _families(rows) == {"cortex-m0+"}
    assert next(row for row in rows if row["field"] == "core_family")["core_bits"] is None
    assert _bits(rows) == set()


def test_bits_only_claim_needs_a_device_word():
    rows = core_family_rows(_pages("16-bit RISC CPU with 16 registers"))
    assert _families(rows) == set()
    assert _bits(rows) == {16}
    jitter = core_family_rows(_pages("32-bit and 16-bit cyclic redundancy check"))
    assert _bits(jitter) == set()
    multiplier = core_family_rows(_pages("32-bit hardware multiplier (MPY)"))
    assert _bits(multiplier) == set()


def test_benchmark_sentence_is_not_a_claim():
    rows = core_family_rows(
        _pages(
            "MSP430FR600x Ultrasonic Sensing MSP430 Microcontrollers",
            "Up to 40x faster than Arm Cortex-M0+ core",
        )
    )
    assert _families(rows) == {"msp430"}


def test_esp32_alone_never_invents_xtensa():
    rows = core_family_rows(_pages("ESP32-S3 Wi-Fi MCU with 8 MB flash"))
    assert _families(rows) == set()


def test_bare_core_names_stay_unknown():
    rows = core_family_rows(
        _pages(
            "Arm Cortex-M microcontroller family",
            "RISC processor with PICKit 4 programmer",
        )
    )
    assert rows == []


def test_msp432_is_not_msp430():
    rows = core_family_rows(_pages("MSP432P401R ARM Cortex-M4F MCU"))
    assert "msp430" not in _families(rows)


def test_feature_block_page_and_verbatim_are_exact():
    page = "3.1 Features\n• RISC-V RV32I core at 100 MHz"
    rows = core_family_rows(_pages("Title page", page))
    row = next(row for row in rows if row["field"] == "core_family")
    assert row["page_1based"] == 2
    assert row["evidence_source"] == "feature_block"
    assert row["verbatim"] in page
    assert row["span"] in row["verbatim"]


def test_page_1_wins_collapse():
    rows = core_family_rows(
        _pages("Cortex-M4 MCU", "Body text", "Feature list: Cortex-M4 core")
    )
    winner = collapse_core_family_rows(rows)
    assert winner["core_family:cortex-m4"]["page_1based"] == 1


def test_dual_core_keeps_both_families():
    rows = core_family_rows(_pages("Cortex-M4 + Cortex-M7 dual-core MCU"))
    assert _families(rows) == {"cortex-m4", "cortex-m7"}


def test_portfolio_boilerplate_never_emits_bits():
    assert core_family_rows(_pages("TI 16-bit and 32-bit microcontrollers")) == []
    assert core_family_rows(_pages("Texas Instruments 32-bit microcontrollers")) == []


def test_real_msp430_fr600x_fixture_keeps_its_own_family():
    pymupdf = pytest.importorskip("pymupdf")
    fixture = (
        Path(__file__).resolve().parent
        / "fixtures/gold/typical_curves/pdfs/msp430fr6005.pdf"
    )
    doc = pymupdf.open(fixture)
    try:
        pages = [(index + 1, doc[index].get_text()) for index in range(min(2, doc.page_count))]
    finally:
        doc.close()
    rows = core_family_rows(pages)
    assert _families(rows) == {"msp430"}
    assert 16 in _bits(rows)


def test_rows_carry_provenance():
    rows = core_family_rows(
        _pages("STM32H7 Cortex-M7 MCU"), sha256="a" * 64, part_number="STM32H7"
    )
    assert rows
    for row in rows:
        assert row["schema"] == CORE_FAMILY_SCHEMA
        assert row["method"] == "deterministic_core_family_v1"
        assert row["pdf_sha"] == "a" * 64
        assert row["part_number"] == "STM32H7"
        assert row["page_1based"] == 1
        assert row["verbatim"]
        assert row["span"]


def test_scan_pdf_emits_sha_page_and_verbatim(tmp_path):
    pymupdf = pytest.importorskip("pymupdf")
    from extract_core_family import _scan

    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "STM32H743 32-bit Arm Cortex-M7 MCU")
    path = tmp_path / "stm32h743.pdf"
    doc.save(path)
    doc.close()

    result = _scan({"part_number": "STM32H743", "source_path": str(path)}, str(tmp_path))
    assert result["error"] is None
    family = next(row for row in result["rows"] if row["field"] == "core_family")
    assert family["value"] == "cortex-m7"
    assert family["core_bits"] == 32
    assert family["page_1based"] == 1
    assert family["pdf_sha"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert "Cortex-M7" in family["verbatim"]
