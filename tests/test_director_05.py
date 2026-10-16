"""DIRECTOR-05 tests — §10 two-project acceptance + §11 failure injection.

Project B is a minimal independent fixture (one repo/objective/policy/
simulated agent/task, no factory/GPU specifics) running the SAME kernel.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from director_consumer.agent_runtime import OpenCodeAdapter, SimulatedAgent
from director_consumer.authorization import (AuthRecord, AuthRequest,
                                             AuthorizationError,
                                             AuthorizationStore)
from director_consumer.context import ContextCompiler
from director_consumer.critical_path import (CriticalPath,
                                             assess as assess_cp)
from director_consumer.incidents import IncidentStore, load_correlations
from director_consumer.ownership import Claim, OwnershipReconciler
from director_consumer.projects import (ProjectRegistry, ProjectSpec,
                                        load_spec_from_config)
from director_consumer.reconciler import (DesiredState, ObservedState,
                                          Reconciler)
from director_consumer.resources import ResourceRegistry
from director_consumer.scope import ensure_scoped
from director_consumer.state import DirectorState

ROOT = Path(__file__).resolve().parents[1]
DEPS = ROOT / "director_consumer" / "deps.yaml"
DESIGNWINS = ROOT / "projects" / "designwins"


def _con(tmp_path, name="d.db"):
    con = sqlite3.connect(tmp_path / name)
    con.row_factory = sqlite3.Row
    return con


# ------------------------------------------------ §2 registry + isolation

def test_project_registration_and_lifecycle(tmp_path):
    reg = ProjectRegistry(_con(tmp_path))
    spec = ProjectSpec(project_id="alpha", name="Alpha",
                       objectives=[{"id": "OBJ-1", "text": "ship v1"}],
                       repositories=["git://alpha"], policy_version="1.0")
    reg.register(spec)
    assert reg.get("alpha").lifecycle == "active"
    reg.set_lifecycle("alpha", "paused", detail="budget hold")
    assert reg.get("alpha").lifecycle == "paused"
    reg.set_lifecycle("alpha", "archived")
    assert reg.get("alpha").lifecycle == "archived"
    with pytest.raises(ValueError):
        reg.set_lifecycle("alpha", "deleted")


def test_same_local_task_id_two_projects_one_db(tmp_path):
    from director_consumer.tasks import TaskStore
    con = _con(tmp_path)
    ta = TaskStore(con, project="alpha")
    tb = TaskStore(con, project="beta")
    for ts, owner in ((ta, "agent-a"), (tb, "agent-b")):
        ts.create("TASK-1", authority="proj-director", evidence="e")
        ts.transition("TASK-1", "offered", actor="proj-director",
                      evidence="e", owner=owner)
    assert ta.get("TASK-1").accountable_owner == "agent-a"
    assert tb.get("TASK-1").accountable_owner == "agent-b"
    assert ta.get("TASK-1").state == tb.get("TASK-1").state == "offered"
    # histories never cross
    assert all(h["actor"] == "proj-director" for h in ta.history("TASK-1"))


def test_legacy_data_migrates_non_destructively(tmp_path):
    """04-era single-key schema -> scoped schema preserves every row."""
    con = _con(tmp_path)
    con.executescript("""
CREATE TABLE tasks (task_id TEXT PRIMARY KEY, project TEXT, state TEXT,
 accountable_owner TEXT, execution_agent TEXT, session_id TEXT, host TEXT,
 assignment_authority TEXT, epoch INTEGER, created_at REAL, updated_at REAL,
 last_verified_progress TEXT, correlation_id TEXT);
INSERT INTO tasks VALUES ('T-9','alpha','active','agent-x','','','h','a',
 2,1.0,2.0,'','');""")
    con.commit()
    ensure_scoped(con)
    row = con.execute("SELECT project, state, accountable_owner FROM tasks "
                      "WHERE task_id='T-9'").fetchone()
    assert tuple(row) == ("alpha", "active", "agent-x")
    assert con.execute("SELECT COUNT(*) FROM tasks_legacy").fetchone()[0] == 1
    # restart-safe: second call is a no-op
    ensure_scoped(con)
    assert con.execute("SELECT COUNT(*) FROM tasks WHERE task_id='T-9'"
                       ).fetchone()[0] == 1


def test_messages_and_incidents_scoped(tmp_path):
    from director_consumer.messaging import (DeliveryPolicy,
                                             MailboxDelivery)
    con = _con(tmp_path)
    pol = DeliveryPolicy(enabled=False)
    ma = MailboxDelivery(con, pol, project="alpha")
    mb = MailboxDelivery(con, pol, project="beta")
    ida = ma.propose("agent-x", "question", "q", "same body")
    idb = mb.propose("agent-x", "question", "q", "same body")
    assert ida == idb  # identical content, identical local id
    assert ma.snapshot()[0]["state"] == "proposed"
    mb.record_ack(idb, from_agent="agent-x")
    assert ma.snapshot()[0]["state"] == "proposed", \
        "ack in beta must never mark alpha's message"
    deps = __import__("yaml").safe_load(DEPS.read_text())
    ia = IncidentStore(con, load_correlations(deps), project="alpha")
    ib = IncidentStore(con, load_correlations(deps), project="beta")
    from director_consumer.schema import Assessment, SourceRef
    def mk(rule):
        return Assessment(rule_id=rule, severity="P2", impacts=["x"],
                          action="propose_investigation",
                          authority="agent:x", rationale="r",
                          evidence=[SourceRef("f", "s", None, time.time())],
                          event_kind="k", event_summary="s",
                          trigger_key=f"tk-{rule}-{time.time_ns()}")
    ia.observe(mk("factory-junk-flood"))
    assert len(ia.open_incidents()) == 1
    assert ib.open_incidents() == [], "incidents never cross projects"


# ------------------------------------------------ §3 context compiler

def test_compiler_retrieves_authority_and_marks_superseded(tmp_path):
    dec = tmp_path / "DECISIONS.md"
    dec.write_text(
        "# DECISIONS\n## D1 — old rule\nthe old way\n"
        "## D2 — new rule\nsupersedes D1\nthe new way\n")
    comp = ContextCompiler(repo_root=ROOT, project_dir=DESIGNWINS,
                           decisions_path=dec, mailbox_root=None)
    pkt = comp.compile("architectural change to the old rule",
                       project_id="designwins", policy_version="1.0")
    classes = {i.evidence_class for i in pkt.items}
    assert "superseded" in classes, "superseded D1 must be marked"
    assert any(i.evidence_class == "authority" and "D2" in i.text
               for i in pkt.items)
    assert any("excluded from current authority" in u
               for u in pkt.uncertainties)


def test_compiler_never_leaks_cross_project(tmp_path):
    dec = tmp_path / "DECISIONS.md"
    dec.write_text("# DECISIONS\n## D1 — designwins only\npdf factory\n")
    comp = ContextCompiler(repo_root=ROOT, project_dir=None,
                           decisions_path=dec, mailbox_root=None)
    pkt_a = comp.compile("pdf factory", project_id="designwins")
    pkt_b = comp.compile("completely unrelated zebra migration",
                         project_id="zebra")
    assert any(i.evidence_class == "authority" for i in pkt_a.items)
    assert all("designwins" not in i.source for i in pkt_b.items), \
        "project B packets must not contain project A material"


def test_compiler_classifies_model_output_as_hypothesis(tmp_path):
    con = _con(tmp_path)
    from director_consumer.incidents import load_correlations
    import yaml as _y
    deps = _y.safe_load(DEPS.read_text())
    st = IncidentStore(con, load_correlations(deps), project="designwins")
    from director_consumer.schema import Assessment, SourceRef
    st.observe(Assessment(
        rule_id="factory-junk-flood", severity="P2", impacts=["intake"],
        action="propose_investigation", authority="agent:m5-opencode",
        rationale="suspected: factory hygiene",
        evidence=[SourceRef("factory", "s", None, time.time())],
        event_kind="junk_flood", event_summary="AppleDouble flood",
        trigger_key="cc-1",
        model_assessment={"text": "probably the rsync", "verified": False}))
    comp = ContextCompiler(repo_root=ROOT, project_dir=None,
                           decisions_path=tmp_path / "none.md")
    pkt = comp.compile("AppleDouble flood", project_id="designwins", db=con)
    hits = [i for i in pkt.items if "AppleDouble" in i.text]
    assert hits and hits[0].evidence_class == "hypothesis", \
        "unverified cause is a hypothesis, never a fact"


# ------------------------------------------------ §4 reconciler

def _desired(**kw):
    return DesiredState(
        objectives=kw.get("objectives", [{"id": "OBJ-1",
                                          "text": "keep supply flowing"}]),
        tasks=kw.get("tasks", []),
        critical_targets=kw.get("critical_targets", {}))


def _observed(**kw):
    return ObservedState(**kw)


def test_objective_without_task_found(tmp_path):
    r = Reconciler(_con(tmp_path), project="alpha")
    rep = r.reconcile(_desired(), _observed())
    assert [f.rule for f in rep["new"]] == ["objective-without-task"]
    assert rep["new"][0].verification_plan


def test_repeat_reconciliation_suppresses_with_reason(tmp_path):
    r = Reconciler(_con(tmp_path), project="alpha")
    r.reconcile(_desired(), _observed())
    rep2 = r.reconcile(_desired(), _observed())
    assert rep2["new"] == []
    assert rep2["suppressed"] and "cooldown" in rep2["suppressed"][0]["why"]
    assert rep2["total_open"] == 1, "never duplicate findings on re-poll"


def test_stalled_acknowledged_task(tmp_path):
    r = Reconciler(_con(tmp_path), project="alpha")
    d = _desired(objectives=[], tasks=[{"task_id": "T-1", "state": "active",
                         "updated_at": time.time() - 3 * 86400,
                         "last_verified_progress": ""}])
    rep = r.reconcile(d, _observed())
    assert [f.rule for f in rep["new"]] == ["stalled-acknowledged"]


def test_conflicting_claims_and_starvation_and_unverified_repair(tmp_path):
    r = Reconciler(_con(tmp_path), project="alpha")
    rep = r.reconcile(
        _desired(objectives=[], critical_targets={"coverage": "declared"},
                 tasks=[{"task_id": "T-1", "state": "active",
                         "updated_at": time.time(), "host": "m5"}]),
        _observed(
            ownership=[{"task_id": "T-1", "state": "conflicting",
                        "claimants": ["a", "b"], "evidence": ["c1", "c2"]}],
            funnel={"holdup": "admission-gap", "starvation_basis": "backlog-zero",
                    "queued": 0},
            incidents=[{"correlation_id": "factory-supply-exhaustion",
                        "lifecycle_state": "repair_in_progress"}],
            fleet={"m5": {"connectivity": "online"}},
            messages=[]))
    rules = sorted(f.rule for f in rep["new"])
    assert rules == ["conflicting-claims", "repair-unverified",
                     "starving-consumer"]


def test_owner_host_missing_is_migration_aware(tmp_path):
    r = Reconciler(_con(tmp_path), project="alpha")
    rep = r.reconcile(
        _desired(objectives=[], tasks=[{"task_id": "T-1", "state": "active",
                         "updated_at": time.time(), "host": "ryzen-2"}]),
        _observed(fleet={"ryzen-2": {"connectivity": "offline"}}))
    assert [f.rule for f in rep["new"]] == ["owner-host-missing"]


def test_reconciler_survives_restart(tmp_path):
    con = _con(tmp_path)
    r = Reconciler(con, project="alpha")
    r.reconcile(_desired(), _observed())
    con.close()
    con2 = _con(tmp_path)  # restart on same file
    r2 = Reconciler(con2, project="alpha")
    rep = r2.reconcile(_desired(), _observed())
    assert rep["new"] == [] and rep["total_open"] == 1


# ------------------------------------------------ §6 authorization

def _auth_store(tmp_path, auth_id=None, record=None):
    """File-backed approvals: the operator writes the approval file
    (owner-only writable) and only then does the store accept the record."""
    import os
    apdir = tmp_path / "approvals"
    apdir.mkdir(exist_ok=True)
    st = AuthorizationStore(_con(tmp_path), approvals_dir=apdir)
    if auth_id and record:
        f = apdir / f"{auth_id}.json"
        f.write_text(json.dumps({"auth_id": auth_id}))
        os.chmod(f, 0o640)   # operator-owned, not group/world writable
        st.issue(record)
    return st


def test_authorization_default_deny_and_scope_checks(tmp_path):
    st = _auth_store(tmp_path, "A1", AuthRecord(
        auth_id="A1", project="alpha", action="deliver",
        recipient="agent-x", task_ref="T-1",
        expires_at=time.time() + 3600, issuer="sam"))
    req = AuthRequest(project="alpha", action="deliver", recipient="agent-x",
                      task_ref="T-1")
    ok, why = st.check("A1", req)
    assert ok
    # expired (approval file present but record expired)
    import os as _os
    f2 = st.approvals_dir / "A2.json"
    f2.write_text("{}"); _os.chmod(f2, 0o640)
    st.issue(AuthRecord(auth_id="A2", project="alpha", action="deliver",
                        recipient="agent-x", task_ref="T-1",
                        expires_at=time.time() - 10, issuer="sam"))
    ok2, why2 = st.check("A2", req)
    assert not ok2 and "expired" in why2
    # project mismatch
    ok3, why3 = st.check("A1", AuthRequest(
        project="beta", action="deliver", recipient="agent-x",
        task_ref="T-1"))
    assert not ok3 and "project mismatch" in why3
    # action mismatch (fully scoped so the mismatch, not missing scope,
    # is the refusal reason)
    ok4, why4 = st.check("A1", AuthRequest(
        project="alpha", action="spawn", objective_ref="OBJ-9"))
    assert not ok4 and "action mismatch" in why4
    # recipient mismatch
    ok5, why5 = st.check("A1", AuthRequest(
        project="alpha", action="deliver", recipient="agent-y",
        task_ref="T-1"))
    assert not ok5 and "recipient mismatch" in why5
    # unknown id
    ok6, why6 = st.check("nope", req)
    assert not ok6 and "default deny" in why6


def test_replay_rejected_once_used(tmp_path):
    st = _auth_store(tmp_path, "A1", AuthRecord(
        auth_id="A1", project="alpha", action="deliver",
        recipient="agent-x", task_ref="T-1",
        expires_at=time.time() + 3600, issuer="sam"))
    req = AuthRequest(project="alpha", action="deliver", recipient="agent-x",
                      task_ref="T-1", nonce="n-1")
    ok1, _ = st.check("A1", req)
    ok2, why2 = st.check("A1", req)   # same nonce
    assert ok1 and not ok2 and "replay" in why2


def test_director_cannot_self_authorize(tmp_path):
    st = _auth_store(tmp_path)
    import os as _os
    f = st.approvals_dir / "SELF.json"
    f.write_text("{}"); _os.chmod(f, 0o640)
    with pytest.raises(AuthorizationError):
        st.issue(AuthRecord(auth_id="SELF", project="alpha",
                            action="deliver", expires_at=time.time() + 10,
                            issuer="engineering-director"))


def test_spend_scope_blocks_metered(tmp_path):
    st = _auth_store(tmp_path, "A1", AuthRecord(
        auth_id="A1", project="alpha", action="reason",
        spend_scope="none", max_spend_usd=0,
        expires_at=time.time() + 3600, issuer="sam"))
    ok, why = st.check("A1", AuthRequest(project="alpha", action="reason",
                                         spend_usd=1.0))
    assert not ok and "exceeds" in why
    ok2, why2 = st.check("A1", AuthRequest(project="alpha", action="reason",
                                           provider="openrouter"))
    assert not ok2 and "METERED REFUSED" in why2


# ------------------------------------------------ §5 agent runtime

def test_simulated_agent_lifecycle_and_disconnect_semantics():
    a = SimulatedAgent("agent-sim", "alpha", "T-1")
    a.acknowledge("ack mail ref")
    a.progress("branch pushed")
    state = a.verify_or_fail(False, "tests failed")
    assert state == "failed-verification"
    a.disconnect()
    assert a.identity.recovery_state == "disconnected", \
        "disconnect never equals completion"
    assert [e[0] for e in a.events] == ["acknowledged", "progress",
                                        "failed-verification", "disconnected"]


def test_opencode_adapter_mode_classification():
    from director_consumer.sessions import SessionRecord
    class FakeSD:
        def discover(self):
            return [SessionRecord(session_id="s1", agent="m5-opencode",
                                  parent_id="", machine="m5"),
                    SessionRecord(session_id="s2", agent="unknown",
                                  parent_id="s1", machine="m5")]
    ad = OpenCodeAdapter(FakeSD(), declared_agents={
        "m5-opencode": {"kind": "autonomous-worker"}})
    ids = {i.session_id: i for i in ad.discover()}
    assert ids["s1"].mode == "managed"      # declared worker
    assert ids["s2"].mode == "managed"      # subagent
    assert ids["s2"].agent_id == "unknown"  # title never becomes identity


# ------------------------------------------------ §7 resources

def test_placement_recommendations_only_and_migration_aware():
    class FakeFleet:
        machines = {"ryzen-2": None, "m5": None, "broken": None}
        def machine_health(self, mid):
            return {
                "ryzen-2": {"connectivity": "online", "roles": [
                    "extraction-worker"], "status": "active",
                    "verdict": "healthy", "services": {}},
                "m5": {"connectivity": "online", "roles": [
                    "control-plane-interim"], "status": "migrating",
                    "verdict": "unknown", "services": {},
                    "migration": "moving to ryzen"},
                "broken": {"connectivity": "offline", "roles": [
                    "extraction-worker"], "status": "active",
                    "verdict": "unknown", "services": {}},
            }[mid]
    reg = ResourceRegistry.from_fleet(
        FakeFleet(), project_of_role={"extraction-worker": "designwins"})
    recs = reg.recommend_placement(
        {"capability": "extraction", "project": "designwins",
         "avoid_hosts": ["m5"]})
    assert recs[0]["resource_id"] == "ryzen-2"
    assert all(r["recommendation_only"] for r in recs), \
        "placement is recommendation-only; nothing moves"


# ------------------------------------------------ §9 critical path

def test_critical_path_status_ladder():
    cp = CriticalPath(name="x", project_id="alpha", upstream_supplier="u",
                      downstream_consumer="d", consumption_rate=50,
                      available_backlog=100, coverage_target_h=24)
    assert assess_cp(cp).status == "at-risk"     # 2h coverage < 24h
    cp.available_backlog = 0
    assert assess_cp(cp).status == "starving"
    cp.available_backlog = "unknown"
    a = assess_cp(cp)
    assert a.status == "unknown" and "available_backlog" in \
        a.missing_measurements
    cp.available_backlog = 5000
    cp.consumption_rate = "unknown"
    a2 = assess_cp(cp)
    assert a2.status == "unknown" and "consumption_rate" in \
        a2.missing_measurements


# ------------------------------------------------ §10 Project B end-to-end

def test_project_b_end_to_end(tmp_path):
    """One repo, one objective, one simulated agent, one task, one policy —
    same kernel, zero DesignWins code."""
    pdir = tmp_path / "projects" / "proj-b"
    pdir.mkdir(parents=True)
    (pdir / "project.yaml").write_text(
        "project_id: proj-b\nname: Project B\n"
        "description: minimal fixture\n"
        "objectives:\n  - {id: OBJ-B-1, text: keep CI green}\n"
        "success_criteria:\n  - all tests pass\n"
        "repositories:\n  - git://proj-b\npolicy_version: '1.0'\n"
        "human_authority: sam\n")
    (pdir / "policy.yaml").write_text(
        "authority:\n  human_owner: sam\n"
        "  human_only_actions:\n    custom_b: 'b says human'\n"
        "models:\n  allowed:\n    - {name: local_b, provider: local,"
        " base_url: 'http://b:8900', model: m, role: routine}\n"
        "  rate_limits: {per_minute: 2, burst: 1}\n")
    spec = load_spec_from_config(pdir)
    assert spec.project_id == "proj-b" and spec.objectives

    con = _con(tmp_path, "kernel.db")
    reg = ProjectRegistry(con)
    reg.register(spec)
    from director_consumer.tasks import TaskStore
    from director_consumer.policy import load_project
    ts = TaskStore(con, project="proj-b")
    mp, ap = load_project(pdir)
    assert ap.human_owner == "sam" and "custom_b" in ap.human_only_actions
    ts.create("TASK-1", authority="director", evidence="objective OBJ-B-1")
    ts.transition("TASK-1", "offered", actor="director", evidence="e",
                  owner="agent-b")
    sim = SimulatedAgent("agent-b", "proj-b", "TASK-1")
    sim.acknowledge("ack")
    ts.transition("TASK-1", "acknowledged", actor="agent-b",
                  evidence="sim ack")
    # reconciler: objective now HAS a task -> no objective-without-task
    r = Reconciler(con, project="proj-b")
    rep = r.reconcile(DesiredState(
        objectives=spec.objectives,
        tasks=[{"task_id": "TASK-1", "objective_ref": "OBJ-B-1",
                "state": "acknowledged",
                "updated_at": time.time(),
                "last_verified_progress": "ack"}]),
        ObservedState())
    assert rep["new"] == [], f"no discrepancy expected, got {rep['new']}"
    # and designwins work in the same db stays invisible to proj-b
    from director_consumer.messaging import DeliveryPolicy, MailboxDelivery
    mb = MailboxDelivery(con, DeliveryPolicy(enabled=False),
                         project="designwins")
    mb.propose("m5-opencode", "question", "q", "designwins private body")
    mb_b = MailboxDelivery(con, DeliveryPolicy(enabled=False),
                           project="proj-b")
    assert mb_b.snapshot() == [], "project B never sees project A mail"


def test_project_b_required_no_engine_change():
    """The abstraction test: Project B runs through imports that contain
    no DesignWins-specific code paths."""
    import director_consumer.reconciler as rec
    import director_consumer.context as ctx
    import director_consumer.tasks as tsk
    for mod in (rec, ctx, tsk):
        src = Path(mod.__file__).read_text()
        for banned in ("canon", "mcu", "pdf factory", "designwins"):
            assert banned not in src.lower(), \
                f"{mod.__name__} leaks project concept {banned!r}"
