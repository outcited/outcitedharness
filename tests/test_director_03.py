"""DIRECTOR-03 tests — §9 matrix: continuity, incidents, ownership, sessions,
fleet freshness, migration, and the preserved governance laws.

Older suites remain the base law (01/01A/01B/02 = 48 tests).
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from director_consumer.consumer import DirectorConsumer, FeedConfig
from director_consumer.continuity import FactoryContinuity
from director_consumer.fleet import FleetRegistry
from director_consumer.incidents import (IncidentStore, load_correlations)
from director_consumer.ownership import (Claim, OwnershipReconciler)
from director_consumer.schema import Assessment, SourceRef

ROOT = Path(__file__).resolve().parents[1]
DEPS = ROOT / "director_consumer" / "deps.yaml"
DESIGNWINS = ROOT / "projects" / "designwins"


def _assess(rule, sev="P1", kind="k", summary="s", action="propose_investigation",
            authority="agent:m5-opencode", tk=None):
    return Assessment(rule_id=rule, severity=sev, impacts=["fleet_infra"],
                      action=action, authority=authority, rationale="r",
                      evidence=[SourceRef("feed", "src", None, time.time())],
                      event_kind=kind, event_summary=summary,
                      trigger_key=tk or f"tk-{rule}-{time.time_ns()}")


def _store(tmp_path):
    con = sqlite3.connect(tmp_path / "d.db")
    deps = __import__("yaml").safe_load(DEPS.read_text())
    return con, IncidentStore(con, load_correlations(deps), project="designwins")


# ------------------------------------------------ §4 incident correlation

def test_symptoms_of_one_incident_group(tmp_path):
    con, st = _store(tmp_path)
    # queue_empty + starvation_risk share the factory-supply-exhaustion group
    a1 = _assess("factory-starvation", sev="P1", kind="queue_empty",
                 summary="extraction queues empty", tk="t1")
    a2 = _assess("factory-starvation", sev="P1", kind="starvation_risk",
                 summary="starvation in 0.0h", tk="t2")
    i1 = st.observe(a1)
    i2 = st.observe(a2)
    assert i1 == i2, "same-cause symptoms must become ONE incident"
    inc = st.open_incidents()
    assert len(inc) == 1
    assert inc[0]["reopen_count"] == 0
    ev = con.execute("SELECT COUNT(*) FROM incident_events "
                     "WHERE incident_id=?", (i1,)).fetchone()[0]
    assert ev == 2, "both symptoms linked to the incident"


def test_distinct_incidents_never_merge(tmp_path):
    con, st = _store(tmp_path)
    # temporally close but different correlation groups => NEVER merged
    a1 = _assess("factory-junk-flood", summary="AppleDouble 176709", tk="j1")
    a2 = _assess("factory-starvation", summary="starvation", tk="s1")
    i1, i2 = st.observe(a1), st.observe(a2)
    assert i1 != i2, "temporal closeness alone must not merge incidents"
    assert len(st.open_incidents()) == 2


def test_suspected_cause_never_verified(tmp_path):
    con, st = _store(tmp_path)
    iid = st.observe(_assess("factory-junk-flood"))
    row = con.execute("SELECT suspected_cause, verified_cause FROM incidents "
                      "WHERE incident_id=?", (iid,)).fetchone()
    assert row[0] and "AppleDouble" in row[0]
    assert row[1] is None, "suspected cause is never a verified cause"


def test_reopen_on_recurrence(tmp_path):
    con, st = _store(tmp_path)
    iid = st.observe(_assess("factory-junk-flood", tk="r1"))
    st.resolve(iid, "fixed upstream")
    assert st.open_incidents() == []
    st.observe(_assess("factory-junk-flood", tk="r2", summary="again"))
    inc = st.open_incidents()
    assert len(inc) == 1 and inc[0]["reopen_count"] == 1, \
        "a resolved incident recurring must reopen with history"


def test_incident_replay_no_duplicates(tmp_path):
    con, st = _store(tmp_path)
    a = _assess("factory-junk-flood", tk="dup-1")
    i1 = st.observe(a)
    i2 = st.observe(a)          # same assessment replayed
    assert i1 == i2
    assert len(st.open_incidents()) == 1
    assert con.execute("SELECT COUNT(*) FROM incidents").fetchone()[0] == 1


def test_watcher_failure_and_stale_lkg_one_incident(tmp_path):
    con, st = _store(tmp_path)
    i1 = st.observe(_assess("watcher-health-stale", tk="w1",
                            summary="sentinel stale"))
    i2 = st.observe(_assess("adjudicator-meaning-drift", tk="w2",
                            summary="GOLD DRIFT (stale LKG symptom)"))
    assert i1 == i2, "watcher failure + stale LKG = one incident"


# ------------------------------------------------ §2 continuity

def _factdb(p, queued=50):
    con = sqlite3.connect(p)
    con.executescript("CREATE TABLE factory_chunks (state TEXT, "
                      "lease_expires_at TEXT);")
    for _ in range(queued):
        con.execute("INSERT INTO factory_chunks VALUES ('queued', NULL)")
    con.commit(); con.close()


def test_starvation_forecast_with_valid_measurements(tmp_path):
    fc = FactoryContinuity(thresholds={"starvation_alert_h": 48})
    _factdb(tmp_path / "f.sqlite3", queued=100)
    fc.factory_db = tmp_path / "f.sqlite3"
    fc.facet_root = tmp_path / "fx"
    (tmp_path / "fx").mkdir()
    (tmp_path / "fx" / "done-a.txt").write_text("\n".join("x" * 1) or "a\n")
    (tmp_path / "fx" / "done-a.txt").write_text("a\n" * 200)
    obs = [{"t": 1000.0, "total": 100}, {"t": 4600.0, "total": 200}]
    m = fc.assess(obs)
    assert m.consumption_rate_docs_hr == 100 / 1.0
    assert m.starvation_basis == "measured"
    assert m.starvation_hours == pytest.approx(1.0, abs=0.05)
    kinds = [e.kind for e in fc.events(m)]
    assert "starvation_risk" in kinds


def test_starvation_unknown_rate_is_not_starvation(tmp_path):
    fc = FactoryContinuity(thresholds={"starvation_alert_h": 48})
    _factdb(tmp_path / "f.sqlite3", queued=100)
    fc.factory_db = tmp_path / "f.sqlite3"
    m = fc.assess([])   # no rate observations
    assert m.starvation_basis == "unknown-telemetry"
    kinds = [e.kind for e in fc.events(m)]
    assert "starvation_risk" not in kinds, \
        "missing telemetry must not be reported as starvation"
    assert any("NOT evidence of starvation" in n for n in m.notes)


def test_queues_unavailable_distinct_from_starvation(tmp_path):
    fc = FactoryContinuity()
    fc.factory_db = tmp_path / "missing.sqlite3"
    fc.pipeline_db = tmp_path / "missing2.sqlite3"
    m = fc.assess([])
    assert m.starvation_basis == "queues-unavailable"
    kinds = [e.kind for e in fc.events(m)]
    assert "starvation_risk" not in kinds
    assert "queue_empty" not in kinds, \
        "unavailable queues are never reported as empty"


def test_worker_hours_class_aware_with_unknowns(tmp_path):
    l0 = tmp_path / "l0.jsonl"
    rows = [
        {"source_path": "/vault/a.pdf", "doc_class": "datasheet",
         "page_count": 12},
        {"source_path": "/vault/b.pdf", "doc_class": "ref_manual",
         "page_count": 2000},
        {"source_path": "/vault/c.pdf", "doc_class": "mystery",
         "page_count": 5},
    ]
    l0.write_text("\n".join(json.dumps(r) for r in rows))
    backlog = tmp_path / "backlog.json"
    backlog.write_text(json.dumps([r["source_path"] for r in rows]))
    fc = FactoryContinuity(l0_index=l0, backlog_paths=backlog,
                           class_costs={"datasheet": {"hours_per_doc": 0.05,
                                                     "provenance": "bench"},
                                        "ref_manual": {"hours_per_doc": 2.0,
                                                      "provenance": "bench"}})
    m = fc.assess([])
    assert m.ready_by_class == {"datasheet": 1, "ref_manual": 1, "mystery": 1}
    assert m.pages_by_class["ref_manual"] == 2000, \
        "a 2000-page manual is not a 12-page datasheet"
    assert m.worker_hours_estimate == pytest.approx(2.05, abs=0.01)
    assert "PARTIAL" in m.worker_hours_note and "mystery" in m.worker_hours_note


def test_prioritized_supply_gap_reported_unknown(tmp_path):
    fc = FactoryContinuity()
    m = fc.assess([])
    assert m.ready_backlog_docs == "unknown"
    assert any("factory.sqlite3 not found" in n for n in m.notes), \
        "missing source must be named"


# ------------------------------------------------ §6 ownership

def test_conflicting_ownership_claims(tmp_path):
    rec = OwnershipReconciler().reconcile([
        Claim("agent-a", "EVIDENCE-SUPPLY-04", "commit", "commit:aaaa1111",
              ts=time.time(), strength=2),
        Claim("agent-b", "EVIDENCE-SUPPLY-04", "branch", "branch:b",
              ts=time.time(), strength=2),
    ], time.time())
    assert rec[0].state == "conflicting"
    assert "settle who owns" in rec[0].recommendation
    evs = OwnershipReconciler().conflict_events(rec)
    assert evs and evs[0]["kind"] == "ownership_conflict"


def test_mailbox_hint_is_never_proof(tmp_path):
    rec = OwnershipReconciler().reconcile([
        Claim("m5-opencode", "TASK-XYZ-1", "commit", "commit:x",
              ts=time.time(), strength=2),
        Claim("other-agent", "TASK-XYZ-1", "mailbox", "mail.md",
              ts=time.time(), strength=1),
    ], time.time())
    assert rec[0].state == "confirmed-owner"
    assert rec[0].claimants == ["m5-opencode"], \
        "a mailbox hint must not create a second claimant"


def test_unknown_agent_never_establishes_ownership():
    rec = OwnershipReconciler().reconcile([
        Claim("unknown", "TASK-XYZ-2", "commit", "commit:y",
              ts=time.time(), strength=2),
    ], time.time())
    assert rec[0].state == "unknown"


def test_stale_assignment_flagged():
    old = time.time() - 10 * 86400
    rec = OwnershipReconciler(stale_days=7).reconcile([
        Claim("agent-a", "TASK-OLD-1", "commit", "commit:z", ts=old,
              strength=2)], time.time())
    assert rec[0].state == "stale"
    assert "status check" in rec[0].recommendation


# ------------------------------------------------ §5 sessions

def test_session_disconnect_is_not_completion(tmp_path):
    from director_consumer.sessions import SessionRecord, SessionDiscovery
    recs = [SessionRecord(session_id="ses_1", task_ids=["TASK-A-1"],
                          process_health="no-process-observed",
                          title="TASK-A-1 work")]
    evs = SessionDiscovery.to_events(recs)
    assert evs and evs[0]["kind"] == "session_disconnected"
    assert "NOT proof" in evs[0]["summary"]


def test_session_title_never_becomes_agent_identity(tmp_path):
    from director_consumer.sessions import SessionDiscovery
    db = tmp_path / "opencode.db"
    con = sqlite3.connect(db)
    con.executescript("""
CREATE TABLE session (id TEXT PRIMARY KEY, title TEXT, time_created INTEGER,
 parent_id TEXT, directory TEXT, branch TEXT);""")
    con.execute("INSERT INTO session VALUES ('s1','Fix everything as "
                "the extraction lead',1791900000000,'', '/repo', 'main')")
    con.commit(); con.close()
    d = SessionDiscovery(opencode_db=db, machine="m5")
    recs = d.discover()
    assert recs and recs[0].agent == "unknown", \
        "a session title is not an agent identity"


# ------------------------------------------------ §7 fleet freshness/migration

def test_stale_snapshot_never_reads_healthy(tmp_path):
    snapdir = tmp_path / "snap"
    snapdir.mkdir()
    (snapdir / "20261001T000000Z.json").write_text(json.dumps({
        "timestamp": "2026-10-01T00:00:00Z",
        "endpoints": {"asus4_canon": ["qwen3.8-27b-fp8"]},
        "nodes": {"asus4": ["canon-qwen38-27b"]}}))
    reg = FleetRegistry(fleet_yaml=DESIGNWINS / "fleet.yaml",
                        snap_dir=snapdir, tailscale={})
    h = reg.machine_health("asus4")
    assert h["verdict"] != "healthy", \
        "stale service telemetry must never read healthy"
    assert "STALE" in reg.source_notes.get("fleet_snapshot", "")


def test_fresh_snapshot_can_read_healthy(tmp_path):
    snapdir = tmp_path / "snap"
    snapdir.mkdir()
    from datetime import datetime, timezone
    now_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    (snapdir / "20991231T000000Z.json").write_text(json.dumps({
        "timestamp": now_ts,
        "endpoints": {"asus4_canon": ["qwen3.8-27b-fp8"]},
        "nodes": {"asus4": ["canon-qwen38-27b"]}}))
    reg = FleetRegistry(fleet_yaml=DESIGNWINS / "fleet.yaml",
                        snap_dir=snapdir, tailscale={})
    assert reg.machine_health("asus4")["verdict"] == "healthy"


def test_no_localhost_duplicate_and_self_identity(tmp_path):
    ts = {"Self": {"HostName": "m5max-ai.local",
                   "TailscaleIPs": ["100.106.13.24"], "Online": True},
          "Peer": {"p1": {"HostName": "localhost", "Online": False,
                          "TailscaleIPs": [], "OS": "macOS"},
                   "p2": {"HostName": "asus2", "Online": True,
                          "TailscaleIPs": ["100.68.133.1"], "OS": "linux"}}}
    reg = FleetRegistry(fleet_yaml=DESIGNWINS / "fleet.yaml",
                        snap_dir=None, tailscale=ts)
    assert "localhost" not in reg.machines, "localhost must never appear"
    assert reg.machines["m5"].connectivity == "online", \
        "self must resolve to the canonical machine id"


def test_migration_state_surfaces():
    reg = FleetRegistry(fleet_yaml=DESIGNWINS / "fleet.yaml",
                        snap_dir=None, tailscale={})
    h = reg.machine_health("m5")
    assert h["status"] == "migrating" and "migrat" in h["migration"].lower()


# ------------------------------------------------ preserved governance laws

def test_metered_refusal_still_enforced():
    from director_consumer.policy import ModelPolicy
    pol = ModelPolicy.load(DESIGNWINS / "policy.yaml")
    ok, why = pol.check("openrouter", "probe")
    assert not ok and "METERED REFUSED" in why


def test_total_model_outage_including_subscription(tmp_path):
    from director_consumer.llm import Endpoint, OpsBrainClient
    from director_consumer.policy import ModelPolicy
    pol = ModelPolicy.load(DESIGNWINS / "policy.yaml")
    dead = OpsBrainClient(endpoints=[
        Endpoint("glm53_sub", "http://127.0.0.1:1", "m"),
        Endpoint("mimo_pro", "http://127.0.0.1:2", "m")],
        timeout_s=0.3, policy=pol, project="designwins")
    ws = tmp_path / "ws"
    p = tmp_path / "CHANGES.md"
    p.write_text("## 2026-10-16T23:59:00Z\n"
                 "- asus4: ['canon-qwen38-27b'] -> ['NO_CONTAINERS']\n")
    c = DirectorConsumer(ws, DEPS,
                         feeds={"fleet_changes": FeedConfig(args={"path": p})},
                         llm=dead, project_dir=DESIGNWINS)
    r = c.run_once()
    c.close()
    assert r.assessments > 0, "ingestion survives total model outage"
    assert r.proposals_new >= 1


def test_two_projects_isolated_memory(tmp_path):
    p = tmp_path / "CHANGES.md"
    p.write_text("## 2026-10-16T23:59:30Z\n- m5: ['a'] -> ['b']\n")
    feeds = {"fleet_changes": FeedConfig(args={"path": p})}
    ws1, ws2 = tmp_path / "p1", tmp_path / "p2"
    for ws in (ws1, ws2):
        c = DirectorConsumer(ws, DEPS, feeds=feeds, project_dir=DESIGNWINS)
        c.run_once()
        c.close()
    n1 = sqlite3.connect(ws1 / "director.db").execute(
        "SELECT COUNT(*) FROM incidents").fetchone()[0]
    n2 = sqlite3.connect(ws2 / "director.db").execute(
        "SELECT COUNT(*) FROM incidents").fetchone()[0]
    assert n1 == n2  # independent, identical workspaces
    assert ws1.joinpath("director.db").stat().st_ino != \
        ws2.joinpath("director.db").stat().st_ino
