"""DIRECTOR-06 — factory population model + happy feeder adapter.

The two laws that decide whether the Director understands the factory:
  1. A document-facet work item is NOT a document, and units are never summed.
  2. An empty substrate queue is never factory starvation while another
     population is reported or another lane is unobserved.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from director_consumer.feeder import HappyFeederAdapter, assert_only_reported
from director_consumer.populations import (
    ASSERTION, MEASURED, UNIT_DOCUMENT, UNIT_DOCUMENT_FACET, UNIT_JOB,
    FactoryAssessment, LaneState, assess_factory, reconcile_reported_total,
)


def L(lane_id, ready=0, unit=UNIT_DOCUMENT_FACET, ev=MEASURED,
      workers=None, inp=None, **kw):
    ln = LaneState(lane_id=lane_id, work_unit_type=unit, ready=ready,
                   evidence=ev, worker_count=workers, input_available=inp,
                   source_id="test")
    for k, v in kw.items():
        setattr(ln, k, v)
    return ln


# --------------------------------------------------------- unit discipline

def test_document_and_document_facet_never_summed():
    """413K document-facet items must never read as 413K documents."""
    lanes = [L("thermal", ready=100, unit=UNIT_DOCUMENT_FACET),
             L("substrate", ready=7, unit=UNIT_DOCUMENT)]
    a = assess_factory(lanes)
    assert a.totals_by_unit_type[UNIT_DOCUMENT_FACET] == 100
    assert a.totals_by_unit_type[UNIT_DOCUMENT] == 7
    assert "document-facet" not in str(a.totals_by_unit_type.get(UNIT_DOCUMENT))
    assert a.unsummed_note
    # there is no single combined total anywhere
    assert not any(k == "total" for k in a.totals_by_unit_type)


def test_reported_413k_is_facet_items_not_documents():
    r = reconcile_reported_total(reported=413000, measured_total=None,
                                 unit_type=UNIT_DOCUMENT_FACET,
                                 unique_docs=73115)
    assert r["reported_unit_type"] == UNIT_DOCUMENT_FACET
    assert any("never be read as a unique-document count" in n
               for n in r["notes"])
    assert r["items_per_document"] == pytest.approx(5.65, abs=0.1)
    assert r["reconciled"] == "unknown", \
        "unmeasured side must not be reconciled by assumption"


# ------------------------------------------------- no false starvation

def test_empty_substrate_with_reported_population_is_NOT_starvation():
    """THE headline case: pipeline.jobs.pending=0 while happy reports ~413K."""
    lanes = [L("substrate", ready=0, unit=UNIT_JOB)]
    asserted = {"happy": {"count": 413000, "work_unit_type":
                          UNIT_DOCUMENT_FACET, "source": "owner-report"}}
    a = assess_factory(lanes, asserted_populations=asserted)
    assert a.verdict != "starvation"
    assert a.verdict == "insufficient-evidence"
    assert "NOT factory starvation" in a.reason
    assert any("NOT independently verified" in x for x in a.asserted_only)
    assert any("live read of the reported work feeder" in g for g in a.gaps)


def test_empty_substrate_with_unobserved_lane_is_not_starvation():
    a = assess_factory([L("substrate", ready=0, unit=UNIT_JOB),
                        L("thermal", ready=None, ev=ASSERTION)])
    assert a.verdict == "insufficient-evidence"
    assert "unobserved" in a.reason
    assert "does not establish that all queues are empty" in a.reason


def test_starvation_word_never_appears_from_partial_data():
    for lanes, asserted in (
            ([L("substrate", ready=0, unit=UNIT_JOB)], {"x": {"count": 10}}),
            ([L("a", ready=0), L("b", ready=None, ev=ASSERTION)], None),
            ([L("a", ready=0), L("b", ready=0)], None)):
        a = assess_factory(lanes, asserted_populations=asserted)
        assert a.verdict != "starvation"
        assert any("starvation' is not asserted" in n for n in a.notes)


def test_work_available_when_any_lane_has_ready():
    a = assess_factory([L("substrate", ready=0, unit=UNIT_JOB),
                        L("vector_curves", ready=3400, unit=UNIT_DOCUMENT_FACET)])
    assert a.verdict == "work-available"
    assert "vector_curves" in a.reason


# --------------------------------------------- input-blocked ≠ starved

def test_macmobile_blocked_lanes_are_input_blocked_not_compute_starved():
    lanes = [L("family_matrix", ready=0, workers=12, inp=False),
             L("thermal", ready=0, workers=12, inp=True)]
    a = assess_factory(lanes)
    assert "family_matrix" in a.input_blocked_lanes
    assert a.verdict == "input-blocked"
    assert "missing input" in a.reason
    assert any("INPUT-BLOCKED" in n and "not compute starvation" in n
               for n in a.notes)
    assert "family_matrix" not in a.compute_starved_lanes


# ------------------------------------------------ assertion discipline

def test_assertions_never_become_measurements():
    recs = assert_only_reported({
        "thermal": {"count": 52000, "work_unit_type": "document-facet",
                    "source": "happy-owner"}})
    assert recs[0]["evidence"] == ASSERTION
    assert recs[0]["source_id"] == "happy-owner"
    # and the assessment keeps them out of measured totals
    a = assess_factory([], asserted_populations={
        "thermal": {"count": 52000, "work_unit_type": "document-facet",
                    "source": "happy-owner"}})
    assert a.totals_by_unit_type == {}, \
        "asserted counts must never enter measured totals"


def test_missing_source_yields_gap_not_zero_factory(tmp_path):
    ad = HappyFeederAdapter(source=None)
    lanes, gaps = ad.snapshot()
    assert lanes == []
    assert any("NO SOURCE CONFIGURED" in g for g in gaps)
    assert any("not an empty factory" in g for g in gaps)

    ad2 = HappyFeederAdapter(source=str(tmp_path / "nope.json"))
    lanes2, gaps2 = ad2.snapshot()
    assert lanes2 == []
    assert any("remains ASSERTIONS" in g for g in gaps2)


def test_unsupported_shape_is_refused_not_guessed(tmp_path):
    p = tmp_path / "feed.yaml"
    p.write_text("lanes: []")
    ad = HappyFeederAdapter(source=str(p))
    lanes, gaps = ad.snapshot()
    assert lanes == []
    assert any("unsupported source shape" in g for g in gaps)
    assert any("refusing to guess a schema" in g for g in gaps)


# --------------------------------------------------- schema tolerance

def test_only_available_fields_are_populated(tmp_path):
    p = tmp_path / "feeder.jsonl"
    p.write_text(json.dumps({
        "lane_id": "thermal", "ready": 120, "running": 4,
        "work_unit_type": "document-facet"}) + "\n")
    ad = HappyFeederAdapter(source=str(p))
    lanes, gaps = ad.snapshot()
    assert not gaps
    ln = lanes[0]
    assert ln.ready == 120 and ln.running == 4
    assert ln.completed == "unknown" and ln.verified == "unknown", \
        "absent fields must stay UNKNOWN, never 0"
    assert ln.evidence == MEASURED


def test_alias_vocabulary_is_mapped(tmp_path):
    p = tmp_path / "f.jsonl"
    p.write_text(json.dumps({"lane": "tables", "queued": 9, "workers": 3,
                             "heartbeat": 1792300000}) + "\n")
    ad = HappyFeederAdapter(source=str(p))
    lanes, _ = ad.snapshot()
    assert lanes[0].lane_id == "tables"
    assert lanes[0].ready == 9
    assert lanes[0].worker_count == 3
    assert lanes[0].feeder_heartbeat == 1792300000


def test_work_items_dedup_on_document_facet_version(tmp_path):
    p = tmp_path / "items.jsonl"
    rows = [
        {"lane_id": "thermal", "source_sha": "abc", "facet": "thermal",
         "extraction_version": "v1", "state": "ready"},
        {"lane_id": "thermal", "source_sha": "abc", "facet": "thermal",
         "extraction_version": "v1", "state": "ready"},   # duplicate
        {"lane_id": "thermal", "source_sha": "abc", "facet": "thermal",
         "extraction_version": "v2", "state": "ready"},   # new version
    ]
    p.write_text("\n".join(json.dumps(r) for r in rows))
    ad = HappyFeederAdapter(source=str(p))
    lanes, _ = ad.snapshot()
    assert lanes[0].ready == 2, "same (doc, facet, version) counted once"
    assert ad.duplicates == 1


def test_lane_rollup_and_work_items_can_coexist(tmp_path):
    p = tmp_path / "mix.jsonl"
    p.write_text(json.dumps({"lane_id": "tables", "ready": 100,
                             "work_unit_type": "document-facet"}) + "\n")
    ad = HappyFeederAdapter(source=str(p))
    lanes, _ = ad.snapshot()
    assert lanes[0].ready == 100


# ------------------------------------------------------- diagnostics

def test_diagnostics_detect_zero_workers_with_ready_work(tmp_path):
    p = tmp_path / "d.jsonl"
    p.write_text(json.dumps({"lane_id": "power_v2", "ready": 400,
                             "worker_count": 0, "failed": 90,
                             "completed": 10}) + "\n")
    ad = HappyFeederAdapter(source=str(p))
    lanes, _ = ad.snapshot()
    d = "\n".join(ad.diagnostics_for(lanes))
    assert "0 workers" in d and "feeder may have stopped launching" in d
    assert "failures exceed completions" in d
    assert "heartbeat UNKNOWN" in d


def test_diagnostic_is_reported_not_acted_on():
    ad = HappyFeederAdapter(source=None)
    lanes, _ = ad.snapshot()
    # no method mutates anything; diagnostics are read-only strings
    out = ad.diagnostics_for(lanes)
    assert isinstance(out, list) and all(isinstance(x, str) for x in out)


# ------------------------------------------ the nine lanes are tracked

def test_nine_reported_lanes_present_as_assertions():
    from director_consumer.populations import HAPPY_LANES
    assert len(HAPPY_LANES) == 9
    asserted = {n: {"count": 413000 // 9, "work_unit_type":
                    UNIT_DOCUMENT_FACET, "source": "owner-report"}
                for n in HAPPY_LANES}
    a = assess_factory([], asserted_populations=asserted)
    assert len(a.asserted_only) == 9
    assert all("NOT independently verified" in x for x in a.asserted_only)
