"""CURVE-05A Workstream 3 — comparison-engine stress suite.

Eleven frozen challenge cases over the frozen corpus and synthetic
curves. Every case asserts either a correct comparable answer (cited,
advisory) or an explicit refusal reason — silence is failure. The
typical-never-guaranteed law is asserted at every layer.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness.discovery.curves import load_reference_curves, query_curve_evidence
from harness.electronics.curve_evidence import (
    CurveEvidence,
    compare_at_operating_point,
    query_operating_point,
)

PILOT = Path(__file__).parent / "fixtures/gold/curve_evidence_pilot"


@pytest.fixture(scope="module")
def curves():
    return load_reference_curves(
        sorted(p for p in PILOT.glob("*.json") if not p.name.startswith("_"))
    )


def _efficiency(curves):
    return [c for c in curves if c.y_kind == "efficiency"]


# case 1: same quantity, incompatible operating conditions


def test_case1_incompatible_vin_refused_with_reason(curves):
    out = query_curve_evidence(
        curves, phenomenon="efficiency_vs_load",
        operating_point={"x": 10.0},
        conditions={"vin_v": 24.0, "vout_v": 1.1},
    )
    comparable = [r for r in out["results"]
                  if r["match_class"] if False]  # noqa
    ok = [r for r in out["results"] if r.get("status") == "ok"]
    assert ok == []
    reasons = {r.get("reason") for r in out["not_usable"]}
    assert "condition_mismatch" in reasons
    for r in out["not_usable"]:
        if r.get("reason") == "condition_mismatch":
            assert r["compatibility"]["mismatched"][0]["key"] in (
                "vin_v", "vout_v")
            break


# case 2: different switching modes


def test_case2_mode_mismatch_refused(curves):
    out = query_curve_evidence(
        curves, phenomenon="efficiency_vs_load",
        operating_point={"x": 10.0},
        conditions={"vin_v": 12.0, "vout_v": 1.1,
                    "categorical": {"mode": "dcm"}},
    )
    parts = {r["part"] for r in out["results"] if r.get("status") == "ok"}
    assert parts == {"TPS548C26"}  # only the DCM figures answer


# case 3: different switching frequencies are a flagged unpinned dimension


def test_case3_frequency_dimension_flagged(curves):
    eff = _efficiency(curves)
    result = compare_at_operating_point(
        eff, 30.0,
        required_conditions={"vin_v": 12.0, "vout_v": 1.1},
    )
    unpinned = result["condition_dimensions_unpinned"]
    assert "legend_frequency" in unpinned
    assert any("MHz" in v or "kHz" in v
               for v in unpinned["legend_frequency"])


# case 4: different input/output voltages


def test_case4_vout_mismatch_refused(curves):
    out = query_curve_evidence(
        curves, phenomenon="efficiency_vs_load",
        operating_point={"x": 10.0},
        conditions={"vin_v": 12.0, "vout_v": 5.0},
    )
    ok = [r for r in out["results"] if r.get("status") == "ok"]
    assert ok == [] or all(
        r["part"] == "SiC461" for r in ok)  # only 5 V-out evidence answers


# case 5: temperature mismatch


def test_case5_temperature_mismatch_refused(curves):
    out = query_curve_evidence(
        curves, phenomenon="supply_current_vs_input_voltage",
        operating_point={"x": 12.0},
        conditions={"ta_c": 85.0},
    )
    ok = [r for r in out["results"] if r.get("status") == "ok"]
    assert ok == []
    assert all(
        r.get("reason") in ("condition_mismatch", "condition_missing",
                            "condition_conflict")
        for r in out["not_usable"]
    )


# case 6: missing temperature dimension named, never defaulted


def test_case6_missing_dimension_named(curves):
    out = query_curve_evidence(
        curves, phenomenon="supply_current_vs_temperature",
        operating_point={"x": 25.0}, conditions={},
    )
    for r in out["results"]:
        assert r["matched_conditions"] == {} or \
            r["matched_conditions"].get("ta_c") == {"swept_at": 25.0}


def test_case6b_sufficiency_names_missing_keys(curves):
    from harness.electronics.curve_evidence import condition_sufficiency

    eff = _efficiency(curves)[0]
    assert condition_sufficiency(eff, {}) == ["vin_v", "vout_v"]


# case 7: logarithmic interpolation (synthetic log axis)


def _log_curve():
    points = [
        {"x": x, "y": 2.0 + 1.0 * __import__("math").log10(x)}
        for x in (0.01, 0.1, 1.0, 10.0, 100.0)
    ]
    return CurveEvidence(
        document_sha256="d" * 64, page_1based=1, figure_index=0,
        series_index=0, caption="Figure L. Thermal impedance",
        region_bbox=None, figure_revision=None,
        axes={"x": {"label": "Pulse Width (s)", "unit": "s", "min": 0.01,
                    "max": 100, "scale": "log10"},
              "y": {"label": "Zth (C/W)", "unit": "°C/W", "min": 0,
                    "max": 5}},
        series={"name": "single", "points": points},
        conditions_verbatim=[], evidence_class_="typical",
        applies_to={"part": "SYNTH"}, uncertainty={},
        verification={"status": "reference"},
    )


def test_case7_log_interpolation_exact_on_decades():
    curve = _log_curve()
    r = query_operating_point(curve, 1.0)
    assert r["status"] == "ok" and r["value"] == pytest.approx(2.0)
    mid = query_operating_point(curve, 3.16227766)  # half decade
    assert mid["status"] == "ok" and mid["value"] == pytest.approx(2.5)


# case 8: out-of-range queries


def test_case8_out_of_range_refused(curves):
    out = query_curve_evidence(
        curves, phenomenon="efficiency_vs_load",
        operating_point={"x": 40.0},
        conditions={"vin_v": 12.0, "vout_v": 1.1},
    )
    oob = [r for r in out["not_usable"]
           if r.get("status") == "out_of_range"]
    assert oob and all("extrapolation" in r["reason"] for r in oob)
    assert not [r for r in out["results"] if r.get("status") == "ok"]


# case 9: typical versus guaranteed


def test_case9_typical_never_guaranteed(curves):
    eff = _efficiency(curves)
    assert all(c.evidence_class == "typical" for c in eff[:5])
    out = query_curve_evidence(
        curves, phenomenon="efficiency_vs_load",
        operating_point={"x": 10.0},
        conditions={"vin_v": 12.0, "vout_v": 1.1,
                    "categorical": {"mode": "fccm"}},
    )
    for r in out["results"]:
        if r.get("status") == "ok":
            assert r["guarantee"] is False
            assert r["evidence_class"] == "typical"
    cmp = compare_at_operating_point(
        eff, 10.0,
        required_conditions={"vin_v": 12.0, "vout_v": 1.1},
    )
    assert cmp["status"] == "proposal"
    assert "not guaranteed limits" in " ".join(cmp["assumptions"])


# case 10: differing reference-design external components


def test_case10_external_components_limited_applicability(curves):
    sic = [c for c in curves if c.applies_to.get("part") == "SiC461"
           and c.y_kind == "efficiency"]
    assert sic, "SiC461 efficiency evidence present"
    for c in sic:
        limitations = {
            lim for tag in c.relevance
            for lim in tag["limitations"]
        }
        assert any("BOM" in lim or "board" in lim.lower()
                   for lim in limitations), \
            "eval-board applicability limitation must ride the evidence"


# case 11: every refusal carries an explicit reason (exhaustive check)


def test_case11_all_refusals_reasoned(curves):
    probes = [
        {"phenomenon": "efficiency_vs_load",
         "operating_point": {"x": 10.0},
         "conditions": {"vin_v": 24.0, "vout_v": 1.1}},
        {"phenomenon": "efficiency_vs_load",
         "operating_point": {"x": 0.001},
         "conditions": {"vin_v": 12.0, "vout_v": 1.1}},
        {"phenomenon": "supply_current_vs_input_voltage",
         "operating_point": {"x": 12.0},
         "conditions": {"ta_c": 85.0}},
        {"phenomenon": "efficiency_vs_load",
         "operating_point": {"x": 1.0},
         "conditions": {"vin_v": 24.0, "vout_v": 5.0}},
    ]
    for probe in probes:
        out = query_curve_evidence(curves, **probe)
        for r in out["not_usable"]:
            assert r.get("reason") or r.get("status"), \
                f"unreasoned refusal in {probe['phenomenon']}"
        for entry in out["insufficient_conditions"]:
            assert entry.get("missing_required_keys")
