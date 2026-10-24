"""DIRECTOR-06 — extraction telemetry contract (harness.extraction-telemetry.v1).

The contract's own rule is the one that prevents false starvation:
ABSENT = unknown, explicit NULL = measured none/zero. Collapsing them is how
an empty legacy queue becomes a starvation claim.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from director_consumer.telemetry import (
    ABSENT, SCHEMA, LaneTelemetry, MachineTelemetry, interpret, load_dir,
    machine_record, measured, parse_record, present,
)


def rec(**over):
    base = {
        "schema": SCHEMA, "timestamp": "2026-10-18T07:00:00Z",
        "machine_id": "happy", "machine_role": "extraction",
        "hardware": {"cpu_threads": 32, "ram_total_mb": 61440},
        "utilization": {"cpu_pct": 90.8, "ram_used_mb": 18018,
                        "load_average": 10.5},
        "services": [{"name": "extract-thermal-metrics", "status": "active",
                      "workers": 33, "restarts": 0}],
        "feeder": {"heartbeat": "2026-10-18T06:59:23Z", "status": "running"},
        "lanes": [{
            "name": "thermal_metrics", "extraction_type": "thermal",
            "extraction_version": "deterministic_thermal_metrics_v1",
            "queue_source": "/tmp/vault-queue.json",
            "queue_items_total": 118795, "queue_items_completed": 3692,
            "queue_items_running": 33, "queue_items_ready": 115103,
            "queue_items_failed": 0, "queue_items_blocked": 0,
            "output_rows": 3692,
            "output_file": "/home/samsonkim/lane-out/thermal-vault.jsonl",
            "last_progress": "2026-10-18T06:59:00Z", "status": "running"}],
        "downstream": {"raw_rows_unnormalized": 135000,
                       "verified_claims": 9400, "normalized_claims": 9400,
                       "identity_bound": None, "comparable_accepted": None,
                       "note": "measured via dw-bridge canary on 100-record samples"},
        "telemetry_status": "complete",
        "unknown_fields": ["identity_bound", "comparable_accepted"],
    }
    base.update(over)
    return base


# ---------------------------------------------- absent vs null (the rule)
def test_absent_is_unknown_and_null_is_measured_none():
    d = rec()
    del d["lanes"][0]["queue_items_failed"]      # ABSENT -> unknown
    d["lanes"][0]["queue_items_blocked"] = None  # NULL -> measured none/zero
    m = parse_record(d)
    ln = m.lanes[0]
    assert ln.queue_items_failed is ABSENT
    assert not measured(ln.queue_items_failed)
    assert ln.queue_items_blocked is None
    assert measured(ln.queue_items_blocked)
    assert ln.queue_items_blocked == 0 or ln.queue_items_blocked is None


def test_absent_ready_is_never_reported_as_zero():
    d = rec()
    del d["lanes"][0]["queue_items_ready"]
    it = interpret(parse_record(d))
    assert it.totals_by_unit["document-facet-queue-items"] == "unknown"
    assert any("queue_items_ready not measured" in g for g in it.gaps)


def test_null_ready_is_a_measured_zero_and_still_not_starvation_alone():
    d = rec()
    d["lanes"][0]["queue_items_ready"] = None
    it = interpret(parse_record(d))
    # null = measured zero, so the total is 0, but one lane's zero is not a
    # factory verdict on its own
    assert it.state in ("productive", "busy-but-useless",
                        "insufficient-evidence", "starved")
    assert it.state != "starved", \
        "a measured zero with a healthy feeder is not starvation"


# --------------------------------------------- the contract distinction table
def test_scenario_productive():
    it = interpret(parse_record(rec()))
    assert it.state == "productive"
    assert "normal extraction" in it.reason


def test_scenario_busy_but_useless():
    d = rec()
    d["lanes"][0]["output_rows"] = 0
    d["lanes"][0]["queue_items_completed"] = 0
    it = interpret(parse_record(d))
    assert it.state == "busy-but-useless"
    assert "spinning on bad data" in it.reason


def test_scenario_starved():
    d = rec()
    d["utilization"] = {"cpu_pct": 10.0, "load_average": 0.5}
    d["services"] = [{"name": "work-feeder", "status": "stopped",
                      "workers": 0, "restarts": 0}]
    d["feeder"] = {"heartbeat": "2026-10-18T01:00:00Z", "status": "stalled"}
    d["lanes"][0].update({"queue_items_ready": 0, "queue_items_running": 0,
                          "output_rows": 0, "status": "failed"})
    it = interpret(parse_record(d))
    assert it.state == "starved"
    assert "queue empty or feeder dead" in it.reason


def test_scenario_downstream_bottleneck_needs_growth_across_snapshots():
    """One snapshot cannot prove a bottleneck — the contract's own productive
    example runs 135000 raw vs 9400 normalised. Only a GROWING gap does."""
    d = rec()
    d["downstream"] = {"raw_rows_unnormalized": 135000,
                       "verified_claims": 9400, "normalized_claims": 9400,
                       "identity_bound": None, "comparable_accepted": None}
    it = interpret(parse_record(d))
    assert it.state != "downstream-bottleneck", \
        "a static raw/normalised ratio is not proof of a bottleneck"
    assert any("second reading" in g for g in it.gaps)

    later = rec()
    later["downstream"] = {"raw_rows_unnormalized": 400000,
                           "verified_claims": 11000,
                           "normalized_claims": 10000,
                           "identity_bound": None, "comparable_accepted": None}
    it2 = interpret(parse_record(later), prior=parse_record(d))
    assert it2.state == "downstream-bottleneck"
    assert "GROWING" in it2.reason


def test_single_snapshot_with_widening_hint_is_not_overclaimed():
    d = rec()
    d["downstream"] = {"raw_rows_unnormalized": 200000,
                       "verified_claims": 100, "normalized_claims": 100,
                       "identity_bound": None, "comparable_accepted": None}
    it = interpret(parse_record(d))
    assert it.state != "downstream-bottleneck"
    assert any("cannot distinguish pipeline depth" in g for g in it.gaps)


def test_input_blocked_lanes_are_not_compute_starved():
    d = rec()
    d["lanes"][0].update({"queue_items_blocked": 400,
                          "queue_items_ready": 0, "status": "blocked"})
    it = interpret(parse_record(d))
    assert it.state == "input-blocked"
    assert "missing input, not compute starvation" in it.reason


def test_missing_cpu_or_feeder_is_insufficient_not_zero():
    d = rec()
    del d["utilization"]
    it = interpret(parse_record(d))
    assert it.state == "insufficient-evidence"
    assert "utilization.cpu_pct" in it.gaps
    d2 = rec()
    d2["feeder"] = {}
    it2 = interpret(parse_record(d2))
    assert it2.state == "insufficient-evidence"
    assert "feeder.status" in it2.gaps


def test_never_sum_queue_items_with_documents():
    it = interpret(parse_record(rec()))
    assert "document-facet-queue-items" in it.totals_by_unit
    assert not any(k in it.totals_by_unit for k in ("document", "total", "job"))
    assert "never summed" in it.unsummed_note


# ---------------------------------------------------------- crash-loop detect
def test_service_restart_loop_is_reported():
    d = rec()
    d["services"].append({"name": "extract-connector", "status": "failed",
                          "workers": 0, "restarts": 17})
    m = parse_record(d)
    loops = [s["name"] for s in m.services
             if isinstance(s, dict) and (s.get("restarts") or 0) > 5]
    assert "extract-connector" in loops


# ------------------------------------------------------------------ round-trip
def test_machine_record_round_trips_with_explicit_nulls():
    d = rec()
    del d["lanes"][0]["queue_items_failed"]
    m = parse_record(d)
    out = machine_record(m)
    assert out["schema"] == SCHEMA
    assert out["lanes"][0]["queue_items_failed"] is None, \
        "absent becomes explicit null on emit (contract: never leave absent)"
    assert "queue_items_blocked" in out["lanes"][0]


def test_load_dir_reads_one_json_per_machine(tmp_path):
    (tmp_path / "happy.json").write_text(json.dumps(rec(machine_id="happy")))
    (tmp_path / "m5.json").write_text(json.dumps(
        rec(machine_id="m5", machine_role="flexible")))
    (tmp_path / "broken.json").write_text("{not json")
    got = load_dir(tmp_path)
    assert sorted(x.machine_id for x in got) == ["happy", "m5"]


def test_load_dir_absent_path_yields_no_machines_not_zeroed():
    assert load_dir("/nonexistent/extraction-telemetry") == []


def test_null_plus_declared_unknown_is_unknown_not_zero():
    """The contract says 'never leave a field absent' AND 'absence = unknown,
    null = measured none'. Its example record resolves this by emitting null
    AND listing the field in unknown_fields. Those must read as unknown."""
    d = rec()
    d["lanes"][0]["queue_items_ready"] = None
    d["unknown_fields"] = ["lanes[thermal_metrics].queue_items_ready"]
    m = parse_record(d)
    assert m.lanes[0].queue_items_ready is ABSENT, \
        "null + declared unknown is unknown, not a measured zero"
    it = interpret(m)
    assert it.totals_by_unit["document-facet-queue-items"] == "unknown"
    assert any("queue_items_ready not measured" in g for g in it.gaps)


def test_null_without_declaration_is_measured_none():
    d = rec()
    d["lanes"][0]["queue_items_blocked"] = None   # not in unknown_fields
    m = parse_record(d)
    assert m.lanes[0].queue_items_blocked is None
    assert measured(m.lanes[0].queue_items_blocked)
