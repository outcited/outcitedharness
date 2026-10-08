from __future__ import annotations

from harness.electronics.thermal_metrics import collapse_thermal, thermal_rows


def _fact(symbol, parameter, value, unit, condition=None, title="Thermal Information"):
    return {
        "symbol": symbol, "parameter": parameter, "typ": value,
        "unit": unit, "condition_verbatim": condition, "table_title": title,
        "page": 12, "verbatim": f"{symbol} {parameter} {value} {unit}",
    }


def test_rthja_row_with_unit_emits():
    rows = thermal_rows([_fact("RθJA", "Thermal resistance, junction to ambient", 42.3, "°C/W")])
    assert len(rows) == 1
    assert rows[0]["metric"] == "RthetaJA"
    assert rows[0]["value"] == 42.3
    assert rows[0]["unit"] == "C/W"


def test_spelled_form_and_psi_and_zth():
    facts = [
        _fact("", "Junction-to-case thermal resistance", 3.1, "K/W"),
        _fact("ΨJT", "Junction to top characterization parameter", 0.8, "°C/W"),
        _fact("ZthJA", "Transient thermal impedance", 0.4, "°C/W"),
    ]
    rows = thermal_rows(facts)
    kinds = {row["metric"] for row in rows}
    assert kinds == {"RthetaJC", "PsiJT", "ZthJA"}


def test_mention_without_thermal_unit_never_emits():
    assert thermal_rows([_fact("RθJA", "Thermal resistance, junction to ambient", 42.3, "")]) == []
    assert thermal_rows([_fact("", "Thermal shutdown", 150, "°C")]) == []


def test_collapse_prefers_jedec_referenced():
    rows = [
        {"metric": "RthetaJA", "value": 45.0, "value_role": "typ",
         "condition_verbatim": "minimum pad"},
        {"metric": "RthetaJA", "value": 42.3, "value_role": "typ",
         "condition_verbatim": "JEDEC 51-7, 4-layer board"},
    ]
    winner = collapse_thermal(rows)
    assert winner["RthetaJA"]["value"] == 42.3
