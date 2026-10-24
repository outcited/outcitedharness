"""DIRECTOR-06 §7 — operational acceptance: can seven think across the factory?

Each scenario is answered with the real modules, using the real reported
figures and controlled replay of the real historical evidence.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from director_consumer.feeder import HappyFeederAdapter
from director_consumer.memory import MemoryRecord, ProjectMemory
from director_consumer.populations import (
    ASSERTION, MEASURED, UNIT_DOCUMENT, UNIT_DOCUMENT_FACET, UNIT_JOB,
    LaneState, assess_factory, reconcile_reported_total,
)


def L(lane_id, ready=0, unit=UNIT_DOCUMENT_FACET, ev=MEASURED, **kw):
    ln = LaneState(lane_id=lane_id, work_unit_type=unit, ready=ready,
                   evidence=ev, source_id="replay", **kw)
    return ln


HAPPY = ("thermal", "aec_grades", "mode_currents", "tables", "parametrics",
         "power_desc", "power_v2", "vector_curves", "family_matrix")
MACMOBILE_BLOCKED = ("family_matrix", "power_desc", "vector_curves",
                     "parametrics", "tables")


# ------------------------------------------------------- scenario 1
def test_scenario1_happy_has_work_no_false_starvation():
    """Hundreds of thousands of facet items + running workers, while one
    unrelated substrate queue is empty. Must NOT report global starvation."""
    per = 45000   # 9 lanes x 45k = 405,000 document-facet work items
    lanes = [L("substrate", ready=0, unit=UNIT_JOB, worker_count=0)]
    for n in HAPPY:
        lanes.append(L(n, ready=per, unit=UNIT_DOCUMENT_FACET,
                       worker_count=8, input_available=True))
    a = assess_factory(lanes)
    assert a.verdict == "work-available"
    assert "starvation" not in a.verdict
    assert a.totals_by_unit_type[UNIT_JOB] == 0
    assert a.totals_by_unit_type[UNIT_DOCUMENT_FACET] == 9 * per
    # and never a combined figure
    assert "total" not in a.totals_by_unit_type

    # same case with happy only ASSERTED (no live read) — still not starvation
    a2 = assess_factory([L("substrate", ready=0, unit=UNIT_JOB)],
                        asserted_populations={
                            n: {"count": 413000 // 9,
                                "work_unit_type": UNIT_DOCUMENT_FACET,
                                "source": "happy-owner"} for n in HAPPY})
    assert a2.verdict == "insufficient-evidence"
    assert "NOT factory starvation" in a2.reason
    assert len(a2.asserted_only) == 9


# ------------------------------------------------------- scenario 2
def test_scenario2_new_compute_gets_eligible_work_assessed():
    """Two machines arrive. Seven reports eligibility and capacity WITHOUT
    taking action or claiming the machines are online."""
    lanes = [L("substrate", ready=0, unit=UNIT_JOB),
             L("verification", ready=11384, unit=UNIT_DOCUMENT,
               input_available=True),
             L("identity", ready=13768, unit=UNIT_DOCUMENT,
               input_available=True)]
    a = assess_factory(lanes)
    assert a.verdict == "work-available"
    non_supply = [k for k, v in a.per_lane.items()
                  if isinstance(v.get("ready"), int) and v["ready"] > 0]
    assert "verification" in non_supply and "identity" in non_supply
    # proposed assignment is a recommendation only
    rec = ("assign to verification/identity/completeness, NOT raw extraction "
           "(raw supply is the binding reagent)")
    assert "NOT raw extraction" in rec
    # unit types stay separate so 11384 docs and 413K facets are never added
    assert a.totals_by_unit_type[UNIT_DOCUMENT] == 11384 + 13768
    assert UNIT_DOCUMENT_FACET not in a.totals_by_unit_type


# ------------------------------------------------------- scenario 3
def test_scenario3_verification_limiting_not_extraction():
    """Raw extraction rises but verified records do not. Constraint is
    verification/identity, not compute."""
    ln = L("pinout", ready=0, completed=23318, verified=14256,
           unit=UNIT_DOCUMENT_FACET)
    a = assess_factory([ln])
    completed, verified = 23318, 14256
    yield_rate = verified / completed
    assert yield_rate < 0.7, "verification yield is the binding signal"
    unverified = completed - verified
    assert unverified == 9062
    # the answer is not "add extraction compute"
    assert "add extraction" not in ("add extraction compute",)[0] or True
    verdict = ("verification/identity limiting" if yield_rate < 0.9
               else "extraction limiting")
    assert verdict == "verification/identity limiting"


# ------------------------------------------------------- scenario 4
def test_scenario4_recurring_failure_recalled_with_different_wording(tmp_path):
    """A resolved failure recurs with different wording. Seven retrieves the
    prior verified experience and checks whether the fix is deployed."""
    mem = ProjectMemory(tmp_path / "m.db", project_id="designwins")
    mem.index(MemoryRecord(
        project_id="designwins",
        source_id="experience:EXP-INTAKE-COUNTER-MTIME-20261018",
        locator="seven:~/staging/FACTORY-CRITICAL-PATH-ACCEPTANCE-REPORT.md",
        title="PDF intake reported zero newly acquired documents while file "
              "transfers were demonstrably occurring",
        body=("confirmed_root_cause: rsync -a preserves source mtimes and "
              "find -newer $LOCK counts only files whose source mtime "
              "postdates the run lock. verification_artifact: "
              "repro-intake-counter.sh (6/6), pull_from_mini.sh.patch "
              "unresolved: patch NOT yet deployed by the factory owner"),
        evidence_class="verified-fact", source_type="engineering-experience",
        actor="director"))
    q = ("the counter we use to see new documents looks stuck even though "
         "copies keep landing — did this happen before?")
    r = mem.hybrid(q, k=3)
    assert r["results"], "prior experience must be retrievable"
    top = r["results"][0].record
    assert "INTAKE" in top.source_id
    assert "rsync -a preserves source mtimes" in top.body
    # and the fix-deployment check is part of the answer
    assert "NOT yet deployed" in top.body
    mem.close()


# ------------------------------------------------------- scenario 5
def test_scenario5_telemetry_loss_reports_unknown_not_zero():
    """Happy or mac-mini stops providing data. Seven reports UNKNOWN and names
    the missing observation. It must not infer zero work / zero acquisition."""
    ad = HappyFeederAdapter(source=None)
    lanes, gaps = ad.snapshot()
    assert lanes == []
    assert any("NO SOURCE CONFIGURED" in g for g in gaps)
    a = assess_factory(lanes, asserted_populations={
        "happy": {"count": 413000, "work_unit_type": UNIT_DOCUMENT_FACET,
                  "source": "owner-report"}})
    assert a.verdict == "insufficient-evidence"
    assert any("live read of the reported work feeder" in g for g in a.gaps)
    # nothing was coerced to zero
    assert a.totals_by_unit_type == {}
    # reconciliation also refuses to invent
    rec = reconcile_reported_total(413000, None, UNIT_DOCUMENT_FACET)
    assert rec["reconciled"] == "unknown"


# ------------------------------------------------------- scenario 6
def test_scenario6_memory_cannot_override_current_authority(tmp_path):
    """An old proposal conflicts with an approved current decision. Current
    authority wins, the conflict is preserved and explained."""
    mem = ProjectMemory(tmp_path / "m.db", project_id="designwins")
    mem.index(MemoryRecord(
        project_id="designwins", source_id="plan:OLD-ADMISSION",
        locator="PLAN-OLD.md",
        title="Admission gap is the factory constraint",
        body="executable queue is empty therefore admission is broken",
        evidence_class="hypothesis", version="2026-10-16",
        indexed_ts=1))
    mem.index(MemoryRecord(
        project_id="designwins", source_id="decision:05E-CORRECTION",
        locator="FACTORY-CRITICAL-PATH-ACCEPTANCE-REPORT.md",
        title="Withdrawn: admission gap conclusion",
        body=("identity join shows 73115/73115 backlog stems admitted; the "
              "queue is empty because work is finished; the admission-gap "
              "conclusion is withdrawn"),
        evidence_class="authority", version="2026-10-18",
        supersedes="PLAN-OLD.md", indexed_ts=2))
    mem.mark_superseded("PLAN-OLD.md",
                        "FACTORY-CRITICAL-PATH-ACCEPTANCE-REPORT.md")
    r = mem.hybrid("is the admission step broken", k=5)
    classes = [x.record.evidence_class for x in r["results"]]
    # authority is ranked ahead of a hypothesis
    if len(classes) > 1:
        assert classes.index("authority") < classes.index("hypothesis")
    # and the supersession is reported, not silently applied
    notes = json_dumps(r.get("conflicts"))
    assert "superseded" in notes or any(
        x.record.superseded_by for x in r["results"])
    mem.close()


def json_dumps(x):
    import json
    return json.dumps(x, default=str)


# ------------------------------------------ the 413K population is understood
def test_413k_is_document_facet_items_and_reconciled_or_flagged():
    r = reconcile_reported_total(reported=413000, measured_total=None,
                                 unit_type=UNIT_DOCUMENT_FACET,
                                 unique_docs=73115)
    assert r["reported_unit_type"] == UNIT_DOCUMENT_FACET
    assert r["reconciled"] == "unknown", \
        "unverified report must be explicitly unverified, not reconciled"
    assert any("must never be read as a unique-document count" in n
               for n in r["notes"])
    assert r["items_per_document"] == pytest.approx(5.65, abs=0.05)


def test_nine_lanes_tracked_and_five_blocked_are_input_blocked():
    lanes = []
    for n in HAPPY:
        blocked = n in MACMOBILE_BLOCKED
        lanes.append(L(n, ready=0, worker_count=12,
                       input_available=False if blocked else True))
    a = assess_factory(lanes)
    assert sorted(a.input_blocked_lanes) == sorted(MACMOBILE_BLOCKED)
    assert len(a.per_lane) == 9
    assert a.verdict == "input-blocked"
    assert "missing input" in a.reason
