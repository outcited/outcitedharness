"""Curve-evidence layer tests: R1-R5 laws over the frozen pilot fixtures
plus synthetic contract probes (PRD-CURVE-02)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness.discovery.curves import query_curve_evidence
from harness.electronics.curve_evidence import (
    CURVE_EVIDENCE_SCHEMA,
    CurveEvidence,
    compare_at_operating_point,
    condition_compatibility,
    condition_sufficiency,
    evidence_class,
    evaluate_load_distribution,
    query_operating_point,
    typed_conditions,
)

PILOT = Path(__file__).parent / "fixtures" / "gold" / "curve_evidence_pilot"


@pytest.fixture(scope="module")
def tps_curves():
    record = json.loads((PILOT / "tps548c26_p10.json").read_text())
    out = []
    for plot in record["plots"]:
        for index in range(len(plot["series"])):
            out.append(CurveEvidence.from_reference_plot(record, plot, index))
    return out


@pytest.fixture(scope="module")
def lmr_curves():
    record = json.loads((PILOT / "lmr33610_p8.json").read_text())
    out = []
    for plot in record["plots"]:
        for index in range(len(plot["series"])):
            out.append(CurveEvidence.from_reference_plot(record, plot, index))
    return out


@pytest.fixture()
def efficiency_curve(tps_curves):
    return next(c for c in tps_curves if c.y_kind == "efficiency")


def _linear_curve(points, name="s", unit="%", scale=None):
    return CurveEvidence(
        document_sha256="a" * 64,
        page_1based=1,
        figure_index=0,
        series_index=0,
        caption="Figure 1. Test",
        region_bbox=(0.0, 0.0, 10.0, 10.0),
        figure_revision="REV A",
        axes={
            "x": {"label": "Load Current (A)", "unit": "A",
                  "min": points[0][0], "max": points[-1][0],
                  **({"scale": scale} if scale else {})},
            "y": {"label": "Efficiency (%)", "unit": "%", "min": 0, "max": 100},
        },
        series={"name": name, "condition": None,
                "points": [{"x": x, "y": y} for x, y in points]},
        conditions_verbatim=[],
        evidence_class_="typical",
        applies_to={"part": "TESTPART", "category": "power.dcdc"},
        uncertainty={"method": "test"},
        verification={"status": "reference"},
    )


# --- R1: curve evidence object -------------------------------------------------


def test_r1_contract_fields_and_id_stability(efficiency_curve):
    c = efficiency_curve
    assert c.curve_id.startswith("curve-")
    clone = CurveEvidence.from_reference_plot(
        json.loads((PILOT / "tps548c26_p10.json").read_text()),
        json.loads((PILOT / "tps548c26_p10.json").read_text())["plots"][0],
        0,
    )
    assert clone.curve_id == c.curve_id
    d = c.to_dict()
    assert d["schema"] == CURVE_EVIDENCE_SCHEMA
    assert d["evidence_class"] == "typical"
    assert d["document_sha256"] and d["page_1based"] == 10
    assert d["applies_to"]["part"] == "TPS548C26"
    assert d["applies_to"]["category"] == "power.dcdc"
    assert d["verification"]["status"] == "reference"
    assert d["payload"], "underlying plot payload preserved"
    assert d["series_name"]


def test_r1_evidence_class_detection():
    assert evidence_class("6.6 Typical Characteristics") == "typical"
    assert evidence_class("Figure 1. Maximum On-Resistance") == "maximum"
    assert evidence_class("worst-case drift") == "guaranteed"
    assert evidence_class("Figure 2. Output Voltage") == "unspecified"


def test_r1_from_extraction_row_gates_on_verdict():
    row = {
        "document_sha256": "b" * 64,
        "page_1based": 3,
        "figure_index": 1,
        "verdict": "hold_ungrounded_axis",
        "ungrounded_labels": ["axis_y:IQ"],
        "region_bbox": [1, 2, 3, 4],
        "payload": {
            "title": "Figure 5. IDD vs VDD",
            "axes": {
                "x": {"label": "VDD", "unit": "V", "min": 0, "max": 4},
                "y": {"label": "IDD", "unit": "mA", "min": 0, "max": 10},
            },
            "series": [{"name": "run", "points": [
                {"x": 1, "y": 2}, {"x": 3, "y": 4}]}],
        },
    }
    curves = CurveEvidence.from_extraction_row(row, section_text="Typical")
    assert len(curves) == 1
    assert not curves[0].usable
    result = query_operating_point(curves[0], 2.0)
    assert result["status"] == "not_usable"
    assert "hold_ungrounded_axis" in result["reason"]
    row["verdict"] = "extracted"
    row["ungrounded_labels"] = []
    assert CurveEvidence.from_extraction_row(row)[0].usable


# --- R2: condition comparability ------------------------------------------------


def test_r2_comparable_when_matched(efficiency_curve):
    compat = condition_compatibility(
        efficiency_curve.conditions,
        {"vin_v": 12.0, "vout_v": 1.1},
    )
    assert compat["status"] == "comparable"
    assert compat["matched"]["vin_v"] == 12.0


def test_r2_mismatch_and_missing_never_default():
    keys = {"keys": {"vin_v": 12.0, "vout_v": 1.1},
            "categorical": {"mode": "fccm"}}
    assert condition_compatibility(keys, {"vin_v": 24.0})["reason"] == \
        "condition_mismatch"
    assert condition_compatibility(keys, {"iout_a": 5.0})["reason"] == \
        "condition_missing"
    partial = condition_compatibility(keys, {"vin_v": 12.0})
    assert partial["status"] == "comparable"
    assert partial["matched"] == {"vin_v": 12.0}, \
        "asked subset matches on that subset only; the phenomenon-level " \
        "insufficiency law is enforced by condition_sufficiency"


def test_r2_categorical_condition_law():
    keys = {"keys": {}, "categorical": {"mode": "fccm"}}
    assert condition_compatibility(keys, {
        "categorical": {"mode": "FCCM"}})["status"] == "comparable"
    result = condition_compatibility(keys, {"categorical": {"mode": "dcm"}})
    assert result["reason"] == "condition_mismatch"
    assert result["mismatched"][0]["key"] == "categorical.mode"


def test_r2_sweep_variable_is_not_a_condition(lmr_curves):
    quiescent = next(
        c for c in lmr_curves
        if "Quiescent" in str((c.axes.get("y") or {}).get("label") or "")
    )
    assert quiescent.x_kind == "input_voltage"
    assert "vin_v" not in quiescent.conditions["keys"], \
        "the x-axis sweep variable must not stay a fixed condition"


def test_r2_legend_temperature_overrides_page_default(lmr_curves):
    shutdown = [c for c in lmr_curves if "Shutdown" in (c.caption or "")]
    by_name = {c.series["name"]: c for c in shutdown}
    assert by_name["-40C"].conditions["keys"]["ta_c"] == -40.0
    assert by_name["-40C"].conditions["legend_override"] == {
        "page_default": 25.0, "series_legend": -40.0,
    }
    assert by_name["25C"].conditions["keys"].get("ta_c") == 25.0
    assert "legend_override" not in by_name["25C"].conditions


def test_r2_conflicting_conditions_fail_closed():
    parsed = typed_conditions(["VIN = 12 V", "VIN = 24 V"])
    assert parsed["conflict"], "repeated key with a different value conflicts"
    result = condition_compatibility(parsed, {"vin_v": 12.0})
    assert result["status"] == "not_comparable"
    assert result["reason"] == "condition_conflict"


def test_r2_alias_typing_pvin():
    parsed = typed_conditions(["PVIN = 12 V", "MODE = FCCM"])
    assert parsed["keys"]["vin_v"] == 12.0
    assert parsed["categorical"]["mode"] == "fccm"


# --- R3: operating-point query ---------------------------------------------------


def test_r3_bounded_interpolation_linear():
    curve = _linear_curve([(0.0, 0.0), (10.0, 100.0)])
    r = query_operating_point(curve, 2.5)
    assert r["status"] == "ok"
    assert r["value"] == pytest.approx(25.0)
    assert r["guarantee"] is False
    assert r["evidence_class"] == "typical"
    assert "typical" in r["typical_note"]


def test_r3_no_extrapolation_either_side():
    curve = _linear_curve([(1.0, 10.0), (10.0, 100.0)])
    for x in (0.5, 10.5, 100.0):
        r = query_operating_point(curve, x)
        assert r["status"] == "out_of_range", x
        assert "extrapolation" in r["reason"]


def test_r3_resolution_rides_the_result():
    curve = _linear_curve([(0.0, 0.0), (5.0, 50.0), (10.0, 100.0)])
    r = query_operating_point(curve, 2.0)
    assert r["uncertainty"]["resolution"] == pytest.approx(5.0)
    assert r["uncertainty"]["local_sample_spacing"] == pytest.approx(5.0)
    assert r["uncertainty"]["distance_to_nearest_sample"] == \
        pytest.approx(2.0)


def test_r3_conditions_gate_the_query(efficiency_curve):
    r = query_operating_point(
        efficiency_curve, 10.0,
        required_conditions={"vin_v": 24.0, "vout_v": 1.1},
    )
    assert r["status"] == "not_comparable"
    assert r["compatibility"]["mismatched"][0]["key"] == "vin_v"


def test_r3_log_scale_interpolation():
    curve = _linear_curve(
        [(0.01, 1.0), (1.0, 3.0), (100.0, 5.0)], scale="log10"
    )
    r = query_operating_point(curve, 0.1)
    assert r["status"] == "ok"
    assert r["value"] == pytest.approx(2.0)  # midway in log10 space


# --- R4: derived decision evidence -----------------------------------------------


def test_r4_comparison_is_a_cited_proposal(tps_curves):
    efficiency = [c for c in tps_curves if c.y_kind == "efficiency"]
    result = compare_at_operating_point(
        efficiency, 10.0,
        required_conditions={
            "vin_v": 12.0, "vout_v": 1.1,
            "categorical": {"mode": "fccm"},
        },
    )
    assert result["status"] == "proposal"
    assert result["method"]
    assert len(result["entries"]) == 8
    assert len(result["rejected"]) == 4  # the DCM figures
    for entry in result["entries"]:
        assert entry["citation"]["document_sha256"]
        assert entry["evidence_class"] == "typical"
        assert entry["uncertainty"]["resolution"] is not None
    unpinned = result["condition_dimensions_unpinned"]
    assert unpinned and "categorical.vcc" in unpinned
    assert "promotion" in result["note"]


def test_r4_load_distribution_weighted_and_bounded():
    curve = _linear_curve([(0.0, 0.0), (10.0, 100.0)])
    ok = evaluate_load_distribution(
        curve, [{"x": 2.0, "weight": 1.0}, {"x": 8.0, "weight": 3.0}]
    )
    assert ok["status"] == "proposal"
    assert ok["weighted_mean"] == pytest.approx(65.0)
    assert ok["guarantee"] is False
    refused = evaluate_load_distribution(
        curve, [{"x": 2.0, "weight": 1.0}, {"x": 20.0, "weight": 1.0}]
    )
    assert refused["status"] == "out_of_range", \
        "a profile past the support is refused whole, never truncated"


# --- R5: relevance ----------------------------------------------------------------


def test_r5_efficiency_curve_tagged_for_light_load(efficiency_curve):
    tags = efficiency_curve.relevance
    assert tags and tags[0]["phenomenon"] == "efficiency_vs_load"
    assert "light_load_efficiency" in tags[0]["use_cases"]
    assert "vin_v" in tags[0]["engineer_params"] or \
        "input voltage" in " ".join(tags[0]["engineer_params"])
    assert tags[0]["supported_region"] == efficiency_curve.supported_region


def test_r5_sufficiency_requires_engineer_conditions(efficiency_curve):
    assert condition_sufficiency(efficiency_curve, {}) == \
        ["vin_v", "vout_v"]
    assert condition_sufficiency(
        efficiency_curve, {"vin_v": 12.0, "vout_v": 1.1}) == []


# --- R7: provider contract ---------------------------------------------------------


def test_provider_query_contract(tps_curves):
    response = query_curve_evidence(
        tps_curves,
        phenomenon="efficiency_vs_load",
        operating_point={"x": 10.0},
        conditions={
            "vin_v": 12.0, "vout_v": 1.1, "categorical": {"mode": "fccm"},
        },
    )
    assert response["schema"] == "harness.discovery-curve-evidence-provider.v1"
    assert response["promotion"] == "none"
    assert len(response["results"]) == 8
    ok = response["results"][0]
    for key in ("curve_id", "value", "unit", "guarantee",
                "evidence_class", "matched_conditions", "supported_region",
                "uncertainty", "citation"):
        assert key in ok
    assert response["not_usable"] and response["not_usable"][0]["reason"]
    assert response["derived"]["status"] == "proposal"


def test_provider_insufficient_conditions_named(tps_curves):
    response = query_curve_evidence(
        tps_curves,
        phenomenon="efficiency_vs_load",
        operating_point={"x": 10.0},
        conditions={"vout_v": 3.3},
    )
    assert response["results"] == []
    assert len(response["insufficient_conditions"]) == 12
    assert response["insufficient_conditions"][0]["missing_required_keys"] \
        == ["vin_v"]


def test_provider_use_case_discovery(tps_curves):
    response = query_curve_evidence(
        tps_curves,
        use_case="light_load_efficiency",
        conditions={"vin_v": 12.0, "vout_v": 1.1},
    )
    assert response["applicable"] == 12
    listed = response["results"]
    assert all(r["status"] == "applicable" for r in listed)
    assert {r["part"] for r in listed} == {"TPS548C26"}
    light = [
        r for r in listed
        if r["supported_region"]["min"] <= 1.0
    ]
    assert light, "curves whose support reaches light load are discoverable"
