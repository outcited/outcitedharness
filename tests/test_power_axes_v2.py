from __future__ import annotations

from harness.electronics.power_axes_v2 import (
    bounded_axis_rows,
    collapse_rows,
    front_vout_rows,
    iq_rows,
    vout_rows,
)


def test_vout_range_from_min_max_columns():
    facts = [
        {
            "symbol": "VOUT",
            "parameter": "Output voltage range",
            "min": 0.8,
            "max": 5.5,
            "typ": None,
            "value": None,
            "unit": "V",
            "page": 5,
            "table_title": "Recommended Operating Conditions",
            "table_kind": "roc",
            "verbatim": "VOUT | 0.8 | 5.5 | V",
            "condition_verbatim": None,
        }
    ]
    rows = vout_rows(facts)
    assert len(rows) == 1
    assert rows[0]["vout_min_v"] == 0.8
    assert rows[0]["vout_max_v"] == 5.5
    assert rows[0]["value_role"] == "range"


def test_vout_fixed_single_value():
    facts = [
        {
            "symbol": "VOUT",
            "parameter": "Output voltage, fixed",
            "typ": 3.3,
            "unit": "V",
            "page": 4,
            "table_title": "Electrical Characteristics",
            "table_kind": "ec",
            "verbatim": "VOUT 3.3 V",
            "condition_verbatim": None,
        }
    ]
    rows = vout_rows(facts)
    assert rows and rows[0]["vout_min_v"] == rows[0]["vout_max_v"] == 3.3
    assert rows[0]["value_role"] == "fixed"


def test_absmax_never_fills_vout_or_iq():
    facts = [
        {
            "symbol": "VOUT",
            "parameter": "Output voltage range",
            "min": -0.3,
            "max": 7.0,
            "unit": "V",
            "page": 3,
            "table_title": "Absolute Maximum Ratings",
            "table_kind": "absmax",
            "verbatim": "VOUT -0.3 7 V",
            "condition_verbatim": None,
        },
        {
            "symbol": "IQ",
            "parameter": "Quiescent current",
            "typ": 2.5,
            "unit": "µA",
            "page": 3,
            "table_title": "Absolute Maximum Ratings",
            "table_kind": "absmax",
            "verbatim": "IQ 2.5 µA",
            "condition_verbatim": None,
        },
    ]
    assert vout_rows(facts) == []
    assert iq_rows(facts) == []


def test_iq_normalizes_to_amps_and_rejects_leakage():
    facts = [
        {
            "symbol": "IQ",
            "parameter": "Quiescent current",
            "typ": 2.5,
            "unit": "µA",
            "page": 6,
            "table_title": "Electrical Characteristics",
            "table_kind": "ec",
            "verbatim": "IQ 2.5 µA",
            "condition_verbatim": "TA = 25°C",
        },
        {
            "symbol": "ILKG",
            "parameter": "Output leakage current",
            "typ": 1.0,
            "unit": "µA",
            "page": 6,
            "table_title": "Electrical Characteristics",
            "table_kind": "ec",
            "verbatim": "ILKG 1 µA",
            "condition_verbatim": None,
        },
    ]
    rows = iq_rows(facts)
    assert len(rows) == 1
    assert rows[0]["field"] == "iq"
    assert abs(rows[0]["value"] - 2.5e-6) < 1e-15
    assert rows[0]["unit"] == "A"
    assert rows[0]["unit_as_printed"] == "µA"


def test_front_page_adjustable_range():
    rows = front_vout_rows(
        ["Adjustable output voltage from 0.8 V to 15 V, 6-A regulator."]
    )
    assert rows and rows[0]["vout_min_v"] == 0.8
    assert rows[0]["vout_max_v"] == 15.0
    assert rows[0]["table_kind"] == "front_page"


def test_collapse_prefers_ta25_typ_over_front_page():
    rows = [
        {"field": "iq", "value": 3.0e-6, "value_role": "typ",
         "condition_verbatim": None, "table_kind": "ec"},
        {"field": "iq", "value": 2.5e-6, "value_role": "typ",
         "condition_verbatim": "TA = 25°C", "table_kind": "ec"},
    ]
    winner = collapse_rows(rows)
    assert winner["iq"]["value"] == 2.5e-6


def test_protection_thresholds_and_dropout_never_fill_vout():
    facts = [
        {
            "symbol": "VOUT_OVP",
            "parameter": "OUT overvoltage rising threshold",
            "min": 103.0,
            "max": 105.0,
            "unit": "V",
            "page": 7,
            "table_title": "Electrical Characteristics",
            "table_kind": "characteristics",
            "verbatim": "VOUT_OVP 103 105 V",
            "condition_verbatim": None,
        },
        {
            "symbol": "VREGN_DROPOUT",
            "parameter": "REGN Output Voltage Dropout",
            "min": 4.4,
            "max": 4.75,
            "unit": "V",
            "page": 7,
            "table_title": "Electrical Characteristics",
            "table_kind": "characteristics",
            "verbatim": "VREGN_DROPOUT 4.4 4.75 V",
            "condition_verbatim": None,
        },
    ]
    assert vout_rows(facts) == []


# --- bounded decision-critical axes (vin / iout / fsw / vds / rds) ---------

def _fact(**kw):
    base = {
        "symbol": "", "parameter": "", "value": None, "min": None, "max": None,
        "typ": None, "unit": None, "page": 5,
        "table_title": "Electrical Characteristics", "table_kind": "ec",
        "verbatim": "row", "condition_verbatim": None,
    }
    base.update(kw)
    return base


def test_vin_range_fills_two_axis_rows():
    rows = bounded_axis_rows([_fact(symbol="VIN", parameter="Input voltage range",
                                    min=2.2, max=5.5, unit="V")])
    got = {r["field"]: r["value"] for r in rows}
    assert got["vin_min_v"] == 2.2
    assert got["vin_max_v"] == 5.5


def test_iout_normalizes_to_amps():
    rows = bounded_axis_rows([_fact(symbol="IOUT", parameter="Maximum output current",
                                    max=200.0, unit="mA")])
    assert len(rows) == 1 and rows[0]["field"] == "iout_max_a"
    assert abs(rows[0]["value"] - 0.2) < 1e-12 and rows[0]["unit"] == "A"


def test_fsw_normalizes_to_khz():
    rows = bounded_axis_rows([_fact(symbol="FSW", parameter="Switching frequency",
                                    value=1.2, unit="MHz")])
    assert rows and rows[0]["field"] == "fsw_max_khz" and rows[0]["value"] == 1200.0


def test_vds_rating_and_rds_mohm():
    rows = bounded_axis_rows([
        _fact(symbol="VDSS", parameter="Drain-to-source breakdown voltage",
              value=60.0, unit="V"),
        _fact(symbol="RDS(on)", parameter="Drain-to-source on-resistance",
              max=44.0, unit="mOhm"),
    ])
    got = {r["field"]: r["value"] for r in rows}
    assert got.get("vds_v") == 60.0
    assert got.get("rds_on_mohm") == 44.0


def test_bounded_absmax_never_fills():
    rows = bounded_axis_rows([
        _fact(symbol="VIN", parameter="Input voltage range", min=-0.3, max=7.0,
              unit="V", table_title="Absolute Maximum Ratings", table_kind="absmax"),
        _fact(symbol="IOUT", parameter="Maximum output current", max=5.0,
              unit="A", table_title="Absolute Maximum Ratings", table_kind="absmax"),
    ])
    assert rows == []


def test_bounded_rejects_uvlo_thresholds():
    rows = bounded_axis_rows([
        _fact(symbol="UVLORISE", parameter="Undervoltage lockout input voltage rising",
              value=1.28, unit="V"),
        _fact(symbol="VIN", parameter="Input voltage range", min=2.2, max=5.5, unit="V"),
    ])
    assert [r["field"] for r in rows] == ["vin_min_v", "vin_max_v"]


def test_bounded_rejects_incompatible_unit():
    rows = bounded_axis_rows([_fact(symbol="IVIN", parameter="VIN pin leakage current",
                                    typ=1.0, unit="uA")])
    assert rows == []


def test_bounded_no_concatenated_header_match():
    rows = bounded_axis_rows([_fact(
        symbol="UVLORISEUVLOFALLUVLOHYS",
        parameter="UNDERVOLTAGE LOCKOUT Input Voltage Rising Input Voltage Falling",
        value=1.28, unit="V")])
    assert rows == []
