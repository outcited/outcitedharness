"""Frozen curve-evidence pilot evaluation in CI (PRD-CURVE-02 R6).

Runs the pilot scorer over the frozen fixtures and asserts the acceptance
gates, so a regression in the decision-grade layer fails tests instead of
silently drifting. The extraction metrics (vision predictions vs the
references) run out-of-band with --predictions and are not asserted here.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from scripts.score_curve_evidence_pilot import (
    GATES,
    score_probes,
    score_reference_integrity,
)
from harness.discovery.curves import load_reference_curves

PILOT = Path(__file__).parent / "fixtures" / "gold" / "curve_evidence_pilot"
MCU = Path(__file__).parent / "fixtures" / "gold" / "typical_curves"


def _curves():
    curves = load_reference_curves(sorted(PILOT.glob("*.json")))
    for path in sorted(MCU.glob("*.json")):
        if not path.name.startswith("_"):
            curves.extend(load_reference_curves([path]))
    return curves


def test_pilot_has_at_least_30_hand_reviewed_curves():
    spec = json.loads((PILOT / "_probes.json").read_text())
    records = {
        page["file"]: json.loads((PILOT / page["file"]).read_text())
        for page in spec["pages"]
    }
    reviewed = sum(
        len(p.get("series") or [])
        for record in records.values()
        for p in record.get("plots") or []
    )
    assert reviewed >= 30, f"frozen pilot must keep >=30 curves, has {reviewed}"


def test_pilot_reference_integrity_gates():
    spec = json.loads((PILOT / "_probes.json").read_text())
    records = {
        page["file"]: json.loads((PILOT / page["file"]).read_text())
        for page in spec["pages"]
    }
    integrity = score_reference_integrity(spec, records)
    # missing printed figures are ALLOWED and counted (coverage gate);
    # wrong extractions are not (precision + axis/series gates)
    hard_errors = [
        m for m in integrity["misses"]
        if "not extracted" not in m and "missing" not in m
    ]
    assert hard_errors == [], hard_errors
    assert integrity["plot_precision"] >= GATES["plot_precision"]
    assert integrity["plot_coverage"] >= GATES["plot_coverage_min"]
    assert integrity["axis_unit_correctness"] >= GATES["axis_unit_correctness"]
    assert integrity["series_identification_accuracy"] >= \
        GATES["series_identification_accuracy"]
    assert integrity["numeric_error"]["x_fit_residual_pct_max"] < \
        GATES["fit_residual_pct_max"]
    assert integrity["numeric_error"]["y_fit_residual_pct_max"] < \
        GATES["fit_residual_pct_max"]


def test_pilot_decision_grade_gates():
    spec = json.loads((PILOT / "_probes.json").read_text())
    probes = score_probes(spec, _curves())
    assert probes["failures"] == []
    assert probes["condition_classification_accuracy"] >= \
        GATES["condition_classification_accuracy"]
    assert probes["false_comparability_rate"] <= \
        GATES["false_comparability_rate_max"]
    assert probes["out_of_range_correct_rejection"] >= \
        GATES["out_of_range_correct_rejection"]
    assert probes["must_reject_probes"] >= 5
    assert probes["out_of_range_probes"] >= 3


def test_pilot_scorer_exit_zero(monkeypatch, capsys):
    from scripts import score_curve_evidence_pilot as scorer

    monkeypatch.setattr(scorer.sys, "argv", ["score_curve_evidence_pilot"])
    assert scorer.main() == 0
    out = capsys.readouterr().out
    assert "false_comparability_rate" in out
