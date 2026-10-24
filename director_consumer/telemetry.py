"""Extraction telemetry contract — `harness.extraction-telemetry.v1`.

The authoritative schema is docs/SEVEN-TELEMETRY-CONTRACT.md. This module is
both the CONSUMER seven uses and the shape an EMITTER must produce.

Two rules from the contract that differ from naive Python and must be honoured
exactly, because they are what prevent false starvation reports:

  * ABSENT field  -> unknown        (never measured)
  * explicit NULL -> measured as none/zero

So `queue_items_ready: null` means "measured, and it is zero", while a record
without `queue_items_ready` means "not measured". Collapsing the two is how an
empty legacy queue becomes a false starvation claim.

The interpretation matrix (contract §What Seven Must Distinguish):

  busy-but-useless      high cpu, many workers, STALE output, fresh feeder
  productive            high cpu, output GROWING, queue DECREASING
  starved               low cpu, 0 workers, stale output, stale feeder, 0 queue
  downstream-bottleneck high cpu, output growing FAST, normalization behind

Authority: read-only. Seven observes and proposes; it never dispatches,
reassigns, or modifies workers.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

SCHEMA = "harness.extraction-telemetry.v1"
UNKNOWN = "unknown"
ABSENT = "__absent__"          # distinct from None (= measured none/zero)

DEFAULT_DIR = "/var/lib/extraction-telemetry"
DEFAULT_HTTP = "http://{host}:9100/telemetry"

# lane fields defined by the contract, in contract order
LANE_FIELDS = (
    "name", "extraction_type", "extraction_version", "queue_source",
    "queue_items_total", "queue_items_completed", "queue_items_running",
    "queue_items_ready", "queue_items_failed", "queue_items_blocked",
    "output_rows", "output_file", "last_progress", "status",
)
DOWNSTREAM_FIELDS = ("raw_rows_unnormalized", "verified_claims",
                     "normalized_claims", "identity_bound",
                     "comparable_accepted")
STATUSES = ("running", "completed", "blocked", "failed")


def present(d: dict, key: str, default=ABSENT) -> Any:
    """Contract semantics: absent != null. Returns ABSENT when the key is
    missing so callers cannot mistake 'not measured' for 'measured zero'."""
    return d.get(key, default)


def measured(v: Any) -> bool:
    return v is not ABSENT


@dataclass
class LaneTelemetry:
    name: str = UNKNOWN
    extraction_type: Any = ABSENT
    extraction_version: Any = ABSENT
    queue_source: Any = ABSENT
    queue_items_total: Any = ABSENT
    queue_items_completed: Any = ABSENT
    queue_items_running: Any = ABSENT
    queue_items_ready: Any = ABSENT
    queue_items_failed: Any = ABSENT
    queue_items_blocked: Any = ABSENT
    output_rows: Any = ABSENT
    output_file: Any = ABSENT
    last_progress: Any = ABSENT
    status: Any = ABSENT
    # provenance carried separately from the contract fields
    evidence: str = "measured"
    source_id: str = ""
    measured_at: Any = ABSENT
    source_timestamp: Any = ABSENT

    def ready(self) -> Any:
        return self.queue_items_ready

    def is_input_blocked(self) -> bool:
        """Blocked by missing input (e.g. MACMOBILE), not by compute."""
        b = self.queue_items_blocked
        if measured(b) and isinstance(b, int) and b > 0:
            return True
        return self.status == "blocked"

    def is_compute_starved(self) -> bool:
        r, w = self.queue_items_ready, self.queue_items_running
        return (isinstance(r, int) and r == 0 and measured(w)
                and isinstance(w, int) and w == 0
                and self.status not in ("blocked",))


@dataclass
class MachineTelemetry:
    schema: str = SCHEMA
    timestamp: Any = ABSENT
    machine_id: str = UNKNOWN
    machine_role: Any = ABSENT
    hardware: dict = field(default_factory=dict)
    utilization: dict = field(default_factory=dict)
    services: list = field(default_factory=list)
    feeder: dict = field(default_factory=dict)
    lanes: list = field(default_factory=list)
    downstream: dict = field(default_factory=dict)
    telemetry_status: str = "unknown"
    unknown_fields: list = field(default_factory=list)
    source_id: str = ""
    mirror_ts: Any = ABSENT


@dataclass
class Interpretation:
    state: str = UNKNOWN          # productive | busy-but-useless | starved |
                                  # downstream-bottleneck | input-blocked |
                                  # insufficient-evidence
    reason: str = ""
    evidence: list[str] = field(default_factory=list)
    per_lane: dict = field(default_factory=dict)
    totals_by_unit: dict = field(default_factory=dict)
    unsummed_note: str = ("queue items are document-facet work units and are "
                          "never summed with documents, jobs or chunks")
    gaps: list[str] = field(default_factory=list)
    advisory: str = ("observation only — seven proposes, humans authorize")


# ------------------------------------------------------------------ parsing
def parse_record(d: dict, source_id: str = "") -> MachineTelemetry:
    m = MachineTelemetry()
    m.machine_id = str(d.get("machine_id") or UNKNOWN)
    m.machine_role = present(d, "machine_role")
    m.timestamp = present(d, "timestamp")
    m.hardware = dict(d.get("hardware") or {})
    m.utilization = dict(d.get("utilization") or {})
    m.services = list(d.get("services") or [])
    m.feeder = dict(d.get("feeder") or {})
    m.downstream = dict(d.get("downstream") or {})
    m.telemetry_status = str(d.get("telemetry_status") or "unknown")
    m.unknown_fields = list(d.get("unknown_fields") or [])
    m.source_id = source_id
    # Contract reconciliation: it says BOTH "never leave a field absent" and
    # "absence = unknown, null = measured none". The example record resolves
    # this by emitting null AND listing the field in unknown_fields. So a null
    # that is ALSO declared unknown is unknown, not measured-zero.
    declared_unknown = set(m.unknown_fields)

    def _v(raw: dict, f: str, lane: str = "") -> Any:
        if f not in raw:
            return ABSENT                     # absent -> unknown
        val = raw[f]
        if val is None and (f"lanes[{lane}].{f}" in declared_unknown
                            or f in declared_unknown):
            return ABSENT                     # null + declared -> unknown
        return val                            # null alone -> measured none/zero

    for raw in d.get("lanes") or []:
        ln = LaneTelemetry()
        ln.name = str(raw.get("name") or UNKNOWN)
        for f in LANE_FIELDS:
            if f == "name":
                continue
            setattr(ln, f, _v(raw, f, ln.name))
        ln.source_id = source_id
        m.lanes.append(ln)
    return m


def load_dir(path: str | Path, source_id: str = "telemetry-dir") -> list[MachineTelemetry]:
    """Contract delivery method 1: one JSON per machine, refreshed every 60s."""
    p = Path(path)
    if not p.exists() or not p.is_dir():
        return []
    out = []
    for f in sorted(p.glob("*.json")):
        try:
            out.append(parse_record(json.loads(f.read_text()),
                                    source_id=f"{source_id}:{f.name}"))
        except Exception:  # noqa: BLE001
            continue
    return out


def load_http(host: str, source_id: str = "telemetry-http",
              timeout: float = 5.0) -> Optional[MachineTelemetry]:
    """Contract delivery method 2: HTTP poll. Absent service = None, not zero."""
    import urllib.request
    try:
        with urllib.request.urlopen(DEFAULT_HTTP.format(host=host),
                                    timeout=timeout) as r:
            return parse_record(json.loads(r.read().decode()),
                                source_id=f"{source_id}:{host}")
    except Exception:  # noqa: BLE001
        return None


# ------------------------------------------------------------- interpretation
def _num(v: Any) -> Optional[float]:
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def interpret(m: MachineTelemetry,
              prior: Optional[MachineTelemetry] = None) -> Interpretation:
    """Apply the contract's distinction table. Never invents a state from
    partial telemetry; anything short of the required signals is
    insufficient-evidence with the missing signal named."""
    it = Interpretation()
    lanes = m.lanes
    ready_total = 0
    ready_measured = False
    blocked_lanes, starved_lanes = [], []
    output_rows = 0
    output_measured = False

    for ln in lanes:
        r = ln.queue_items_ready
        if isinstance(r, int):
            ready_total += r
            ready_measured = True
        elif ln.queue_items_ready is ABSENT:
            it.gaps.append(f"lane {ln.name}: queue_items_ready not measured")
        if measured(ln.output_rows) and isinstance(ln.output_rows, int):
            output_rows += ln.output_rows
            output_measured = True
        if ln.is_input_blocked():
            blocked_lanes.append(ln.name)
        elif ln.is_compute_starved():
            starved_lanes.append(ln.name)
        it.per_lane[ln.name] = {
            "extraction_type": ln.extraction_type if measured(ln.extraction_type) else UNKNOWN,
            "extraction_version": ln.extraction_version if measured(ln.extraction_version) else UNKNOWN,
            "ready": ln.queue_items_ready,
            "completed": ln.queue_items_completed,
            "running": ln.queue_items_running,
            "failed": ln.queue_items_failed,
            "blocked": ln.queue_items_blocked,
            "output_rows": ln.output_rows,
            "status": ln.status if measured(ln.status) else UNKNOWN,
            "evidence": ln.evidence,
        }
    # unit-typed total: queue items are one unit, never merged with documents
    it.totals_by_unit["document-facet-queue-items"] = ready_total \
        if ready_measured else UNKNOWN
    it.totals_by_unit["output-rows"] = output_rows if output_measured else UNKNOWN

    cpu = _num((m.utilization or {}).get("cpu_pct"))
    workers = sum(int(s.get("workers") or 0) for s in m.services
                  if isinstance(s, dict))
    feeder = m.feeder or {}
    feeder_status = feeder.get("status")
    heartbeat = feeder.get("heartbeat")
    ds = m.downstream or {}
    raw = _num(ds.get("raw_rows_unnormalized"))
    norm = _num(ds.get("normalized_claims"))
    ver = _num(ds.get("verified_claims"))

    if blocked_lanes and (ready_total == 0 or not ready_measured):
        it.state = "input-blocked"
        it.reason = (f"lane(s) {blocked_lanes} are BLOCKED awaiting input — "
                     f"missing input, not compute starvation")
        return it

    if cpu is None or feeder_status is None:
        it.state = "insufficient-evidence"
        it.reason = ("cannot classify: need cpu_pct AND feeder.status "
                     "(contract distinction table)")
        if cpu is None:
            it.gaps.append("utilization.cpu_pct")
        if feeder_status is None:
            it.gaps.append("feeder.status")
        return it

    stale_feeder = feeder_status in ("stalled", "stopped")
    stale_output = bool(measured(m.timestamp)) and output_measured and output_rows == 0

    if cpu < 20 and workers == 0 and (ready_measured and ready_total == 0) \
            and stale_feeder:
        it.state = "starved"
        it.reason = ("low cpu, zero workers, empty queue and a stalled feeder "
                     "— queue empty or feeder dead")
        it.evidence.append(f"cpu={cpu} workers={0} ready={ready_total} "
                           f"feeder={feeder_status}")
        return it

    if cpu >= 60 and workers > 0:
        queue_shrinking = any(
            isinstance(ln.queue_items_completed, int) and
            isinstance(ln.queue_items_ready, int)
            for ln in lanes)
        # A raw/normalised ratio in ONE snapshot cannot prove a bottleneck:
        # the contract's own productive example runs 135000 raw vs 9400
        # normalised. Only growth of the gap across snapshots proves it.
        divergence = (raw - norm) if (raw is not None and norm is not None)             else None
        if divergence is not None and divergence > 0:
            prior_div = None
            if prior is not None:
                praw = _num((prior.downstream or {}).get("raw_rows_unnormalized"))
                pnorm = _num((prior.downstream or {}).get("normalized_claims"))
                if praw is not None and pnorm is not None:
                    prior_div = praw - pnorm
            if prior_div is not None and divergence > prior_div * 1.2:
                it.state = "downstream-bottleneck"
                it.reason = (
                    "extraction is producing and the normalisation gap is "
                    f"GROWING ({prior_div:.0f} -> {divergence:.0f} rows) — "
                    f"verification/normalisation is falling behind")
                it.evidence.append("downstream divergence growth across "
                                   "snapshots")
                return it
            it.gaps.append(
                f"downstream divergence {divergence:.0f} rows observed "
                f"(raw {raw} vs normalised {norm}) — one snapshot cannot "
                f"distinguish pipeline depth from a bottleneck; needs a "
                f"second reading")
        if stale_output:
            it.state = "busy-but-useless"
            it.reason = ("high cpu and many workers but output is stale — "
                         "workers spinning on bad data")
            it.evidence.append(f"cpu={cpu} workers={workers} output_rows=0")
            return it
        if queue_shrinking or output_rows > 0:
            it.state = "productive"
            it.reason = ("high cpu, workers active, output flowing and/or "
                         "queues draining — normal extraction")
            it.evidence.append(f"cpu={cpu} workers={workers} "
                               f"output_rows={output_rows}")
            return it

    it.state = "insufficient-evidence"
    it.reason = "signals present but no contract state matched"
    it.gaps.append("queue_items_completed/output_rows per lane")
    return it


def machine_record(m: MachineTelemetry) -> dict:
    """Round-trip to the contract shape (for emitters and for persistence)."""
    out = {"schema": m.schema, "timestamp": m.timestamp,
           "machine_id": m.machine_id, "machine_role": m.machine_role,
           "hardware": m.hardware, "utilization": m.utilization,
           "services": m.services, "feeder": m.feeder,
           "lanes": [], "downstream": m.downstream,
           "telemetry_status": m.telemetry_status,
           "unknown_fields": m.unknown_fields}
    for ln in m.lanes:
        row = {}
        for f in LANE_FIELDS:
            v = getattr(ln, f)
            row[f] = None if v is ABSENT else v
        out["lanes"].append(row)
    return out
