"""Source-derived regression tests for bounded-axis role binding (2026-10-27).

Every fact below is derived VERBATIM from a locally byte-verified PDF
(content sha256 cited); no filename identity, no invented rows:

  LPM entry threshold (contamination class):
    doc a0ad8212537c... (TPS2HC120-Q1) p9 / 1764346810ce... (TPS4HC120-Q1) p9
    row: "ILPM,enter | Load current level for entry to LPM | t > tSTBY |
          83 | 110 | 137 | mA"  (min 83 / typ 110 / max 137)
    => a mode-TRANSITION threshold, NOT output-current capability.

  PFM entry threshold (contamination class):
    doc eb6968a362d8... (TPS63024 family) p5
    row: "IPWMPFM | Output current to enter PFM mode | VIN=3V; VOUT=3.3V |
          350 | mA"  (typ 350)
    => mode-transition threshold, NOT output-current capability.

  RDS(on) typ/max relabel (role-binding class):
    doc edbb430ad7d9... (STL33N60M6, DS12638 Rev 3) p3 Table 4 r6
    row: "RDS(on) | Static drain-source on-resistance | VGS=10 V, ID=10.5 A |
          (min -) | 0.115 (Typ) | 0.137 (Max) | Ω"
    cells verified: Typ [462.047,333.764,492.283,359.271]=0.115,
                    Max [492.283,333.764,522.52,359.271]=0.137
    => the MAX column (137 mΩ) binds for a max-valued axis; the typ value
       must NEVER be relabeled maximum.

Run: python -m pytest tests/test_power_axes_role_binding.py -q
"""
from __future__ import annotations

from harness.electronics.power_axes_v2 import bounded_axis_rows


def _fact(**kw):
    base = {
        "symbol": "", "parameter": "", "value": None, "min": None, "max": None,
        "typ": None, "unit": None, "page": 5,
        "table_title": "Electrical Characteristics", "table_kind": "ec",
        "verbatim": "row", "condition_verbatim": None,
    }
    base.update(kw)
    return base


# --- contamination: mode-transition thresholds never fill iout ---------------

def test_lpm_entry_threshold_never_fills_iout():
    # source: docs a0ad8212537c.../1764346810ce... p9 (byte-verified)
    rows = bounded_axis_rows([_fact(
        symbol="ILPM,enter",
        parameter="Load current level for entry to LPM",
        min=83, typ=110, max=137, unit="mA", page=9,
        condition_verbatim="t > tSTBY",
        verbatim="ILPM,enter | Load current level for entry to LPM | t > tSTBY | 83 | 110 | 137 | mA")])
    assert rows == [], f"mode-transition threshold must not fill iout: {rows}"


def test_pfm_entry_threshold_never_fills_iout():
    # source: doc eb6968a362d8... p5 (byte-verified)
    rows = bounded_axis_rows([_fact(
        symbol="IPWMPFM",
        parameter="Output current to enter PFM mode",
        typ=350, unit="mA", page=5,
        condition_verbatim="VIN =3V; VOUT = 3.3V",
        verbatim="IPWMPFM | Output current to enter PFM mode | VIN =3V; VOUT = 3.3V | 350 | mA")])
    assert rows == [], f"mode-transition threshold must not fill iout: {rows}"


def test_pfm_mode_conditioned_maximum_never_fills_iout():
    # source: doc 30183c477d78... (LM3263) p7 — "Maximum output load current in
    # PFM mode" is a mode-conditioned limit, not the part's output capability
    rows = bounded_axis_rows([_fact(
        symbol="IOUT,MAX_PFM",
        parameter="Maximum output load current in PFM mode",
        value=60, unit="mA", page=7,
        condition_verbatim="VBATT = 3.8 V, VSET = 3.2 V",
        verbatim="IOUT,MAX_PFM | Maximum output load current in PFM mode | VBATT = 3.8 V, VSET = 3.2 V | 60 | mA")])
    assert rows == [], f"PFM-mode-conditioned limit must not fill iout: {rows}"


# --- role binding: max-valued axes bind the MAX column only ------------------

def test_rds_binds_max_column_not_typ():
    # source: doc edbb430ad7d9... (STL33N60M6) p3 Table 4 r6 (cells verified)
    rows = bounded_axis_rows([_fact(
        symbol="RDS(on)",
        parameter="Static drain-source on-resistance",
        typ=0.115, max=0.137, unit="Ω", page=3,
        condition_verbatim="VGS = 10 V, ID = 10.5 A",
        verbatim="RDS(on) | Static drain-source on-resistance | VGS = 10 V, ID = 10.5 A | 0.115 | 0.137 | Ω")])
    assert len(rows) == 1
    assert rows[0]["field"] == "rds_on_mohm"
    assert abs(rows[0]["value"] - 137.0) < 1e-9, (
        f"must bind the MAX column (137 mΩ), got {rows[0]['value']}")
    assert rows[0]["value_role"] == "max"


def test_rds_typ_only_fails_closed():
    # a typ-only row must NEVER be relabeled maximum (fail closed, no row)
    rows = bounded_axis_rows([_fact(
        symbol="RDS(on)", parameter="Static drain-source on-resistance",
        typ=0.115, unit="Ω", page=3,
        verbatim="RDS(on) | 0.115 | Ω")])
    assert rows == [], f"typ-only must fail closed: {rows}"


def test_iout_typ_only_fails_closed():
    rows = bounded_axis_rows([_fact(
        symbol="IOUT", parameter="Maximum output current",
        typ=200, unit="mA", page=4,
        verbatim="IOUT | 200 | mA")])
    assert rows == [], f"typ-only must fail closed: {rows}"


# --- positive controls: valid specified maxima still fill --------------------

def test_iout_specified_max_fills():
    rows = bounded_axis_rows([_fact(
        symbol="IOUT", parameter="Maximum output current",
        max=200.0, unit="mA", page=4,
        verbatim="IOUT | 200 | mA")])
    assert len(rows) == 1 and rows[0]["field"] == "iout_max_a"
    assert abs(rows[0]["value"] - 0.2) < 1e-12 and rows[0]["value_role"] == "max"


def test_rds_specified_max_fills():
    rows = bounded_axis_rows([_fact(
        symbol="RDS(on)", parameter="Drain-source on-state resistance",
        min=100, typ=160, max=240, unit="mΩ", page=2,
        condition_verbatim="V GS=-10 V, I D=-10.6 A",
        # source: doc 4551601c... (SPD15P10P G, Rev 1.0) p2 t1 r5 (cells verified)
        verbatim="Drain-source on-state resistance | R DS(on) | V GS=-10 V, I D=-10.6 A | 160 | 240 | mΩ")])
    assert len(rows) == 1 and abs(rows[0]["value"] - 240.0) < 1e-9


def test_fsw_specified_max_fills():
    rows = bounded_axis_rows([_fact(
        symbol="fSW", parameter="Switching frequency",
        max=1.2, unit="MHz", page=4,
        verbatim="fSW | 1.2 | MHz")])
    assert rows and rows[0]["field"] == "fsw_max_khz" and rows[0]["value"] == 1200.0


# --- axis-specific semantics preserved (unchanged by this fix) ----------------

def test_vin_range_semantics_unchanged():
    rows = bounded_axis_rows([_fact(
        symbol="VIN", parameter="Input voltage range",
        min=2.2, max=5.5, unit="V", page=3,
        verbatim="VIN | 2.2 | 5.5 | V")])
    got = {r["field"]: r["value"] for r in rows}
    assert got == {"vin_min_v": 2.2, "vin_max_v": 5.5}


def test_vds_rating_semantics_unchanged():
    # vds remains a rating bound from the printed value (documented unresolved
    # related axis: rating-vs-column binding not changed by this fix)
    rows = bounded_axis_rows([_fact(
        symbol="V(BR)DSS", parameter="Drain-source breakdown voltage",
        value=600, unit="V", page=3,
        verbatim="V(BR)DSS | 600 | V")])
    assert rows and rows[0]["field"] == "vds_v" and rows[0]["value"] == 600.0
