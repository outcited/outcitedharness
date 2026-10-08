"""CURVE-03 experiment acceptance tests (Phases 2-4)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.run_curve_discovery_experiments import run

LEDGER = Path("results/curve-discovery-experiments/ledger.json")


@pytest.fixture(scope="module")
def ledger():
    return run()


def _walk(node):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def test_acceptance_no_false_hard_eliminations(ledger):
    for name, exp in ledger["experiments"].items():
        for fam, verdict in exp["A_parametric"].items():
            assert verdict in ("PASS", "FAIL", "UNKNOWN")
    acc = ledger["acceptance"]
    assert acc["false_hard_eliminations_from_curve_evidence"] == 0
    assert acc["typical_as_guarantee_usages"] == 0
    assert acc["provenance_complete"] is True


def test_acceptance_typical_never_guaranteed(ledger):
    for node in _walk(ledger["experiments"]):
        for key in ("guarantee",):
            if key in node:
                assert node[key] is False
        if node.get("evidence_class"):
            assert node["evidence_class"] in ("typical", "unspecified")


def test_every_number_carries_citation(ledger):
    cited = 0
    for node in _walk(ledger["experiments"]):
        if any(isinstance(node.get(k), (int, float)) for k in
               ("value", "input_power_w", "energy_wh_per_day",
                "current_a", "eta_pct")):
            assert node.get("curve_id") or node.get("citation"), node
            cited += 1
    assert cited >= 5, "experiments must produce cited quantitative output"


def test_intent_a_light_load_quantified_and_refused_honestly(ledger):
    exp = ledger["experiments"]["A_light_load"]
    sleep = exp["C_curve"]["sleep_power"]
    ok_sleep = [s for s in sleep if s["status"] == "ok"]
    assert ok_sleep, "12 V standby must be quantified from printed curves"
    assert all(s["citation"]["page_1based"] for s in ok_sleep)
    energy = exp["C_curve"]["duty_energy"]
    assert "active" in energy["segments_answered"]
    ok_active = [
        pc for e in energy["energy_wh_per_day_by_curve"]
        for pc in e.get("per_curve", [])
    ]
    assert ok_active and all(pc["citation"] for pc in ok_active)
    # eligibility: curves changed nothing hard — parametric verdicts intact
    assert exp["A_parametric"]["TPS628302"] == "FAIL"
    assert exp["A_parametric"]["SiC461-464"] == "UNKNOWN"


def test_intent_b_high_load_distinguishes_modes_with_citations(ledger):
    exp = ledger["experiments"]["B_high_load"]
    eff = exp["C_curve"]["efficiency_at_30A"]["ok"]
    dis = exp["C_curve"]["dissipation_at_30A"]["ok"]
    assert eff and dis, "30 A efficiency and dissipation must be answered"
    assert all(r["citation"]["document_sha256"] for r in eff + dis)
    assert exp["C_curve"]["unresolved"], \
        "thermal unknowns must be listed, not guessed away"
    assert exp["A_parametric"]["TPS548C26"] == "PASS"


def test_intent_c_tradeoff_refuses_every_comparison(ledger):
    exp = ledger["experiments"]["C_tradeoff"]
    eff = exp["C_curve"]["efficiency_at_24V_5V_1A"]
    # CURVE-05B correction: positional legend binding recovered the
    # vendor's printed 24 V-input traces, so SiC46x genuinely answers at
    # 24 V -> 5 V now; every non-SiC family still refuses
    ok_parts = {r["part"] for r in eff["ok"]}
    assert ok_parts and ok_parts <= {"SiC461", "SiC462", "SiC463",
                                     "SiC464"}, \
        "only legend-bound SiC46x 24 V traces answer"
    assert all(r["citation"]["document_sha256"] for r in eff["ok"])
    assert eff["not_usable"], "refusals still recorded with reasons"
    assert exp["priority_rankings"]["efficiency_first"]


def test_ledger_written_and_schema_stamped(ledger):
    assert LEDGER.exists()
    written = json.loads(LEDGER.read_text())
    assert written["schema"] == "harness.electronics-curve-discovery-experiments.v1"
    assert written["cohort_families"] >= 8
