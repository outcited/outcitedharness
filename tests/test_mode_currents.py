from __future__ import annotations

from harness.electronics.mode_currents import (
    collapse_competing,
    mode_current_rows,
)
from harness.electronics.page_index import classify_section


def _table(rows):
    return {"table_index": 0, "rows": rows}


def test_mode_current_rows_extracts_printed_mode_rows():
    table = _table(
        [
            ["Mode", "Conditions", "Min", "Typ", "Max", "Unit"],
            [
                "Run mode",
                "VDD = 3.3 V, fHCLK = 64 MHz, TA = 25 °C",
                "-",
                "5.2",
                "6.1",
                "mA",
            ],
            ["Sleep mode", "VDD = 3.3 V, TA = 25 °C", "-", "1.2", "-", "mA"],
        ]
    )
    rows = mode_current_rows(
        table, document_sha256="a" * 64, page_1based=41
    )
    modes = {row["mode"]: row for row in rows}
    assert set(modes) == {"Run mode", "Sleep mode"}
    run = modes["Run mode"]
    assert run["value"] == 5.2
    assert run["unit"] == "mA"
    assert run["value_role"] == "typ"
    assert run["anchors"] == {"vdd_v": 3.3, "freq_mhz": 64.0, "ta_c": 25.0}
    assert "5.2" in run["verbatim"]
    assert run["method"] == "deterministic_mode_currents_v1"
    max_rows = [r for r in rows if r["mode"] == "Run mode" and r["value_role"] == "max"]
    assert max_rows and max_rows[0]["value"] == 6.1


def test_mode_current_rows_blanks_and_prose_never_emit():
    table = _table(
        [
            ["Mode", "Conditions", "Typ", "Unit"],
            ["Standby", "VDD = 1.8 V", "-", "µA"],
            ["Standby", "VDD = 1.8 V", "", "µA"],
            ["See Figure 12 for typical characteristics", "", "3", "µA"],
            ["Stop 2", "VDD = 1.8 V", "1.1", "µA"],
        ]
    )
    rows = mode_current_rows(
        table, document_sha256="a" * 64, page_1based=42
    )
    assert [row["mode"] for row in rows] == ["Stop 2"]
    assert rows[0]["value"] == 1.1
    assert rows[0]["unit"] == "µA"


def test_collapse_competing_prefers_ta25():
    rows = mode_current_rows(
        _table(
            [
                ["Mode", "Conditions", "Typ", "Unit"],
                ["Standby", "VDD = 3.0 V, TA = 85 °C", "2.2", "µA"],
                ["Standby", "VDD = 3.0 V, TA = 25 °C", "1.8", "µA"],
                ["Standby", "VDD = 3.0 V, TA = -40 °C", "1.5", "µA"],
            ]
        ),
        document_sha256="a" * 64,
        page_1based=43,
    )
    assert len(rows) == 3
    collapsed = collapse_competing(rows)
    assert len(collapsed) == 1
    assert collapsed[0]["value"] == 1.8
    assert collapsed[0]["anchors"]["ta_c"] == 25.0


def test_collapse_competing_keeps_distinct_vdd_points():
    rows = mode_current_rows(
        _table(
            [
                ["Mode", "Conditions", "Typ", "Unit"],
                ["Run mode", "VDD = 1.8 V, fHCLK = 8 MHz", "1.1", "mA"],
                ["Run mode", "VDD = 3.3 V, fHCLK = 8 MHz", "2.3", "mA"],
            ]
        ),
        document_sha256="a" * 64,
        page_1based=44,
    )
    collapsed = collapse_competing(rows)
    assert sorted(row["value"] for row in collapsed) == [1.1, 2.3]


def test_bare_number_cells_take_the_unit_column():
    table = _table(
        [
            ["Mode", "Typ [µA]", "Unit"],
            ["Snooze", "0.35", "µA"],
        ]
    )
    # "Typ [µA]" is a role header via inline role grammar
    rows = mode_current_rows(
        table, document_sha256="a" * 64, page_1based=45
    )
    assert rows and rows[0]["value"] == 0.35
    assert rows[0]["unit"] == "µA"


def test_vcc_column_feeds_the_vdd_anchor():
    table = _table(
        [
            ["Parameter", "Test conditions", "VCC", "TYP", "MAX", "Unit"],
            [
                "Active mode (AM) current (1 MHz)",
                "fDCO = fMCLK = fSMCLK = 1 MHz",
                "2.2 V",
                "270",
                "-",
                "µA",
            ],
            [
                "Active mode (AM) current (1 MHz)",
                "fDCO = fMCLK = fSMCLK = 1 MHz",
                "3 V",
                "390",
                "550",
                "µA",
            ],
        ]
    )
    rows = mode_current_rows(
        table, document_sha256="a" * 64, page_1based=15
    )
    by_vdd = {row["anchors"].get("vdd_v"): row for row in rows if row["value_role"] == "typ"}
    assert set(by_vdd) == {2.2, 3.0}
    assert by_vdd[2.2]["value"] == 270.0
    assert by_vdd[2.2]["unit"] == "µA"
    assert by_vdd[3.0]["value"] == 390.0
    assert by_vdd[3.0]["anchors"].get("freq_mhz") == 1.0


def test_page_index_routes_the_two_new_lanes():
    assert classify_section("Typical operating characteristics") == (
        "typical_characteristics",
    )
    assert classify_section("Performance curves") == ("typical_characteristics",)
    assert "power_modes" in classify_section("Power consumption modes")
    assert "power_modes" in classify_section("Current consumption")
    assert "power_modes" in classify_section("Supply current characteristics")
    assert "power_modes" not in classify_section("Low-power mode transitions")
    assert classify_section("Electrical characteristics") == ("parametrics",)
