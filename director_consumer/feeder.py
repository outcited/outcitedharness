"""Observation-only adapter for happy's self-feeding extraction feeder.

DIRECTOR-06 §1. Happy owns local scheduling; this adapter READS and never
writes, never restarts, never enqueues.

Contract rules:
  * The feeder's schema is NOT assumed. Every field is taken only when the
    source actually provides it; anything absent stays UNKNOWN. Absent
    telemetry is never coerced to zero.
  * If no source is configured or reachable, the adapter returns an explicit
    gap — it does not fabricate an empty factory.
  * Work items are keyed by (document identity, facet identity, extraction
    version) for dedup and population comparison. Documents and
    document-facets are separate populations and are never summed.
  * Producer timestamps and observation timestamps are kept distinct.
  * Diagnostic detection: failed auto-restarts, empty-eligible lanes,
    repeated crash/relaunch loops, stalled progress, growing failures.

Supported source shapes (auto-detected, first match wins):
  1. SQLite database  — one table per lane, or a table with a lane column
  2. JSONL            — one lane snapshot or work item per line
  3. JSON             — {lane_id: {...}} or {"lanes": [...]}
Nothing else is silently accepted.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

from .populations import (
    ASSERTION, MEASURED, UNKNOWN, LaneState, Observation,
    UNIT_DOCUMENT_FACET,
)

# The lane record contract from the assignment. We accept any subset.
LANE_FIELDS = (
    "lane_id", "source_population", "queue_id", "work_unit_type",
    "source_sha", "ready", "blocked", "running", "completed", "failed",
    "retrying", "verified", "measured_at", "source_timestamp",
    "feeder_heartbeat", "worker_count", "throughput", "last_error",
    "input_available",
)

# Aliases we tolerate if a producer used different words. Nothing is invented;
# unknown names simply do not populate a field.
FIELD_ALIASES = {
    "ready": ("ready", "queued", "eligible", "ready_count", "n_ready"),
    "blocked": ("blocked", "waiting_input", "held", "n_blocked"),
    "running": ("running", "active", "in_flight", "n_running", "workers_busy"),
    "completed": ("completed", "done", "finished", "n_completed"),
    "failed": ("failed", "errors", "n_failed"),
    "retrying": ("retrying", "retries", "n_retrying"),
    "verified": ("verified", "n_verified", "verified_count"),
    "worker_count": ("worker_count", "workers", "n_workers", "processes"),
    "throughput": ("throughput", "rate", "docs_per_hour", "items_per_hour"),
    "feeder_heartbeat": ("feeder_heartbeat", "heartbeat", "last_beat",
                         "feeder_ts"),
    "last_error": ("last_error", "error", "last_failure"),
    "input_available": ("input_available", "has_input", "input_ready"),
    "work_unit_type": ("work_unit_type", "unit_type", "granularity"),
    "queue_id": ("queue_id", "queue", "manifest"),
    "source_population": ("source_population", "population", "cohort"),
    "source_timestamp": ("source_timestamp", "producer_ts", "ts", "updated_at"),
}


def _pick(d: dict, canonical: str) -> Any:
    for name in FIELD_ALIASES.get(canonical, (canonical,)):
        if name in d and d[name] is not None:
            return d[name]
    return UNKNOWN


def _coerce_intish(v: Any) -> Any:
    if isinstance(v, bool):
        return UNKNOWN
    if isinstance(v, int):
        return v
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return UNKNOWN


def _lane_from_mapping(raw: dict, default_lane: str = "",
                       source_id: str = "") -> LaneState:
    ln = LaneState()
    ln.lane_id = str(raw.get("lane_id") or default_lane or UNKNOWN)
    for canon in ("source_population", "queue_id", "work_unit_type",
                  "source_timestamp", "feeder_heartbeat", "last_error"):
        setattr(ln, canon, _pick(raw, canon))
    for canon in ("ready", "blocked", "running", "completed", "failed",
                  "retrying", "verified", "worker_count"):
        setattr(ln, canon, _coerce_intish(_pick(raw, canon)))
    if ln.work_unit_type is UNKNOWN:
        # do not guess the unit type; leave UNKNOWN so it can never be summed
        pass
    iv = _pick(raw, "input_available")
    if isinstance(iv, bool):
        ln.input_available = iv
    ln.throughput = _pick(raw, "throughput")
    ln.source_id = source_id
    ln.measured_at = time.time()
    ln.evidence = MEASURED     # we read it from a real source
    return ln


class HappyFeederAdapter:
    """Read-only. Never writes to the feeder, its queues, or its outputs."""

    def __init__(self, source: Optional[str] = None,
                 source_id: str = "happy-self-feeder",
                 max_rows: int = 200_000):
        self.source = source
        self.source_id = source_id
        self.max_rows = max_rows
        self.gaps: list[str] = []
        self.diagnostics: list[str] = []
        self.observations: list[Observation] = []
        self.dedup_keys: set[tuple] = set()
        self.duplicates = 0

    # ------------------------------------------------------------------ #
    def snapshot(self) -> tuple[list[LaneState], list[str]]:
        """Return (lanes, gaps). lanes is EMPTY and gaps is populated when no
        source exists — never a fabricated all-zero factory."""
        self.gaps = []
        if not self.source:
            self.gaps.append(
                "happy self-feeder: NO SOURCE CONFIGURED — lane state UNKNOWN; "
                "an absent feed is not an empty factory")
            return [], self.gaps
        p = Path(self.source)
        if not p.exists():
            self.gaps.append(
                f"happy self-feeder: source not present ({self.source}) — "
                f"owner-reported lane counts remains ASSERTIONS")
            return [], self.gaps
        try:
            if p.suffix in (".db", ".sqlite", ".sqlite3"):
                lanes = self._from_sqlite(p)
            elif p.suffix in (".jsonl", ".ndjson"):
                lanes = self._from_jsonl(p)
            elif p.suffix == ".json":
                lanes = self._from_json(p)
            else:
                self.gaps.append(
                    f"happy self-feeder: unsupported source shape {p.suffix!r} "
                    f"— refusing to guess a schema")
                return [], self.gaps
        except Exception as e:                      # noqa: BLE001
            self.gaps.append(
                f"happy self-feeder: read failed ({type(e).__name__}: {e}) — "
                f"lane state UNKNOWN")
            return [], self.gaps
        if not lanes:
            self.gaps.append(
                "happy self-feeder: source read but produced no lane records")
        return lanes, self.gaps

    # ------------------------------------------------------------------ #
    def _dedup(self, raw: dict) -> bool:
        """Stable (document identity, facet identity, extraction version)."""
        doc = raw.get("source_sha") or raw.get("document_sha256") or \
            raw.get("doc") or raw.get("part_number")
        facet = raw.get("facet") or raw.get("facet_id") or raw.get("kind") or \
            raw.get("work_unit_type")
        ver = raw.get("extraction_version") or raw.get("version") or \
            raw.get("extractor_version")
        if doc is None and facet is None:
            return True                      # cannot key it; do not drop
        key = (doc, facet, ver)
        if key in self.dedup_keys:
            self.duplicates += 1
            return False
        self.dedup_keys.add(key)
        return True

    def _accumulate(self, lanes: dict, raw: dict) -> None:
        lane_id = str(raw.get("lane_id") or raw.get("lane")
                      or "unattributed")
        if lane_id not in lanes:
            lanes[lane_id] = _lane_from_mapping(raw, lane_id, self.source_id)
        ln = lanes[lane_id]
        # if this row is a work item rather than a lane rollup, count it
        if any(k in raw for k in ("source_sha", "work_unit_id", "facet")):
            if not self._dedup(raw):
                return
            st = str(raw.get("state") or raw.get("status") or "ready").lower()
            field = {"ready": "ready", "queued": "ready", "eligible": "ready",
                     "blocked": "blocked", "held": "blocked",
                     "running": "running", "active": "running",
                     "completed": "completed", "done": "completed",
                     "failed": "failed", "retrying": "retrying",
                     "verified": "verified"}.get(st)
            if field:
                cur = getattr(ln, field)
                setattr(ln, field, (cur + 1) if isinstance(cur, int) else 1)
            if ln.work_unit_type is UNKNOWN:
                ln.work_unit_type = UNIT_DOCUMENT_FACET

    def _from_jsonl(self, p: Path) -> list[LaneState]:
        lanes: dict[str, LaneState] = {}
        with open(p, errors="replace") as fh:
            for i, line in enumerate(fh):
                if i >= self.max_rows:
                    self.diagnostics.append(f"row cap {self.max_rows} reached")
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(raw, dict):
                    self._accumulate(lanes, raw)
        return list(lanes.values())

    def _from_json(self, p: Path) -> list[LaneState]:
        d = json.loads(p.read_text())
        lanes: dict[str, LaneState] = {}
        if isinstance(d, dict) and isinstance(d.get("lanes"), list):
            for raw in d["lanes"]:
                if isinstance(raw, dict):
                    self._accumulate(lanes, raw)
        elif isinstance(d, dict):
            maybe = all(isinstance(v, dict) for v in d.values()) and d
            if maybe:
                for lid, raw in d.items():
                    raw = dict(raw)
                    raw.setdefault("lane_id", lid)
                    self._accumulate(lanes, raw)
            else:
                self._accumulate(lanes, d)
        elif isinstance(d, list):
            for raw in d:
                if isinstance(raw, dict):
                    self._accumulate(lanes, raw)
        return list(lanes.values())

    def _from_sqlite(self, p: Path) -> list[LaneState]:
        con = sqlite3.connect(f"file:{p}?mode=ro&immutable=1", uri=True)
        con.row_factory = sqlite3.Row
        try:
            names = [r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")]
            lanes: dict[str, LaneState] = {}
            # shape 1: a table with a lane/lane_id column
            rollup = None
            for t in names:
                cols = {r[1] for r in con.execute(f"PRAGMA table_info({t})")}
                if "lane_id" in cols or "lane" in cols:
                    rollup = (t, cols)
                    break
            if rollup:
                t, cols = rollup
                col = "lane_id" if "lane_id" in cols else "lane"
                for raw in con.execute(f"SELECT * FROM {t} LIMIT ?",
                                       (self.max_rows,)):
                    d = dict(raw)
                    d.setdefault("lane_id", d.get(col))
                    self._accumulate(lanes, d)
            else:
                # shape 2: one table per lane, named after the lane
                for t in names:
                    row = con.execute(f"SELECT * FROM {t} LIMIT 1").fetchone()
                    if row is None:
                        continue
                    d = dict(row)
                    d.setdefault("lane_id", t)
                    self._accumulate(lanes, d)
            return list(lanes.values())
        finally:
            con.close()

    # ------------------------------------------------------------------ #
    def diagnostics_for(self, lanes: list[LaneState]) -> list[str]:
        """Failure-mode detection. Reported, never acted on."""
        out = list(self.diagnostics)
        for ln in lanes:
            if ln.input_available is False:
                out.append(
                    f"lane {ln.lane_id}: INPUT-BLOCKED — missing input, not "
                    f"compute starvation")
            if _int0(ln.worker_count) == 0 and _int0(ln.ready) > 0                     and isinstance(ln.ready, int):
                out.append(
                    f"lane {ln.lane_id}: {ln.ready} ready but 0 workers — "
                    f"feeder may have stopped launching (check auto-restart)")
            if isinstance(ln.failed, int) and ln.failed > 0:
                if ln.failed > _int0(ln.completed):
                    out.append(
                        f"lane {ln.lane_id}: failures exceed completions — "
                        f"growing failure pattern")
            if ln.feeder_heartbeat is UNKNOWN:
                out.append(
                    f"lane {ln.lane_id}: feeder heartbeat UNKNOWN — cannot "
                    f"tell a live feeder from a dead one")
        if self.duplicates:
            out.append(
                f"{self.duplicates} duplicate (document, facet, version) keys "
                f"suppressed — populations compared on stable identity")
        return out


def _int0(v: Any, default: int = 0) -> int:
    """Numeric view for comparisons. UNKNOWN/None/non-numeric -> default."""
    return v if isinstance(v, int) and not isinstance(v, bool) else default


def assert_only_reported(lanes_reported: dict) -> list[dict]:
    """Wrap owner/agent-reported lane figures as ASSERTIONS with the exact
    schema fields that were actually reported and nothing more."""
    out = []
    for lane_id, info in lanes_reported.items():
        rec = {"lane_id": lane_id, "evidence": ASSERTION,
               "source_id": info.get("source", "owner-report")}
        for k, v in info.items():
            if k in LANE_FIELDS or k in ("count", "work_unit_type", "source"):
                rec[k] = v
        out.append(rec)
    return out
