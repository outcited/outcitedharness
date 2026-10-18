"""Factory population model — DIRECTOR-06 §1.

The single reason the observer repeatedly got the factory wrong is that it
treated ONE queue's emptiness as a statement about the factory. Happy runs a
SELF-FEEDING work feeder over NINE named extraction lanes with its own work
populations; the substrate/pipeline queue is a different mechanism with a
different population. An empty substrate queue is a fact about the substrate
lane and nothing else.

This module makes that mistake structurally impossible.

Laws enforced here:

  * A work unit is typed. `document` and `document-facet` are NEVER the same
    population and are never summed. 413K document-facet work items is not
    413K documents.
  * Populations are keyed by (source_population, work_unit_type, lane_id).
    Counts from different keys are never added.
  * An observation carries its evidence class: `measured` (read from a source
    we opened) or `assertion` (owner/agent-reported, not independently
    verified). An assertion never becomes a measurement by repetition.
  * "Factory starvation" is only assertable when every known lane is observed
    AND ready == 0 AND there is no unobserved lane AND no asserted-but-unverified
    population. Anything less is `insufficient-evidence` with the gaps named.
  * A lane with no input available is `input-blocked`, not compute-starved.
    (The five MACMOBILE lanes are exactly this case.)
  * Producer timestamps and observation timestamps are distinct fields.

Nothing here mutates state or talks to a feeder. This is observation only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

UNKNOWN = "unknown"

# Evidence classes. Deliberately mirrors the institutional memory contract so
# the same words mean the same thing everywhere.
MEASURED = "measured"
ASSERTION = "assertion"

# Work-unit types. Adding two different types is always a bug.
UNIT_DOCUMENT = "document"
UNIT_DOCUMENT_FACET = "document-facet"
UNIT_CHUNK = "chunk"
UNIT_JOB = "job"
WORK_UNIT_TYPES = (UNIT_DOCUMENT, UNIT_DOCUMENT_FACET, UNIT_CHUNK, UNIT_JOB)

# The nine lanes reported for happy's self-feeding extractor. They are an
# ASSERTION until a live feeder source proves them.
HAPPY_LANES = (
    "thermal", "aec_grades", "mode_currents", "tables", "parametrics",
    "power_desc", "power_v2", "vector_curves", "family_matrix",
)


@dataclass
class Observation:
    """One counted quantity, with the provenance required to trust it."""
    value: Any = UNKNOWN
    evidence: str = UNKNOWN          # measured | assertion
    source_id: str = ""              # where it was read / who reported it
    source_timestamp: Any = UNKNOWN  # producer time
    measured_at: Any = UNKNOWN       # when we looked
    note: str = ""


@dataclass
class LaneState:
    """Per-lane state. Fields stay UNKNOWN unless a source actually had them;
    absent telemetry is never coerced to zero."""
    lane_id: str = ""
    source_population: str = ""     # e.g. happy-self-feeder / substrate-pipeline
    work_unit_type: str = UNKNOWN
    queue_id: str = UNKNOWN
    ready: Any = UNKNOWN
    blocked: Any = UNKNOWN
    running: Any = UNKNOWN
    completed: Any = UNKNOWN
    failed: Any = UNKNOWN
    retrying: Any = UNKNOWN
    verified: Any = UNKNOWN
    worker_count: Any = UNKNOWN
    throughput: Any = UNKNOWN
    feeder_heartbeat: Any = UNKNOWN
    last_error: Any = UNKNOWN
    input_available: Any = UNKNOWN  # False => input-blocked, not compute-starved
    evidence: str = UNKNOWN
    source_id: str = ""
    measured_at: Any = UNKNOWN
    source_timestamp: Any = UNKNOWN

    def ready_or_unknown(self) -> Any:
        return self.ready

    def observed(self) -> bool:
        return self.evidence == MEASURED


@dataclass
class PopulationKey:
    source_population: str
    work_unit_type: str
    lane_id: str

    def __repr__(self) -> str:  # pragma: no cover
        return f"{self.source_population}/{self.work_unit_type}/{self.lane_id}"


@dataclass
class FactoryAssessment:
    verdict: str = UNKNOWN
    reason: str = ""
    per_lane: dict = field(default_factory=dict)
    totals_by_unit_type: dict = field(default_factory=dict)  # NEVER summed across
    unsummed_note: str = ("document, document-facet, chunk and job populations "
                          "are different units and are never added together")
    gaps: list[str] = field(default_factory=list)
    asserted_only: list[str] = field(default_factory=list)
    input_blocked_lanes: list[str] = field(default_factory=list)
    compute_starved_lanes: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    evidence_summary: dict = field(default_factory=dict)


def _int(x) -> bool:
    return isinstance(x, int)


def assess_factory(lanes: list[LaneState],
                   asserted_populations: Optional[dict] = None) -> FactoryAssessment:
    """Produce a factory-level statement that cannot overreach.

    asserted_populations: {lane_id: {"count": int, "unit_type": str, "source": str}}
    owner/agent-reported quantities we could not read ourselves.
    """
    a = FactoryAssessment()
    asserted_populations = asserted_populations or {}
    by_type: dict[str, list] = {}

    for ln in lanes:
        a.per_lane[ln.lane_id] = {
            "source_population": ln.source_population,
            "work_unit_type": ln.work_unit_type,
            "ready": ln.ready, "blocked": ln.blocked, "running": ln.running,
            "completed": ln.completed, "failed": ln.failed,
            "retrying": ln.retrying, "verified": ln.verified,
            "worker_count": ln.worker_count, "throughput": ln.throughput,
            "input_available": ln.input_available,
            "evidence": ln.evidence, "source_id": ln.source_id,
        }
        if _int(ln.ready):
            by_type.setdefault(ln.work_unit_type, []).append(ln.ready)
        if not ln.observed():
            a.gaps.append(
                f"lane {ln.lane_id!r} not measured (evidence={ln.evidence})")
        # input-blocked vs compute-starved
        if ln.input_available is False:
            a.input_blocked_lanes.append(ln.lane_id)
            a.notes.append(
                f"lane {ln.lane_id!r} is INPUT-BLOCKED (no input available) — "
                f"this is missing input, not compute starvation")
        elif ln.input_available is True and _int(ln.ready) and ln.ready == 0 \
                and (_int(ln.worker_count) is False or ln.worker_count == 0):
            a.compute_starved_lanes.append(ln.lane_id)

    # totals per unit type only — never across types
    for ut, vals in by_type.items():
        a.totals_by_unit_type[ut] = sum(vals)
    a.evidence_summary["measured_lanes"] = [l.lane_id for l in lanes
                                            if l.observed()]
    a.evidence_summary["asserted_lanes"] = [l.lane_id for l in lanes
                                            if not l.observed()]

    # ---- asserted-but-unverified populations (happy's 413K, etc.) ---------
    asserted_total_by_type: dict[str, int] = {}
    for lane_id, info in asserted_populations.items():
        ut = info.get("work_unit_type", UNKNOWN)
        cnt = info.get("count")
        a.asserted_only.append(
            f"{lane_id}: {cnt} {ut} (asserted by {info.get('source','?')}, "
            f"NOT independently verified)")
        if _int(cnt):
            asserted_total_by_type[ut] = asserted_total_by_type.get(ut, 0) + cnt
    if asserted_total_by_type:
        a.notes.append(
            "asserted populations are reported separately from measured ones "
            "and are never added to them")

    # ---- the verdict ----------------------------------------------------
    observed = [l for l in lanes if l.observed()]
    unobserved = [l for l in lanes if not l.observed()]
    ready_zero_all_observed = (bool(observed) and all(
        _int(l.ready) and l.ready == 0 for l in observed))
    any_ready = any(_int(l.ready) and l.ready > 0 for l in lanes)

    if any_ready:
        a.verdict = "work-available"
        lanes_with = [l.lane_id for l in lanes if _int(l.ready) and l.ready > 0]
        a.reason = (f"ready work present in lane(s) {lanes_with}; "
                    f"units kept separate by type")
    elif asserted_populations:
        # THE CASE THAT MATTERS: our queues look empty but an unverified
        # population is reported elsewhere. This is NOT starvation.
        a.verdict = "insufficient-evidence"
        a.reason = (
            "observed queues show zero ready work, but other populations are "
            "reported and not independently verified — this is NOT factory "
            "starvation and NOT evidence that extraction is idle")
        a.gaps.append("live read of the reported work feeder (owner access)")
    elif unobserved:
        a.verdict = "insufficient-evidence"
        a.reason = (
            f"{len(unobserved)} lane(s) unobserved ({[l.lane_id for l in unobserved]}); "
            f"an empty observed queue does not establish that all queues are empty")
    elif ready_zero_all_observed and not a.input_blocked_lanes:
        a.verdict = "observed-lanes-empty"
        a.reason = ("every observed lane reports zero ready work and no other "
                    "population is reported; still not factory-wide starvation "
                    "unless every producer is confirmed observed")
        a.gaps.append("confirmation that no unlisted lane or producer exists")
    elif a.input_blocked_lanes and ready_zero_all_observed:
        a.verdict = "input-blocked"
        a.reason = ("ready work is zero because input is unavailable for "
                    f"lane(s) {a.input_blocked_lanes} — missing input, not "
                    f"compute starvation")
    else:
        a.verdict = "insufficient-evidence"
        a.reason = "population state incomplete; no conclusion drawn"

    # never allow the word starvation as a conclusion from partial data
    if a.verdict in ("insufficient-evidence", "input-blocked", "observed-lanes-empty"):
        a.notes.append(
            "'starvation' is not asserted: it requires every lane observed with "
            "ready == 0 and no unverified population reported")
    return a


def reconcile_reported_total(reported: Any, measured_total: Any,
                             unit_type: str,
                             unique_docs: Any = UNKNOWN) -> dict:
    """Reconcile an owner-reported figure against what we measured.

    The 413K number is DOCUMENT-FACET WORK ITEMS. It is not 413K unique PDFs.
    This makes the distinction explicit instead of letting a headline number
    drift into a document count.
    """
    out = {
        "reported": reported,
        "reported_unit_type": unit_type,
        "measured_total": measured_total,
        "unique_documents": unique_docs,
        "reconciled": UNKNOWN,
        "notes": [],
    }
    if unit_type != UNIT_DOCUMENT:
        out["notes"].append(
            f"reported figure is in {unit_type!r} units — it must never be "
            f"read as a unique-document count")
    if _int(reported) and _int(measured_total):
        out["reconciled"] = ("exact" if reported == measured_total
                             else "differs")
        out["delta"] = reported - measured_total
    else:
        out["notes"].append(
            "cannot reconcile: one side is unmeasured (missing live feeder read)")
    if _int(unique_docs) and _int(reported):
        out["items_per_document"] = round(reported / unique_docs, 2)
        out["notes"].append(
            "items-per-document shows the facet fan-out; only meaningful if "
            "both sides use the same population")
    return out
