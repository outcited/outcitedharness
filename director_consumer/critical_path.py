"""Critical-path intelligence as a generic platform feature (§9).

Every project has something that limits useful work. The contract names
the supplier/consumer relationship and the coverage math; the DesignWins
factory-continuity engine is the first instantiation and stays
document-class-aware with full UNKNOWN discipline. NO factory-specific
logic lives here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

UNKNOWN = "unknown"


@dataclass
class CriticalPath:
    name: str
    project_id: str
    upstream_supplier: str
    downstream_consumer: str
    required_rate: Any = UNKNOWN        # work units / hour
    available_backlog: Any = UNKNOWN
    consumption_rate: Any = UNKNOWN
    coverage_target_h: float = 24.0
    failure_conditions: list = field(default_factory=list)
    repair_owner: str = ""
    verification_requirement: str = ""
    notes: list = field(default_factory=list)


@dataclass
class CriticalPathAssessment:
    status: str = UNKNOWN               # ok | at-risk | starving | unknown
    coverage_hours: Any = UNKNOWN
    reasons: list = field(default_factory=list)
    missing_measurements: list = field(default_factory=list)


def assess(cp: CriticalPath) -> CriticalPathAssessment:
    a = CriticalPathAssessment()
    rate_known = isinstance(cp.consumption_rate, (int, float))
    backlog_known = isinstance(cp.available_backlog, (int, float))
    if not backlog_known:
        a.status = UNKNOWN
        a.missing_measurements.append("available_backlog")
    elif cp.available_backlog == 0:
        a.status = "starving"
        a.reasons.append("backlog is zero — consumer has no work")
    elif rate_known and cp.consumption_rate > 0:
        a.coverage_hours = round(cp.available_backlog / cp.consumption_rate, 1)
        if a.coverage_hours < cp.coverage_target_h:
            a.status = "at-risk"
            a.reasons.append(
                f"coverage {a.coverage_hours}h < target "
                f"{cp.coverage_target_h}h")
        else:
            a.status = "ok"
            a.reasons.append(f"coverage {a.coverage_hours}h meets target")
    else:
        a.status = UNKNOWN
        a.missing_measurements.append("consumption_rate")
        a.reasons.append(
            "coverage cannot be computed — rate unknown (not evidence of "
            "starvation)")
    return a


def from_designwins_continuity(m, funnel_report=None) -> CriticalPath:
    """DesignWins instantiation: prioritized PDF supply -> extraction.
    All values passed through from measured sources; UNKNOWN preserved."""
    cp = CriticalPath(
        name="pdf-supply-to-extraction", project_id="designwins",
        upstream_supplier="mac-mini/Tapes + N100 acquisition factory",
        downstream_consumer="Ryzen extraction/normalization workers",
        consumption_rate=m.consumption_rate_docs_hr,
        available_backlog=(m.ready_backlog_docs if funnel_report is None
                           else funnel_report.queued),
        coverage_target_h=m.coverage_target_h,
        failure_conditions=[
            "executable queues empty while eligible inventory exists "
            "(admission gap)",
            "AppleDouble contamination flooding dead letters",
            "manufacturer endpoint regression blocking acquisition"],
        repair_owner="agent:m5-opencode (supply path) / mac-mini (factory)",
        verification_requirement="measured ready-work coverage >= target "
                                 "with class-aware worker-hours",
        notes=list(m.notes))
    if funnel_report is not None and funnel_report.holdup == "admission-gap":
        cp.notes.append(
            "admission gap confirmed: eligible inventory not entering "
            "execution queues")
    return cp
