"""Factory continuity — the PDF Factory must stay ahead of extraction (§4).

Read-only measurements from REAL queue/throughput evidence only. Every
unknown is reported as UNKNOWN with the missing measurement named — never
invented. Two runtimes share one assessment:

  - Rust production lane (factory.sqlite3: chunks + events)
  - Python substrate queue (pipeline-live.db: jobs)
  - Facet consumption evidence (ledger line-count growth across runs)
  - Acquisition evidence (pdf-intake log cadence)
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .schema import Event, SourceRef, StateMarker, utcnow


def _ro(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
    con.row_factory = sqlite3.Row
    return con


def _scalar(con, sql, params=()):
    try:
        row = con.execute(sql, params).fetchone()
        return row[0] if row else None
    except sqlite3.Error:
        return None


INTAKE_TS = re.compile(r"=== PDF intake (\S+) ===")
INTAKE_FILES = re.compile(r"Number of files: (\d+)")


@dataclass
class ContinuityMeasurements:
    rust_queued: Any = "unknown"
    rust_claimed: Any = "unknown"
    substrate_pending: Any = "unknown"
    junk_dead_jobs: Any = "unknown"
    stalled_leases: Any = "unknown"
    ledger_done_total: Any = "unknown"
    acquisition_files: Any = "unknown"
    acquisition_span_days: Any = "unknown"
    acquisition_rate_docs_day: Any = "unknown"
    consumption_rate_docs_hr: Any = "unknown"
    ready_backlog_docs: Any = "unknown"
    starvation_hours: Any = "unknown"
    starvation_basis: str = "unknown"   # measured | backlog-zero |
                                        # unknown-telemetry | queues-unavailable
    ready_by_class: dict = field(default_factory=dict)   # class -> docs
    pages_by_class: dict = field(default_factory=dict)   # class -> pages
    worker_hours_estimate: Any = "unknown"
    worker_hours_note: str = ""
    coverage_target_h: float = 24.0
    notes: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)


class FactoryContinuity:
    def __init__(self, factory_db: Optional[Path] = None,
                 pipeline_db: Optional[Path] = None,
                 facet_root: Optional[Path] = None,
                 intake_log: Optional[Path] = None,
                 l0_index: Optional[Path] = None,
                 backlog_paths: Optional[Path] = None,
                 class_costs: Optional[dict] = None,
                 thresholds: Optional[dict] = None):
        self.factory_db = Path(factory_db) if factory_db else None
        self.pipeline_db = Path(pipeline_db) if pipeline_db else None
        self.facet_root = Path(facet_root) if facet_root else None
        self.intake_log = Path(intake_log) if intake_log else None
        self.l0_index = Path(l0_index) if l0_index else None
        self.backlog_paths = Path(backlog_paths) if backlog_paths else None
        self.class_costs = class_costs or {}
        th = thresholds or {}
        self.junk_flood_threshold = int(th.get("junk_flood_threshold", 1000))
        self.lease_stall_window_m = int(th.get("lease_stall_window_m", 60))
        self.starvation_alert_h = float(th.get("starvation_alert_h", 24))
        self.coverage_target_h = float(th.get("ready_coverage_target_h", 24))

    # ---- measurement helpers (each names its missing measurement) ----

    def _measure_rust(self, m: ContinuityMeasurements) -> None:
        if not (self.factory_db and self.factory_db.exists()):
            m.notes.append("rust queue UNKNOWN: factory.sqlite3 not found "
                           f"({self.factory_db})")
            return
        con = _ro(self.factory_db)
        try:
            m.rust_queued = _scalar(
                con, "SELECT COUNT(*) FROM factory_chunks WHERE state='queued'")
            m.rust_claimed = _scalar(
                con, "SELECT COUNT(*) FROM factory_chunks WHERE state='claimed'")
            stalled = _scalar(con, (
                "SELECT COUNT(*) FROM factory_chunks WHERE state='claimed' "
                "AND lease_expires_at < datetime('now')"))
            if stalled is None:
                # schema variant: report unknown, name it
                m.notes.append("stalled leases UNKNOWN: lease_expires_at "
                               "column not queryable")
            else:
                m.stalled_leases = stalled
            m.evidence.append(f"{self.factory_db}:factory_chunks")
        finally:
            con.close()

    def _measure_pipeline(self, m: ContinuityMeasurements) -> None:
        if not (self.pipeline_db and self.pipeline_db.exists()):
            m.notes.append("substrate queue UNKNOWN: pipeline db not found")
            return
        con = _ro(self.pipeline_db)
        try:
            m.substrate_pending = _scalar(
                con, "SELECT COUNT(*) FROM jobs WHERE state='pending'")
            junk = _scalar(con, (
                "SELECT COUNT(*) FROM jobs WHERE state='dead' "
                "AND (corpus_key LIKE '%._%' OR source_path LIKE '%._%')"))
            m.junk_dead_jobs = junk if junk is not None else "unknown"
            m.evidence.append(f"{self.pipeline_db}:jobs")
        finally:
            con.close()

    def _measure_ledgers(self, m: ContinuityMeasurements, obs: list) -> None:
        if not (self.facet_root and self.facet_root.exists()):
            m.notes.append("consumption UNKNOWN: facet ledgers not found")
            return
        total = 0
        for f in self.facet_root.glob("done-*.txt"):
            total += sum(1 for _ in open(f, errors="replace"))
        m.ledger_done_total = total
        m.evidence.append(str(self.facet_root) + "/done-*.txt")
        # rate from the caller-managed history (last two observations);
        # the caller appends the CURRENT measurement after assess()
        if len(obs) >= 2:
            a, b = obs[-2], obs[-1]
            dt_h = (float(b["t"]) - float(a["t"])) / 3600.0
            if dt_h > 0.01:
                m.consumption_rate_docs_hr = round(
                    (b["total"] - a["total"]) / dt_h, 2)
            else:
                m.notes.append("consumption rate UNKNOWN: observations too "
                               "close together")
        else:
            m.notes.append("consumption rate UNKNOWN: need >=2 ledger "
                           "observations (missing measurement: elapsed time "
                           "between runs)")

    def _measure_intake(self, m: ContinuityMeasurements) -> None:
        if not (self.intake_log and self.intake_log.exists()):
            m.notes.append("acquisition UNKNOWN: intake log not found")
            return
        points = []
        ts = None
        for line in self.intake_log.read_text(errors="replace").splitlines():
            t = INTAKE_TS.search(line)
            if t:
                ts = t.group(1)
                continue
            f = INTAKE_FILES.search(line)
            if f and ts:
                points.append((ts, int(f.group(1))))
        if len(points) >= 2:
            first, last = points[0], points[-1]
            try:
                from datetime import datetime
                t0 = datetime.fromisoformat(first[0].replace("Z", "+00:00"))
                t1 = datetime.fromisoformat(last[0].replace("Z", "+00:00"))
                days = (t1 - t0).total_seconds() / 86400.0
                if days > 0.01:
                    m.acquisition_span_days = round(days, 2)
                    m.acquisition_files = last[1]
                    m.acquisition_rate_docs_day = round(
                        (last[1] - first[1]) / days, 1)
                    m.evidence.append(f"{self.intake_log} "
                                      f"({first[0]}..{last[0]})")
                else:
                    m.notes.append("acquisition rate UNKNOWN: log span too "
                                   "short")
            except ValueError:
                m.notes.append("acquisition rate UNKNOWN: log timestamps "
                               "unparseable")
        else:
            m.notes.append("acquisition rate UNKNOWN: need >=2 intake "
                           "checkpoints in log")

    # ---- main ----

    def _measure_classes(self, m: ContinuityMeasurements) -> None:
        """§2: PDFs are not equal — class/page aware cost over the REAL
        rescan backlog joined to L0 census records by source_path."""
        if not self.l0_index or not self.l0_index.exists():
            m.notes.append("worker-hours UNKNOWN: L0 index not available "
                           "(missing measurement: doc_class/page_count join)")
            return
        backlog: list[str] = []
        if self.backlog_paths and self.backlog_paths.exists():
            try:
                backlog = json.loads(self.backlog_paths.read_text())
            except json.JSONDecodeError:
                backlog = []
        else:
            m.notes.append("class backlog UNKNOWN: backlog path list missing")
            return
        want = set(backlog)
        by_class: dict[str, int] = {}
        pages_by_class: dict[str, int] = {}
        index: dict[str, tuple[str, int]] = {}
        try:
            with open(self.l0_index, errors="replace") as fh:
                for line in fh:
                    try:
                        r = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    sp = r.get("source_path")
                    if sp in want:
                        index[sp] = (r.get("doc_class", "unknown"),
                                     int(r.get("page_count") or 0))
        except OSError:
            m.notes.append("worker-hours UNKNOWN: L0 index unreadable")
            return
        for sp, (cls, pages) in index.items():
            by_class[cls] = by_class.get(cls, 0) + 1
            pages_by_class[cls] = pages_by_class.get(cls, 0) + pages
        m.ready_by_class = by_class
        m.pages_by_class = pages_by_class
        m.evidence.append(f"{self.l0_index} (joined {len(index)}/"
                          f"{len(want)} backlog docs)")
        # worker-hours: measured class costs only; unmapped classes are
        # explicitly UNKNOWN, never guessed
        total_h, unmapped = 0.0, 0
        unmapped_classes = []
        for cls, n in by_class.items():
            cost = self.class_costs.get(cls)
            if cost and "hours_per_doc" in cost:
                total_h += n * float(cost["hours_per_doc"])
            else:
                unmapped += n
                unmapped_classes.append(cls)
        if unmapped and not self.class_costs:
            m.worker_hours_estimate = "unknown"
            m.worker_hours_note = (
                "missing measurement: per-class extraction cost table "
                "(class_costs config empty; benchmark needed)")
        elif unmapped:
            m.worker_hours_estimate = round(total_h, 2)
            m.worker_hours_note = (
                f"PARTIAL: {unmapped} docs of unmapped classes excluded "
                f"({', '.join(sorted(unmapped_classes))}) — missing "
                f"measurement: class cost for those classes")
        else:
            m.worker_hours_estimate = round(total_h, 2)
            m.worker_hours_note = "class-cost table applied (see provenance)"

    def assess(self, observations: list) -> ContinuityMeasurements:
        m = ContinuityMeasurements()
        m.coverage_target_h = self.coverage_target_h
        self._measure_rust(m)
        self._measure_pipeline(m)
        self._measure_ledgers(m, observations)
        self._measure_intake(m)
        self._measure_classes(m)
        # ready backlog = known queue parts only
        parts = [x for x in (m.rust_queued, m.substrate_pending)
                 if isinstance(x, int)]
        queues_missing = (m.rust_queued == "unknown"
                          and m.substrate_pending == "unknown")
        if parts:
            m.ready_backlog_docs = sum(parts)
        # starvation basis: distinguish real starvation from telemetry gaps
        # and temporarily unavailable queues (§2 acceptance)
        if queues_missing:
            m.starvation_basis = "queues-unavailable"
            m.notes.append("starvation UNKNOWN: queues temporarily "
                           "unavailable (NOT evidence of starvation)")
        elif m.ready_backlog_docs == 0:
            m.starvation_hours = 0.0
            m.starvation_basis = "backlog-zero"
        elif isinstance(m.consumption_rate_docs_hr, (int, float)) and \
                m.consumption_rate_docs_hr > 0:
            m.starvation_hours = round(
                m.ready_backlog_docs / m.consumption_rate_docs_hr, 1)
            m.starvation_basis = "measured"
        else:
            m.starvation_basis = "unknown-telemetry"
            m.notes.append("starvation UNKNOWN: consumption rate unknown "
                           "(NOT evidence of starvation)")
        return m

    def events(self, m: ContinuityMeasurements) -> list[Event]:
        out: list[Event] = []

        def ev(kind, summary, attrs):
            ref = SourceRef("factory", f"continuity/{kind}",
                            None, utcnow(), locator="factory-continuity")
            e = Event("factory", kind, summary, [ref], attrs=attrs)
            out.append(e)

        if isinstance(m.junk_dead_jobs, int) and \
                m.junk_dead_jobs > self.junk_flood_threshold:
            ev("junk_flood",
               f"AppleDouble contamination: {m.junk_dead_jobs} dead '._' "
               f"jobs > threshold {self.junk_flood_threshold}",
               {"severity_hint": "P2"})
        if isinstance(m.stalled_leases, int) and m.stalled_leases > 0:
            ev("lease_stall",
               f"{m.stalled_leases} claimed chunks with expired leases "
               f"(stall window {self.lease_stall_window_m}m)",
               {"severity_hint": "P2"})
        if m.starvation_basis in ("measured", "backlog-zero") and \
                isinstance(m.starvation_hours, (int, float)):
            if m.starvation_hours < self.starvation_alert_h:
                ev("starvation_risk",
                   f"predicted extraction starvation in "
                   f"{m.starvation_hours}h (< {self.starvation_alert_h}h "
                   f"target); basis={m.starvation_basis}; "
                   f"backlog {m.ready_backlog_docs} docs "
                   f"(worker-hours: {m.worker_hours_estimate})",
                   {"severity_hint": "P1"})
        if m.ready_backlog_docs == 0:
            ev("queue_empty", "extraction queues empty — workers will idle",
               {"severity_hint": "P1"})
        if isinstance(m.acquisition_rate_docs_day, (int, float)) and \
                m.acquisition_rate_docs_day <= 0:
            ev("intake_anomaly",
               f"acquisition rate {m.acquisition_rate_docs_day} docs/day — "
               f"factory not growing the corpus",
               {"severity_hint": "P2"})
        return out

    def markers(self, m: ContinuityMeasurements) -> list[StateMarker]:
        # UNKNOWN is a first-class result; mark the feed degraded only when
        # the primary queues are unobservable
        marks = []
        if m.rust_queued == "unknown" and m.substrate_pending == "unknown":
            marks.append(StateMarker(
                "feed_error", "factory",
                "both extraction queues unobservable — continuity UNKNOWN",
                utcnow()))
        return marks
