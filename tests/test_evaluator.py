"""Elimination-ledger evaluator tests: the discovery laws on real shapes.

The spec laws under test, by name:
- section 7: unknown never eliminates; condition mismatch is UNKNOWN
- section 41: FAIL eliminates only on hard atoms; NEAR_MISS cohorts
- section 42: margin never silent, applies to the requirement
- section 43: transient/surge never satisfy continuous axes
- section 28: every verdict carries evidence
"""

from __future__ import annotations

import pytest

from harness.discovery.evaluator import (
    AXES,
    Atom,
    axis_for_symbol,
    evaluate_design,
    evaluate_part,
)


def _claim(**kw):
    base = {"symbol": "VDSS", "qualifier": None, "condition": "",
            "value": 60.0, "unit": "V", "provenance": {"quote": "60",
                                                       "sha": "a" * 64,
                                                       "page": 3}}
    base.update(kw)
    return base


# --- axis routing -----------------------------------------------------------------

def test_axis_routing_normalizes_symbols():
    assert axis_for_symbol("V(BR)DSS") == "vds_rating_v"
    assert axis_for_symbol("RDS(ON)") == "rds_on_ohm"
    assert axis_for_symbol("TSTG") == "tstg_range_c"
    assert axis_for_symbol("QG") is None


def test_atom_rejects_unknown_axis():
    with pytest.raises(ValueError):
        Atom(axis="nope", value=1.0)


def test_atom_defaults_op_from_axis():
    assert Atom(axis="vds_rating_v", value=40.0).op == "min_rating"


# --- section 43: rating-class guards ----------------------------------------------

def test_pulse_claims_never_satisfy_continuous_axis():
    atom = Atom(axis="id_continuous_a", value=5.0)
    claim = _claim(symbol="IDM", value=996.0)   # pulsed, 996 A
    from harness.discovery.evaluator import evaluate_claim
    assert evaluate_claim(atom, claim) is None


def test_surge_claim_never_satisfies_continuous_axis():
    atom = Atom(axis="id_continuous_a", value=5.0)
    from harness.discovery.evaluator import evaluate_claim
    assert evaluate_claim(atom, _claim(symbol="ISM", value=15.4)) is None


# --- section 7: unknown never eliminates -------------------------------------------

def test_no_claim_is_unknown():
    out = evaluate_part("P1", [Atom(axis="vds_rating_v", value=40.0)], [])
    assert out["vds_rating_v"].verdict == "UNKNOWN"
    assert out["vds_rating_v"].reason == "no_claim"


def test_condition_mismatch_is_unknown_never_fail():
    """Efficiency-style law: claim measured at 25C vs required 100C."""
    atom = Atom(axis="id_continuous_a", value=5.0,
                required_condition={"tc_c": 100.0})
    claim = _claim(symbol="IDDC", value=7.4, condition="Tc = 25 °C")
    from harness.discovery.evaluator import evaluate_claim
    v = evaluate_claim(atom, claim)
    assert v.verdict == "UNKNOWN"
    assert v.reason == "condition_mismatch"
    assert v.condition_relation == "mismatch"


def test_condition_cover_passes():
    atom = Atom(axis="id_continuous_a", value=5.0,
                required_condition={"tc_c": 25.0})
    claim = _claim(symbol="IDDC", value=7.4, condition="vgs = 20 v tc = 25 °c")
    from harness.discovery.evaluator import evaluate_claim
    v = evaluate_claim(atom, claim)
    assert v.verdict == "PASS"
    assert v.condition_relation in ("covers", "exact")


def test_untyped_claim_condition_is_unknown_when_condition_required():
    atom = Atom(axis="id_continuous_a", value=5.0,
                required_condition={"tc_c": 100.0})
    claim = _claim(symbol="IDDC", value=7.4, condition="")
    from harness.discovery.evaluator import evaluate_claim
    v = evaluate_claim(atom, claim)
    assert v.verdict == "UNKNOWN"
    assert v.reason == "condition_absent"


def test_range_floor_never_fails_a_rating_atom():
    """TJ range row stored as Min-column claim (-55) must not read as a
    max-temp FAIL — wrong bound is UNKNOWN, never FAIL (catalog:
    AIMZA75R008M1H T_J -55.0, column_header Min)."""
    atom = Atom(axis="tj_max_c", value=125.0)
    claim = _claim(symbol="TJ", value=-55.0,
                   provenance={"quote": "-55", "page": 4,
                               "column_header": "Min"})
    from harness.discovery.evaluator import evaluate_claim
    v = evaluate_claim(atom, claim)
    assert v.verdict == "UNKNOWN"
    assert v.reason == "wrong_bound"


def test_range_floor_cannot_trivially_pass_max_rating():
    """rds_on axis keeps the guard via op semantics: a Min-column claim
    trivially passing max_rating is wrong-bound. Use a span-axis-shaped
    synthetic via the tj atom (min_column_evidence=False)."""
    atom = Atom(axis="tj_max_c", value=125.0)
    claim = _claim(symbol="TJ", value=-55.0,
                   provenance={"quote": "-55", "column_header": "Min"})
    from harness.discovery.evaluator import evaluate_claim
    v = evaluate_claim(atom, claim)
    assert v.verdict == "UNKNOWN"
    assert v.reason == "wrong_bound"


def test_guaranteed_minimum_column_is_valid_vds_evidence():
    """VDSS prints in the Min column ("breakdown >= 500 V") — that IS the
    rating (catalog: IPN50R1K4CE VDSS 500, column_header Min)."""
    atom = Atom(axis="vds_rating_v", value=36.0)
    claim = _claim(value=500.0,
                   provenance={"quote": "500", "column_header": "Min"})
    from harness.discovery.evaluator import evaluate_claim
    v = evaluate_claim(atom, claim)
    assert v.verdict == "PASS"


# --- section 41/42: verdicts, cohorts, margin ----------------------------------------

def test_pass_beats_fail_across_claims():
    claims = [_claim(value=30.0, condition="", qualifier=None),
              _claim(value=60.0)]
    out = evaluate_part("P1", [Atom(axis="vds_rating_v", value=40.0)], claims)
    assert out["vds_rating_v"].verdict == "PASS"
    assert out["vds_rating_v"].evidence["claim"]["value"] == 60.0


def test_all_fail_is_fail():
    claims = [_claim(value=20.0), _claim(value=25.0)]
    out = evaluate_part("P1", [Atom(axis="vds_rating_v", value=40.0)], claims)
    assert out["vds_rating_v"].verdict == "FAIL"


def test_near_miss_band_from_axis_default():
    claims = [_claim(value=37.0)]
    out = evaluate_part("P1", [Atom(axis="vds_rating_v", value=40.0)], claims)
    assert out["vds_rating_v"].verdict == "NEAR_MISS"


def test_margin_applies_to_requirement_not_claim():
    claims = [_claim(value=44.0)]
    out = evaluate_part("P1", [Atom(axis="vds_rating_v", value=36.0,
                                    margin_pct=20.0)], claims)
    v = out["vds_rating_v"]
    assert v.verdict == "PASS"
    cmp = v.evidence["comparison"]
    assert cmp["effective_requirement"] == pytest.approx(43.2)
    assert cmp["margin_pct_applied"] == 20.0


def test_margin_gap_is_near_miss_with_satisfies_raw():
    claims = [_claim(value=40.0)]
    out = evaluate_part("P1", [Atom(axis="vds_rating_v", value=36.0,
                                    margin_pct=20.0)], claims)
    v = out["vds_rating_v"]
    assert v.verdict == "NEAR_MISS"
    assert v.reason == "margin_gap"
    assert v.evidence["comparison"]["satisfies_raw_requirement"] is True


# --- section 28: every verdict carries evidence ---------------------------------------

def test_evidence_rides_every_verdict():
    claims = [_claim(value=60.0, provenance={"quote": "60", "page": 3})]
    out = evaluate_part("P1", [Atom(axis="vds_rating_v", value=40.0)], claims)
    ev = out["vds_rating_v"].evidence
    assert ev["claim"]["symbol"] == "VDSS"
    assert ev["provenance"]["quote"] == "60"


# --- design ledger: cohorts + constraint driver ----------------------------------------

def _population():
    return {
        "GOOD": [_claim(value=75.0)],
        "SHORT": [_claim(value=30.0)],           # hard FAIL
        "ALMOST": [_claim(value=38.0)],          # NEAR_MISS vs 40
        "OPAQUE": [{"symbol": "QG", "value": 30.0}],  # no axis claims
    }


def test_design_cohorts_and_constraint_driver():
    atoms = [Atom(axis="vds_rating_v", value=40.0)]
    result = evaluate_design(atoms, _population())
    assert result["candidates"] == 4
    assert result["cohorts"]["passing"] == ["GOOD"]
    assert result["cohorts"]["eliminated"] == ["SHORT"]
    assert result["cohorts"]["near_miss"] == ["ALMOST"]
    assert result["cohorts"]["unknown"] == ["OPAQUE"]
    # the killer stat: which constraint drives the search
    assert result["constraint_driver"]["vds_rating_v"]["eliminates_pct"] == 25.0


def test_unknown_never_eliminates_in_design():
    atoms = [Atom(axis="vds_rating_v", value=40.0)]
    result = evaluate_design(atoms, {"O1": [], "O2": [_claim(value=30.0)]})
    assert result["cohorts"]["eliminated"] == ["O2"]
    assert result["cohorts"]["unknown"] == ["O1"]


def test_soft_atom_fail_does_not_eliminate():
    """Spec section 41: soft-atom FAIL scores lower — it never eliminates
    AND never silently passes (soft_fail cohort)."""
    atoms = [Atom(axis="vds_rating_v", value=40.0, hard=False)]
    result = evaluate_design(atoms, {"S1": [_claim(value=30.0)]})
    assert result["cohorts"]["eliminated"] == []
    assert result["cohorts"]["soft_fail"] == ["S1"]
    assert result["cohorts"]["passing"] == []


def test_covers_direction_semantics():
    """A part's -55..150 storage range COVERS a -40..105 environment."""
    from harness.electronics.rating_class import compare_with_margin
    out = compare_with_margin([-55.0, 150.0], [-40.0, 105.0], "covers")
    assert out["verdict"] == "PASS"
    short = compare_with_margin([-40.0, 100.0], [-40.0, 105.0], "covers")
    assert short["verdict"] == "NEAR_MISS"          # 4.8% short of hi
    far = compare_with_margin([-40.0, 60.0], [-40.0, 105.0], "covers")
    assert far["verdict"] == "FAIL"


def test_covers_axis_pairs_bounds_and_single_bound_never_fails():
    atoms = [Atom(axis="tstg_range_c", value=[-40.0, 105.0])]
    paired = [
        {"symbol": "TSTG", "value": -55.0,
         "provenance": {"quote": "-55", "column_header": "Min"}},
        {"symbol": "TSTG", "value": 150.0,
         "provenance": {"quote": "150", "column_header": "Max"}},
    ]
    out = evaluate_part("P1", atoms, paired)
    v = out["tstg_range_c"]
    assert v.verdict == "PASS"
    assert v.evidence["paired_bounds"]["max_quote"] == "150"

    single = [_claim(symbol="TSTG", value=150.0,
                     provenance={"quote": "150", "column_header": "Max"})]
    out = evaluate_part("P2", atoms, single)
    assert out["tstg_range_c"].verdict == "UNKNOWN"
    assert out["tstg_range_c"].reason == "single_bound"
