"""DIRECTOR-05E — supply-intelligence correctness.

These tests demonstrate that the FALSE conclusions previously produced are
now prevented:
  * rust chunks + python jobs summed into one invented document count
  * "admission-gap" concluded from a zero counter in one queue
  * an empty inspected queue reported as "all queues empty / workers idle"
  * an incomplete L0 join converted into proof of zero eligibility
  * a cumulative inventory counter reported as newly-acquired documents
  * done-ledger completions reported as verified output
  * producer observation time conflated with mirror/copy time
  * critical missing sources left unnamed
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from director_consumer.continuity import ContinuityMeasurements, FactoryContinuity
from director_consumer.funnel import UNKNOWN, build_funnel


class M:
    """Minimal measurement stand-in matching the funnel's contract."""
    def __init__(self, **kw):
        self.acquisition_files = kw.get("acquired", 66994)
        self.rust_queued = kw.get("queued", 0)
        self.substrate_pending = kw.get("pending", 0)
        self.rust_claimed = kw.get("claimed", 0)
        self.ledger_done_total = kw.get("done", 5200)
        self.ready_by_class = kw.get("by_class", {"datasheet": 1060})
        self.eligible_measured = kw.get("eligible_measured", True)


# ---------------------------------------------------------------- C: no sum

def test_rust_and_python_queues_are_never_summed():
    """Two lanes with different populations must NOT produce one total."""
    f = build_funnel(M(queued=100, pending=7))
    assert f.queued_by_lane == {"rust_chunks": 100, "substrate_jobs": 7}
    assert f.queued == UNKNOWN, \
        "rust chunks + python jobs must never be summed into a doc count"
    assert any("never summed" in n for n in f.notes)


def test_single_lane_still_reports_a_count():
    f = build_funnel(M(queued=42, pending="unknown"))
    assert f.queued_by_lane["substrate_jobs"] == UNKNOWN
    assert f.queued == 42, "a single observable lane may report its own count"


def test_both_lanes_zero_is_a_measured_zero_not_unknown():
    f = build_funnel(M(queued=0, pending=0))
    assert f.queued_by_lane == {"rust_chunks": 0, "substrate_jobs": 0}
    assert f.queued == UNKNOWN, "two different zero populations are not one 0"


# ------------------------------------------------- admission from a zero

def test_zero_counter_alone_never_proves_admission_gap():
    """THE false conclusion: queue empty + eligible inventory => admission-gap.
    Without a measured identity join this is insufficient-evidence."""
    f = build_funnel(M(queued=0, pending=0))
    assert f.holdup != "admission-gap"
    assert f.holdup == "insufficient-evidence"
    assert any("does not establish that admission is broken" in e
               for e in f.holdup_evidence)
    assert any("identity join" in e for e in f.holdup_evidence)


def test_unobserved_lane_prevents_all_queues_empty_claim():
    """An empty inspected queue is not all queues empty."""
    f = build_funnel(M(queued="unknown", pending=0))
    assert f.holdup == "insufficient-evidence"
    assert any("unobserved" in e for e in f.holdup_evidence), \
        "the unobserved lane must be named"


def test_measured_non_overlap_is_what_proves_admission_gap():
    """The one case where admission-gap is legitimate: measured identity
    join shows inventory absent from the admitted work-unit set."""
    f = build_funnel(M(queued=0, pending=0),
                     admission_overlap=(12, 100),
                     admission_overlap_measured=True)
    assert f.holdup == "admission-gap"
    assert any("12 of 100" in e for e in f.holdup_evidence)


def test_full_admission_overlap_rules_out_admission_gap():
    """When every inventory document IS admitted, zero queue = finished work,
    not an admission failure (the 05E finding)."""
    f = build_funnel(M(queued=0, pending=0),
                     admission_overlap=(73115, 73115),
                     admission_overlap_measured=True)
    assert f.holdup == "insufficient-evidence", \
        "100% admitted inventory can never be an admission-gap"


def test_queues_unavailable_is_not_a_gap():
    m = M()
    m.rust_queued = UNKNOWN
    m.substrate_pending = UNKNOWN
    f = build_funnel(m)
    assert f.holdup == "queues-unavailable"
    assert any("absence of telemetry" in e for e in f.holdup_evidence)


# ------------------------------------------------------- eligibility zero

def test_incomplete_l0_join_is_never_zero_eligibility():
    m = M(by_class={}, eligible_measured=False)
    f = build_funnel(m)
    assert f.eligible == UNKNOWN
    assert f.eligible_measured is False
    assert any("L0 census" in s for s in f.missing_sources)


def test_measured_zero_eligibility_is_allowed():
    f = build_funnel(M(by_class={}, eligible_measured=True))
    assert f.eligible == 0
    assert f.eligible_measured is True


# ------------------------------------------------------------ acquisition

def test_cumulative_counter_is_not_newly_acquired():
    f = build_funnel(M(acquired=66994))
    assert f.acquired_inventory_total == 66994
    assert f.acquired_new == UNKNOWN
    assert any("reliable new-acquisition counter" in s
               for s in f.missing_sources)
    assert any("CUMULATIVE" in n for n in f.notes)


def test_reliable_new_counter_is_honoured():
    f = build_funnel(M(acquired=66994), acquired_new=17)
    assert f.acquired_new == 17
    assert f.acquired_inventory_total == 66994


# --------------------------------------------------------- verification

def test_done_ledger_is_not_verification():
    f = build_funnel(M(done=5200))
    assert f.lane_done_records == 5200
    assert f.completed_verified == UNKNOWN, \
        "facet done-ledger entries are completions, not verified output"
    assert any("verification source" in s for s in f.missing_sources)


def test_verified_count_is_honoured_when_supplied():
    f = build_funnel(M(done=5200), verified_count=210193)
    assert f.completed_verified == 210193


# ------------------------------------------- producer vs mirror freshness

def test_producer_and_mirror_times_are_separate():
    f = build_funnel(M(), producer_times={"pdf-intake.log": "2026-10-17T23:36:11Z"},
                     mirror_times={"pdf-intake.log": "2026-10-18T00:36:38Z"})
    assert f.producer_times != f.mirror_times
    assert f.producer_times["pdf-intake.log"] == "2026-10-17T23:36:11Z"
    assert f.mirror_times["pdf-intake.log"] == "2026-10-18T00:36:38Z"


# -------------------------------------------------- missing source flags

def test_critical_missing_sources_are_named_not_silent():
    f = build_funnel(M(by_class={}, eligible_measured=False))
    joined = " ".join(f.missing_sources)
    assert "validation ledger" in joined
    assert "verification source" in joined
    assert "L0 census" in joined
    assert "new-acquisition counter" in joined


# ------------------------------------------------- queue_empty event text

def _m_with_lanes(rust, sub):
    m = ContinuityMeasurements()
    m.rust_queued = rust
    m.substrate_pending = sub
    m.ready_backlog_docs = 0
    m.starvation_basis = "backlog-zero"
    m.lane_counts = {"rust_chunks": rust, "substrate_jobs": sub}
    m.junk_dead_jobs = 0
    m.stalled_leases = "unknown"
    m.acquisition_rate_docs_day = 10
    return m


def test_queue_empty_event_never_claims_all_queues_from_one_zero():
    fc = FactoryContinuity()
    m = _m_with_lanes(0, "unknown")     # one lane observed empty, one not
    kinds = {e.kind: e for e in fc.events(m)}
    assert "queue_empty" in kinds
    text = kinds["queue_empty"].summary
    assert "does NOT establish that all queues are empty" in text
    assert "unobserved" in text
    assert "workers will idle" not in text, \
        "the idle prophecy is withheld while a lane is unobserved"
    assert kinds["queue_empty"].attrs.get("severity_hint") == "P2"


def test_queue_empty_event_states_scope_when_all_lanes_observed():
    fc = FactoryContinuity()
    m = _m_with_lanes(0, 0)             # both lanes observed
    kinds = {e.kind: e for e in fc.events(m)}
    text = kinds["queue_empty"].summary
    assert "all observed execution queues empty" in text
    assert "eligible inventory measured at zero" in text
