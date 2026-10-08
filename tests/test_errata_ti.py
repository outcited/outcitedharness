from __future__ import annotations

from harness.electronics.errata_deltas import (
    ti_advisory_details,
    ti_affected_matrix,
)

TI_DETAILS = """
BCL16
BCL Module
Category
Functional
Function
SMCLK clock source selection from XT1/VLO to DCO
Description
When the MCLK and the SMCLK do not use the DCO, the DCO is off. The DCO does not start.
Workaround
Set clock source of MCLK to DCO.
CPU14
CPU Module
Category
Functional
Function
Erroneous setting of SCG0 after reset
Description
The SCG0 bit in the CPU status register may be erroneously set after reset.
Workaround
None.
"""

# word grid: (x0, y0, x1, y1, text) — matrix page layout, stacked header
# (revision letter printed above its "Rev" label, as the TI PDFs ship)
TI_MATRIX_WORDS = [
    (50, 100, 120, 112, "Errata"),
    (124, 100, 160, 112, "Number"),
    (204, 100, 228, 110, "J"),
    (304, 100, 328, 110, "I"),
    (404, 100, 428, 110, "H"),
    (200, 114, 232, 124, "Rev"),
    (300, 114, 332, 124, "Rev"),
    (400, 114, 432, 124, "Rev"),
    (50, 140, 90, 152, "BCL12"),
    (205, 140, 225, 152, "✓"),
    (305, 140, 325, 152, "✓"),
    (405, 140, 425, 152, "✓"),
    (50, 160, 90, 172, "BCL13"),
    (405, 160, 425, 172, "✓"),
]


def test_ti_advisory_details_parse_fields():
    records = ti_advisory_details([line for line in TI_DETAILS.splitlines() if line])
    assert [record["issue_id"] for record in records] == ["BCL16", "CPU14"]
    bcl16 = records[0]
    assert bcl16["category"] == "Functional"
    assert bcl16["title_verbatim"] == "SMCLK clock source selection from XT1/VLO to DCO"
    assert bcl16["symptom_verbatim"].startswith("When the MCLK")
    assert bcl16["workaround_verbatim"].startswith("Set clock source")


def test_ti_matrix_joins_marks_by_x_position():
    affected = ti_affected_matrix([TI_MATRIX_WORDS])
    assert affected == {"BCL12": ["Rev H", "Rev I", "Rev J"], "BCL13": ["Rev H"]}


def test_ti_matrix_ignores_pages_without_header():
    assert ti_affected_matrix([[(10, 10, 20, 20, "BCL12"), (30, 10, 40, 20, "✓")]]) == {}
