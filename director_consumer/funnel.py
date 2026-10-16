"""Supply funnel reconciliation (§6) — where is usable work held up?

Stages (each from a named source or explicitly UNKNOWN):
  acquired → validated → eligible → prioritized → queued → leased
  → completed/verified

The 03 discrepancy (executable queue 0 vs 121k inventory) is explained
here: the rescan inventory is ELIGIBLE work that is not being admitted
into execution queues — an admission gap, not missing supply.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

UNKNOWN = "unknown"


@dataclass
class FunnelReport:
    acquired: Any = UNKNOWN
    validated: Any = UNKNOWN
    eligible: Any = UNKNOWN
    prioritized: Any = UNKNOWN
    queued: Any = UNKNOWN
    leased: Any = UNKNOWN
    completed_verified: Any = UNKNOWN
    holdup: str = UNKNOWN
    holdup_evidence: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def build_funnel(m, l0_coverage: tuple[int, int] = (0, 0)) -> FunnelReport:
    """m: FactoryContinuity.measurements. All numbers are passed-through
    evidence; UNKNOWN discipline preserved."""
    f = FunnelReport()
    f.acquired = m.acquisition_files
    f.queued = (m.rust_queued if isinstance(m.rust_queued, int) else 0) + \
        (m.substrate_pending if isinstance(m.substrate_pending, int) else 0) \
        if (isinstance(m.rust_queued, int)
            or isinstance(m.substrate_pending, int)) else UNKNOWN
    f.leased = m.rust_claimed
    f.completed_verified = m.ledger_done_total
    f.prioritized = UNKNOWN
    f.notes.append(
        "prioritized UNKNOWN: factory_targets.json unreachable "
        "(missing source: mac-mini factory manifest mirror)")
    f.validated = UNKNOWN
    f.notes.append("validated UNKNOWN: no validation ledger source "
                   "(missing measurement: validated-doc counter)")
    # eligible: the rescan inventory when class/backlog evidence exists
    eligible = sum(m.ready_by_class.values()) if m.ready_by_class else UNKNOWN
    f.eligible = eligible
    # holdup analysis — the §6 question
    if f.queued == 0 and isinstance(eligible, int) and eligible > 0:
        f.holdup = "admission-gap"
        f.holdup_evidence.append(
            f"executable queue empty while {eligible} eligible rescan docs "
            f"exist — inventory is not entering execution queues")
        f.holdup_evidence.append(
            "facet wave orchestrators run waves from the path list; the "
            "list is NOT the queue (no admission step into factory_chunks "
            "or pipeline jobs)")
    elif f.queued == UNKNOWN:
        f.holdup = "queues-unavailable"
    elif isinstance(f.queued, int) and f.queued > 0:
        f.holdup = "none-detected"
    covered, total = l0_coverage
    if total and covered < total:
        f.notes.append(
            f"L0 class coverage {covered}/{total} — re-census or path-map "
            f"proposal required before worker-hours are complete")
    return f
