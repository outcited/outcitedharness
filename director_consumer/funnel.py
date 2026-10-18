"""Supply funnel reconciliation (§6) — where is usable work held up?

Stages (each from a named source or explicitly UNKNOWN):
  acquired → validated → eligible → prioritized → queued → leased
  → completed/verified

MEASUREMENT LAWS enforced here (DIRECTOR-05E problem C):

  * A measured zero is not UNKNOWN, and UNKNOWN is not a measured zero.
  * An empty queue is not proof that other queues are empty. Lane counts are
    NEVER added: Rust chunks and Python jobs are different populations and
    are never summed into a document count.
  * A zero counter in one queue never establishes that admission is broken.
    "admission-gap" requires MEASURED non-overlap between the inventory and
    the admitted work units (identity join). Without that, the answer is
    "insufficient-evidence" and the missing measurement is named.
  * An incomplete L0 join is never converted into proof of zero eligibility.
  * Acquisition is split: a cumulative inventory counter is never reported
    as "newly acquired". Producer observation time and mirror time are kept
    separate.
  * Done-ledger counts are completions, NOT verification. "completed_verified"
    stays UNKNOWN unless a verification source is supplied.
  * Critical missing source data is flagged by name rather than producing a
    reassuring or an alarming conclusion.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

UNKNOWN = "unknown"


@dataclass
class FunnelReport:
    # acquisition split — a cumulative counter is NOT "newly acquired"
    acquired_new: Any = UNKNOWN
    acquired_inventory_total: Any = UNKNOWN
    validated: Any = UNKNOWN
    eligible: Any = UNKNOWN
    eligible_measured: bool = False
    prioritized: Any = UNKNOWN
    # per-lane queue counts; the cross-lane sum is deliberately absent
    queued_by_lane: dict = field(default_factory=dict)
    queued: Any = UNKNOWN              # single-lane only; else UNKNOWN
    leased: Any = UNKNOWN
    completed_verified: Any = UNKNOWN  # requires a verification source
    lane_done_records: Any = UNKNOWN   # raw completions, NOT verification
    holdup: str = UNKNOWN
    holdup_evidence: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    missing_sources: list[str] = field(default_factory=list)
    # producer observation time vs mirror/copy time — never conflated
    producer_times: dict = field(default_factory=dict)
    mirror_times: dict = field(default_factory=dict)


def build_funnel(m, l0_coverage: tuple[int, int] = (0, 0),
                 admission_overlap: tuple[int, int] = (0, 0),
                 admission_overlap_measured: bool = False,
                 verified_count: Any = UNKNOWN,
                 producer_times: dict | None = None,
                 mirror_times: dict | None = None,
                 acquired_new: Any = UNKNOWN) -> FunnelReport:
    """m: FactoryContinuity.measurements.

    admission_overlap: (matched, total) — how many inventory documents are
    present in the ADMITTED work-unit set (identity join). Only when
    admission_overlap_measured is True may "admission-gap" be concluded, and
    only when matched < total.
    """
    f = FunnelReport()
    f.producer_times = dict(producer_times or {})
    f.mirror_times = dict(mirror_times or {})

    # ---- acquisition: split cumulative inventory from new arrivals -------
    if isinstance(m.acquisition_files, (int, float)):
        f.acquired_inventory_total = m.acquisition_files
        f.notes.append(
            "acquired_inventory_total is a CUMULATIVE file counter "
            "(rsync 'Number of files:'), not newly acquired documents")
    if isinstance(acquired_new, (int, float)):
        f.acquired_new = acquired_new
    else:
        f.acquired_new = UNKNOWN
        f.missing_sources.append(
            "reliable new-acquisition counter (transfer manifest or "
            "SHA-based reconciliation) — an mtime-based 'new PDFs' count is "
            "not trustworthy")

    # ---- queue: per-lane, NEVER summed ----------------------------------
    f.queued_by_lane = {
        "rust_chunks": m.rust_queued,
        "substrate_jobs": m.substrate_pending,
    }
    single = [v for v in (m.rust_queued, m.substrate_pending)
              if isinstance(v, int)]
    if len(single) == 1:
        f.queued = single[0]
    else:
        f.queued = UNKNOWN
        if len(single) > 1:
            f.notes.append(
                "ready queue UNKNOWN: rust chunks and substrate jobs are "
                "different populations and are never summed into one count")
    f.leased = m.rust_claimed

    # ---- completions vs verification ------------------------------------
    f.lane_done_records = m.ledger_done_total
    f.notes.append(
        "lane_done_records are facet done-ledger entries (completions), "
        "NOT verified output")
    f.completed_verified = verified_count
    if verified_count is UNKNOWN:
        f.missing_sources.append(
            "verification source (claim-level source-verified verdicts) — "
            "done-ledger counts cannot stand in for verification")

    # ---- eligibility: never invent zero from an incomplete join ---------
    if getattr(m, "eligible_measured", False):
        f.eligible = sum(m.ready_by_class.values()) if m.ready_by_class else 0
        f.eligible_measured = True
    else:
        f.eligible = UNKNOWN
        f.eligible_measured = False
        f.missing_sources.append(
            "L0 census + backlog path list (eligible inventory join) — "
            "an unmeasured population is never a measured zero")

    f.prioritized = UNKNOWN
    f.notes.append(
        "prioritized UNKNOWN: factory_targets.json unreachable "
        "(missing source: mac-mini factory manifest mirror)")
    f.validated = UNKNOWN
    f.missing_sources.append(
        "validation ledger (validated / duplicate / failed source-document "
        "counter)")

    # ---- holdup analysis -----------------------------------------------
    queues_unknown = (m.rust_queued == UNKNOWN and
                      m.substrate_pending == UNKNOWN)
    observed_vals = [v for v in (m.rust_queued, m.substrate_pending)
                     if isinstance(v, int)]
    # "zero" means every OBSERVED lane reports zero — not that a summed
    # total equals zero (there is no summed total).
    queue_zero = bool(observed_vals) and all(v == 0 for v in observed_vals)
    matched, total = admission_overlap

    if queues_unknown:
        f.holdup = "queues-unavailable"
        f.holdup_evidence.append(
            "no execution queue is observable — absence of telemetry is not "
            "evidence of an empty queue")
        f.missing_sources.append("execution queue telemetry (all lanes)")
    elif queue_zero and admission_overlap_measured and total and matched < total:
        f.holdup = "admission-gap"
        f.holdup_evidence.append(
            f"queue empty AND measured identity join shows only {matched} of "
            f"{total} inventory documents present in the admitted work-unit "
            f"set — inventory is demonstrably not entering execution queues")
    elif queue_zero:
        # A zero counter in ONE queue is not proof of an admission failure:
        # other queues may be active and unobserved, and the inventory may
        # already be fully admitted (as the 05E identity join showed).
        f.holdup = "insufficient-evidence"
        f.holdup_evidence.append(
            "queue shows zero executable work, but a zero counter in one "
            "queue does not establish that admission is broken")
        if not admission_overlap_measured:
            f.holdup_evidence.append(
                "missing measurement: identity join of inventory vs admitted "
                "work units (matched/total)")
        if m.rust_queued == UNKNOWN or m.substrate_pending == UNKNOWN:
            f.holdup_evidence.append(
                "at least one execution lane is unobserved — an empty "
                "inspected queue does not establish that all queues are empty")
    elif any(isinstance(v, int) and v > 0
             for v in (m.rust_queued, m.substrate_pending)):
        f.holdup = "none-detected"

    covered, total_l0 = l0_coverage
    if total_l0 and covered < total_l0:
        f.notes.append(
            f"L0 class coverage {covered}/{total_l0} — re-census or path-map "
            f"proposal required before worker-hours are complete")
        f.missing_sources.append(
            f"L0 census coverage for {total_l0 - covered} backlog documents")
    return f
