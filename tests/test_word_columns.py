from __future__ import annotations

from pathlib import Path

import pytest

from harness.electronics.word_columns import (
    extract_pin_columns,
    package_pin_bogey,
)


STM32H745 = Path(
    "/Volumes/M5_4TB/DigiKey_Reference_Designs/pdf_cache/st_stm32h745xg.pdf"
)
EFR32BG22 = Path(
    "/Volumes/M5_4TB/DigiKey_Reference_Designs/pdf_cache/"
    "silabs_efr32bg22-datasheet.pdf"
)

pytestmark = pytest.mark.skipif(
    not STM32H745.exists() or not EFR32BG22.exists(),
    reason="datasheet PDF cache volume is not mounted",
)


def _rows(columns: dict, key: str) -> list[tuple[str, str]]:
    return [(row.pin_no, row.name) for row in columns[key].rows]


def test_stm32_five_package_columns_are_exact() -> None:
    columns = extract_pin_columns(STM32H745, 60)
    numeric_truth = [
        ("1", "PE2"),
        ("2", "PE3"),
        ("3", "PE4"),
        ("4", "PE5"),
        ("5", "PE6"),
        ("6", "VSS"),
        ("7", "VDD"),
        ("8", "VBAT"),
    ]
    assert _rows(columns, "LQFP144@74") == numeric_truth
    assert _rows(columns, "LQFP176@130") == numeric_truth
    assert _rows(columns, "LQFP208@159") == numeric_truth
    assert _rows(columns, "UFBGA176+25@102") == [
        ("C3", "PE2"),
        ("B2", "PE3"),
        ("B1", "PE4"),
        ("D3", "PE5"),
        ("E3", "PE6"),
        ("A1", "VSS"),
        ("D5", "VDD"),
        ("E2", "VBAT"),
        ("A15", "VSS"),
    ]
    assert _rows(columns, "TFBGA240+25@188") == [
        ("C3", "PE2"),
        ("D3", "PE3"),
        ("D2", "PE4"),
        ("D1", "PE5"),
        ("E5", "PE6"),
        ("A1", "VSS"),
        ("B1", "VBAT"),
        ("B2", "VSS"),
    ]


def test_stm32_claims_carry_source_coordinates() -> None:
    columns = extract_pin_columns(STM32H745, 60)
    claim = columns["LQFP176@130"].rows[0]
    assert claim.pin_no == "1"
    assert claim.name == "PE2"
    assert claim.identifier_span.text == "1"
    assert claim.name_span.text == "PE2"
    assert claim.identifier_span.bbox != claim.name_span.bbox
    header = columns["LQFP176@130"].header_span
    assert header.text == "LQFP176"
    assert abs(claim.identifier_span.bbox[0] - header.bbox[0]) < 20.0


def test_silabs_two_group_table_merges_to_truth() -> None:
    columns = extract_pin_columns(EFR32BG22, 81)
    merged = sorted(
        _rows(columns, "Pin(s)@106") + _rows(columns, "Pin(s)@378"),
        key=lambda row: (int(row[0]) if row[0].isdigit() else 10_000, row[0]),
    )
    assert merged == [
        ("1", "PC00"),
        ("2", "PC01"),
        ("3", "PC02"),
        ("4", "PC03"),
        ("5", "PC04"),
        ("6", "PC05"),
        ("7", "HFXTAL_I"),
        ("8", "HFXTAL_O"),
        ("9", "RESETn"),
        ("10", "RFVDD"),
        ("11", "RFVSS"),
        ("12", "RF2G4_IO"),
    ]


def test_package_pin_bogey() -> None:
    assert package_pin_bogey("LQFP176") == (176, 0)
    assert package_pin_bogey("TFBGA240+25") == (240, 25)
    assert package_pin_bogey("40-pin QFN") is None
    assert package_pin_bogey("") is None
