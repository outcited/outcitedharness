from __future__ import annotations

from harness.electronics.connector_parametrics import (
    collapse_series,
    connector_parametric_rows,
)

SPEC_PAGE = """
Specifications
Number of positions: 12
Pitch: 0.5 mm
Rated current: 0.5 A AC/DC per contact
Rated voltage: 50 V AC
Dielectric withstanding voltage: 250 V AC for 1 minute
Operating temperature range: -25°C to +85°C
"""


def test_axes_extract_with_verbatim():
    rows = connector_parametric_rows([SPEC_PAGE])
    axes = {row["axis"]: row for row in rows}
    assert axes["positions"]["value"] == 12
    assert axes["pitch_mm"]["value"] == 0.5
    assert axes["current_rating_a"]["value"] == 0.5
    assert axes["voltage_rating_v"]["value"] == 50
    assert axes["dielectric_withstanding_v"]["value"] == 250
    assert axes["temp_range_c"]["value"] == [-25.0, 85.0]
    assert "0.5 mm" in axes["pitch_mm"]["verbatim"]


def test_unlabeled_numbers_never_emit():
    rows = connector_parametric_rows(
        ["Dimensions 12.4 x 3.2 mm, 24 circuits available on request."]
    )
    assert rows == []


def test_collapse_takes_most_common_print():
    pages = [
        "Rated current: 0.5 A AC/DC",
        "Rated current: 0.5 A AC/DC",
        "Rated current: 1 A AC/DC (note)",
    ]
    collapsed = collapse_series(connector_parametric_rows(pages))
    assert collapsed["current_rating_a"] == 0.5
    assert collapsed["current_rating_a_prints"] == 3


def test_current_without_ampere_unit_rejected():
    rows = connector_parametric_rows(["Rated current per 1000 mating cycles"])
    assert rows == []
