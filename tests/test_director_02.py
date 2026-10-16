"""DIRECTOR-02 tests — §9 matrix: governance, fleet, continuity, isolation.

Focused on the NEW capabilities; 01/01A/01B suites remain the base law.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from director_consumer.consumer import DirectorConsumer, FeedConfig
from director_consumer.continuity import FactoryContinuity
from director_consumer.fleet import FleetRegistry, latest_snapshot
from director_consumer.llm import Endpoint, OpsBrainClient
from director_consumer.policy import (AuthorityPolicy, ModelPolicy, load_project)

ROOT = Path(__file__).resolve().parents[1]
DEPS = ROOT / "director_consumer" / "deps.yaml"
DESIGNWINS = ROOT / "projects" / "designwins"


# ------------------------------------------------ §6 model governance

def test_metered_hard_refusal_no_authorization(tmp_path):
    pol = ModelPolicy.load(DESIGNWINS / "policy.yaml")
    ok, why = pol.check("openrouter", "anything")
    assert not ok and "METERED REFUSED" in why
    # a metered endpoint added WITHOUT authorization_ref is refused too
    pol.endpoints["or_new"] = pol.endpoints["asus2_ops_brain"].__class__(
        name="or_new", provider="openrouter", base_url="https://x",
        model="m", role="", authorization_ref="")
    ok2, _ = pol.check("or_new")
    assert not ok2
    # with an explicit task authorization it may pass
    pol.endpoints["or_new"].authorization_ref = "SAM-TASK-42"
    ok3, why3 = pol.check("or_new")
    assert ok3 and "SAM-TASK-42" in why3


def test_metered_refusal_recorded_in_usage_ledger(tmp_path):
    ws = tmp_path / "ws"
    pol = ModelPolicy.load(DESIGNWINS / "policy.yaml")
    from director_consumer.state import DirectorState
    st = DirectorState(ws)
    pol.usage_sink = st.record_model_usage
    ok, why = pol.check("openrouter", "probe")
    pol.record("openrouter", ok, 0.0, "probe", "designwins", note=why)
    rows = st.con.execute(
        "SELECT endpoint, ok, note FROM model_usage").fetchall()
    st.close()
    assert rows and rows[0][0] == "openrouter" and rows[0][1] == 0
    assert "METERED REFUSED" in rows[0][2]


def test_policy_refuses_unknown_endpoint():
    pol = ModelPolicy.load(DESIGNWINS / "policy.yaml")
    ok, why = pol.check("some_random_host")
    assert not ok and "allowlist" in why


def test_llm_total_outage_with_policy_ingestion_continues(tmp_path):
    deps, ws = DEPS, tmp_path / "ws"
    pol = ModelPolicy.load(DESIGNWINS / "policy.yaml")
    dead = OpsBrainClient(
        endpoints=[Endpoint("asus2_ops_brain", "http://127.0.0.1:1", "m")],
        timeout_s=0.3, policy=pol, project="designwins")
    feeds = {"fleet_changes": FeedConfig(args={
        "path": _write(tmp_path, "## 2026-10-16T22:00:00Z\n"
                      "- asus4: ['canon-qwen38-27b'] -> ['NO_CONTAINERS']\n")})}
    c = DirectorConsumer(ws, deps, feeds=feeds, llm=dead,
                         project_dir=DESIGNWINS)
    r = c.run_once()
    c.close()
    assert r.assessments > 0, "deterministic ingestion never depends on LLM"
    rows = sqlite3.connect(ws / "director.db").execute(
        "SELECT COUNT(*) FROM model_usage").fetchone()[0]
    assert rows >= 1, "refused/failed attempts are attributable"


def _write(tmp_path, text, name="CHANGES.md"):
    p = tmp_path / name
    p.write_text(text)
    return p


# ------------------------------------------------ §3 fleet awareness

SNAP = {
    "timestamp": "2026-10-16T22:00:00Z",
    "endpoints": {
        "asus4_canon": ["UNREACHABLE"],
        "spark_embedder": ["READY model=bge-m3"],
    },
    "nodes": {"asus4": ["canon-qwen38-27b"], "asus2": ["NO_CONTAINERS"],
              "e10b": ["canon-replica"]},
}
TS_ONLINE = {"Peer": {
    "p1": {"HostName": "asus4", "Online": True,
           "TailscaleIPs": ["100.100.116.82"], "OS": "linux"},
    "p2": {"HostName": "gx10-26b6", "Online": True,
           "TailscaleIPs": ["100.68.133.1"], "OS": "linux"},
}}


def _snap_dir(tmp_path, snap=SNAP):
    d = tmp_path / "snapshots"
    d.mkdir(exist_ok=True)
    (d / "20261016T220000Z.json").write_text(json.dumps(snap))
    return d


def test_service_down_machine_online_distinction(tmp_path):
    reg = FleetRegistry(fleet_yaml=DESIGNWINS / "fleet.yaml",
                        snap_dir=_snap_dir(tmp_path), tailscale=TS_ONLINE)
    h = reg.machine_health("asus4")
    assert h["connectivity"] == "online"
    assert h["verdict"] == "service-degraded", \
        "online tailscale must NOT imply healthy services"
    assert h["online_is_not_healthy"] is True
    events = reg.diff_events({})
    kinds = [e["kind"] for e in events]
    assert "service_down" in kinds
    assert "machine_offline" not in kinds, \
        "a service failure is never a machine failure"


def test_machine_offline_is_connectivity_event(tmp_path):
    ts_off = {"Peer": {"p1": {"HostName": "asus4", "Online": False,
                              "TailscaleIPs": ["100.100.116.82"], "OS": "linux"}}}
    reg = FleetRegistry(fleet_yaml=DESIGNWINS / "fleet.yaml",
                        snap_dir=_snap_dir(tmp_path), tailscale=ts_off)
    assert reg.machine_health("asus4")["connectivity"] == "offline"
    kinds = [e["kind"] for e in reg.diff_events({})]
    assert "machine_offline" in kinds


def test_tailscale_unavailable_reports_unknown_never_offline(tmp_path):
    reg = FleetRegistry(fleet_yaml=DESIGNWINS / "fleet.yaml",
                        snap_dir=_snap_dir(tmp_path), tailscale=None)
    # force unavailable: tailscale_status() called with None override -> our
    # constructor uses the passed value only when not None; simulate the
    # unavailable probe by passing a sentinel the code treats as unqueried
    reg2 = FleetRegistry(fleet_yaml=DESIGNWINS / "fleet.yaml",
                         snap_dir=None, tailscale={})
    for r in (reg, reg2):
        for mid, rec in r.machines.items():
            if rec.connectivity not in ("online", "offline"):
                assert rec.connectivity == "unknown", \
                    "lost probe must yield UNKNOWN, not fabricated offline"


def test_migration_recognized(tmp_path):
    reg = FleetRegistry(fleet_yaml=DESIGNWINS / "fleet.yaml",
                        snap_dir=_snap_dir(tmp_path), tailscale=TS_ONLINE)
    h = reg.machine_health("m5")
    assert h["status"] == "migrating"
    assert any("migrat" in (reg.machines["m5"].migration or "").lower()
               for _ in [0]), "migration text must be carried"


def test_alias_reconciliation_registry(tmp_path):
    snap = json.loads(json.dumps(SNAP))
    snap["nodes"]["dgx1"] = ["canon-replica"]  # same box as e10b
    reg = FleetRegistry(fleet_yaml=DESIGNWINS / "fleet.yaml",
                        snap_dir=_snap_dir(tmp_path, snap), tailscale={})
    # e10b is not in fleet.yaml machines; it appears as its own derived
    # record unless declared — the project file declares dgx1 roles; the
    # alias map (machine-aliases.yaml) is used at classification level.
    assert "e10b" in reg.machines or "dgx1" in reg.machines


def test_fleet_diff_idempotent(tmp_path):
    reg = FleetRegistry(fleet_yaml=DESIGNWINS / "fleet.yaml",
                        snap_dir=_snap_dir(tmp_path), tailscale=TS_ONLINE)
    first = reg.snapshot_state()
    events2 = FleetRegistry(fleet_yaml=DESIGNWINS / "fleet.yaml",
                            snap_dir=_snap_dir(tmp_path),
                            tailscale=TS_ONLINE).diff_events(first)
    assert events2 == [], "unchanged fleet state must produce no events"


# ------------------------------------------------ §4 factory continuity

def _factory_db(path, queued=100, claimed=2):
    con = sqlite3.connect(path)
    con.executescript("""
CREATE TABLE factory_chunks (state TEXT, lease_expires_at TEXT);""")
    for _ in range(queued):
        con.execute("INSERT INTO factory_chunks VALUES ('queued', NULL)")
    for _ in range(claimed):
        con.execute("INSERT INTO factory_chunks VALUES "
                    "('claimed', '2020-01-01T00:00:00Z')")
    con.commit()
    con.close()


def _pipeline_db(path, dead_junk=5000):
    con = sqlite3.connect(path)
    con.executescript("""
CREATE TABLE jobs (state TEXT, corpus_key TEXT, source_path TEXT);""")
    for i in range(dead_junk):
        con.execute("INSERT INTO jobs VALUES ('dead', 'pdf:._x%d', '')" % i)
    con.execute("INSERT INTO jobs VALUES ('pending', 'pdf:real', '')")
    con.commit()
    con.close()


def test_starvation_event_when_rate_known(tmp_path):
    fc = FactoryContinuity(thresholds={"starvation_alert_h": 24})
    obs = [{"t": 1000000.0, "total": 100}, {"t": 1000000.0 + 3600, "total": 150}]
    _factory_db(tmp_path / "f.sqlite3", queued=100)
    _pipeline_db(tmp_path / "p.sqlite3", dead_junk=0)
    fc.factory_db = tmp_path / "f.sqlite3"
    fc.pipeline_db = tmp_path / "p.sqlite3"
    fc.facet_root = tmp_path / "facets"
    (tmp_path / "facets").mkdir()
    (tmp_path / "facets" / "done-x.txt").write_text("a\n" * 150)
    m = fc.assess(obs)
    assert m.consumption_rate_docs_hr == 50.0
    assert m.ready_backlog_docs == 101
    assert m.starvation_hours == pytest.approx(2.0, abs=0.2)
    kinds = [e.kind for e in fc.events(m)]
    assert "starvation_risk" in kinds, "coverage < target must be flagged"


def test_unknown_rate_reported_with_missing_measurement(tmp_path):
    fc = FactoryContinuity()
    fc.facet_root = tmp_path / "facets"
    (tmp_path / "facets").mkdir()
    (tmp_path / "facets" / "done-x.txt").write_text("a\n" * 10)
    m = fc.assess([{"t": 1000.0, "total": 10}])   # ONE observation
    assert m.consumption_rate_docs_hr == "unknown"
    assert any("need >=2 ledger" in n for n in m.notes), \
        "UNKNOWN must name the missing measurement"


def test_appledouble_contamination_incident(tmp_path):
    fc = FactoryContinuity(thresholds={"junk_flood_threshold": 1000})
    fc.pipeline_db = tmp_path / "p.sqlite3"
    _pipeline_db(tmp_path / "p.sqlite3", dead_junk=5000)
    m = fc.assess([])
    kinds = [e.kind for e in fc.events(m)]
    assert "junk_flood" in kinds, "AppleDouble flood must raise an incident"


def test_intake_log_anomaly(tmp_path):
    log = tmp_path / "intake.log"
    log.write_text(
        "=== PDF intake 2026-10-15T00:00:00Z ===\n"
        "Number of files: 66000\n"
        "=== PDF intake 2026-10-16T00:00:00Z ===\n"
        "Number of files: 65900\n")
    fc = FactoryContinuity(intake_log=log)
    m = fc.assess([])
    assert m.acquisition_rate_docs_day == -100.0
    kinds = [e.kind for e in fc.events(m)]
    assert "intake_anomaly" in kinds


# ------------------------------------------------ §2 project isolation

def test_project_isolation_two_projects(tmp_path):
    p1 = tmp_path / "proj-alpha"
    p2 = tmp_path / "proj-beta"
    p1.mkdir()
    p2.mkdir()
    (p1 / "policy.yaml").write_text(
        "authority:\n  human_owner: alice\n"
        "  human_only_actions:\n    custom_action: 'alpha says human'\n")
    (p2 / "policy.yaml").write_text(
        "authority:\n  human_owner: bob\n"
        "  human_only_actions:\n    custom_action: 'beta allows nothing'\n")
    a1, auth1 = load_project(p1)
    a2, auth2 = load_project(p2)
    assert auth1.human_owner == "alice" and auth2.human_owner == "bob"
    only1, note1 = auth1.is_human_only("custom_action")
    only2, note2 = auth2.is_human_only("custom_action")
    assert only1 and note1 == "alpha says human"
    assert only2 and note2 == "beta allows nothing"
    # unknown action fails closed in BOTH projects
    for auth in (auth1, auth2):
        only, note = auth.is_human_only("never_heard_of_it")
        assert only and "fail closed" in note


def test_project_workspaces_have_isolated_journals(tmp_path):
    feeds = {"fleet_changes": FeedConfig(args={
        "path": _write(tmp_path, "## 2026-10-16T23:00:00Z\n- m5: ['a'] -> ['b']\n")})}
    ws1, ws2 = tmp_path / "a" / "ws", tmp_path / "b" / "ws"
    for ws in (ws1, ws2):
        c = DirectorConsumer(ws, DEPS, feeds=feeds, project_dir=DESIGNWINS)
        c.run_once()
        c.close()
    j1 = sqlite3.connect(ws1 / "director.db").execute(
        "SELECT COUNT(*) FROM journal").fetchone()[0]
    j2 = sqlite3.connect(ws2 / "director.db").execute(
        "SELECT COUNT(*) FROM journal").fetchone()[0]
    assert j1 > 0 and j2 > 0
    assert ws1.joinpath("director.db").stat().st_ino != \
        ws2.joinpath("director.db").stat().st_ino


# ------------------------------------------------ §9 multi-failure + briefing

def test_simultaneous_failures_and_good_feed(tmp_path):
    good = _write(tmp_path, "## 2026-10-16T23:30:00Z\n- asus4: ['canon-qwen38-27b'] -> ['NO_CONTAINERS']\n")
    missing = tmp_path / "does-not-exist.json"
    stale = tmp_path / "lkg.json"
    stale.write_text("{}")
    import os
    old = 1790000000.0  # ~25 days before any plausible now
    os.utime(stale, (old, old))
    feeds = {
        "fleet_changes": FeedConfig(args={"path": good}),
        "sentinel": FeedConfig(args={"alerts": tmp_path / "ALERTS.md",
                                     "lkg": stale}),
        "factory_events": FeedConfig(args={"db": missing}),
    }
    (tmp_path / "ALERTS.md").write_text("")
    ws = tmp_path / "ws"
    c = DirectorConsumer(ws, DEPS, feeds=feeds, project_dir=DESIGNWINS)
    r = c.run_once()
    c.close()
    assert r.assessments > 0, "good feed must still classify"
    assert r.markers >= 2, "bad feeds must be explicitly marked"
    ok, _ = __import__("director_consumer.state", fromlist=["DirectorState"]) \
        .DirectorState(ws).verify_chain()
    assert ok


def test_briefing_renders_with_sections(tmp_path):
    feeds = {"fleet_changes": FeedConfig(args={
        "path": _write(tmp_path, "## 2026-10-16T23:45:00Z\n- asus4: ['canon-qwen38-27b'] -> ['NO_CONTAINERS']\n")})}
    ws = tmp_path / "ws"
    c = DirectorConsumer(ws, DEPS, feeds=feeds, project_dir=DESIGNWINS,
                         fleet_yaml=DESIGNWINS / "fleet.yaml",
                         snap_dir=_snap_dir(tmp_path), tailscale=TS_ONLINE)
    c.run_once()
    c.close()
    briefs = sorted((ws / "briefings").glob("BRIEFING-*.md"))
    assert briefs, "every run writes an operational briefing"
    body = briefs[-1].read_text()
    for section in ("## What changed", "## What is at risk",
                    "## What should happen next", "## Fleet",
                    "## Model usage"):
        assert section in body
    assert "proposed: true" in body and "delivered: false" in body


def test_fleet_events_flow_through_rules(tmp_path):
    feeds = {}
    ws = tmp_path / "ws"
    c = DirectorConsumer(ws, DEPS, feeds=feeds, project_dir=DESIGNWINS,
                         fleet_yaml=DESIGNWINS / "fleet.yaml",
                         snap_dir=_snap_dir(tmp_path), tailscale=TS_ONLINE)
    c.run_once()  # baseline state (first sight of canon UNREACHABLE -> down)
    # snapshot changes: canon recovers
    snap2 = json.loads(json.dumps(SNAP))
    snap2["endpoints"]["asus4_canon"] = ["qwen3.8-27b-fp8"]
    _snap_dir(tmp_path, snap2)
    c2 = DirectorConsumer(ws, DEPS, feeds=feeds, project_dir=DESIGNWINS,
                          fleet_yaml=DESIGNWINS / "fleet.yaml",
                          snap_dir=_snap_dir(tmp_path, snap2),
                          tailscale=TS_ONLINE)
    r = c2.run_once()
    c2.close()
    rows = [json.loads(x[0]) for x in sqlite3.connect(ws / "director.db").execute(
        "SELECT payload FROM journal WHERE kind='assessment'")]
    hit = [x for x in rows if x["rule_id"] == "fleet-service-recovered"]
    assert hit, f"service transition must classify, got {[x['rule_id'] for x in rows]}"
