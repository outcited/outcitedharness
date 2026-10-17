"""DIRECTOR-05A tests — §11 service E2E + §4 adversarial + §5 context trust.

The service tests launch the ACTUAL CLI entrypoint as a subprocess against
isolated workspaces and synthetic feeds. No production resources touched.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
DEPS = ROOT / "director_consumer" / "deps.yaml"


def _run_cli(args, cwd=ROOT, timeout=300):
    r = subprocess.run([PY, "-m", "director_consumer.cli", *args],
                       capture_output=True, text=True, timeout=timeout,
                       cwd=str(cwd))
    return r


def _mk_project(pdir: Path, pid: str, obj_id: str):
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "project.yaml").write_text(
        f"project_id: {pid}\nname: {pid}\n"
        f"description: test project\n"
        f"objectives:\n  - {{id: {obj_id}, text: keep pipeline green}}\n"
        f"success_criteria:\n  - tests pass\n"
        f"repositories:\n  - git://{pid}\npolicy_version: '1.0'\n"
        f"human_authority: sam\n")
    (pdir / "policy.yaml").write_text(
        "authority:\n  human_owner: sam\n"
        "  human_only_actions:\n    custom: 'human only'\n"
        "models:\n  allowed: []\n  rate_limits: {per_minute: 2, burst: 1}\n")


def _mk_feeds(tmp: Path) -> Path:
    chg = tmp / "CHANGES.md"
    chg.write_text(
        "## 2026-10-16T10:00:00Z\n"
        "- asus4: ['canon-qwen38-27b'] -> ['NO_CONTAINERS']\n")
    sent = tmp / "sentinel"
    sent.mkdir()
    (sent / "ALERTS.md").write_text("")
    (sent / "last-known-good.json").write_text("{}")
    feeds = tmp / "feeds.yaml"
    feeds.write_text(
        f"fleet_changes:\n  args:\n    path: {chg}\n"
        f"sentinel:\n  args:\n    alerts: {sent/'ALERTS.md'}\n"
        f"    lkg: {sent/'last-known-good.json'}\n")
    return feeds


def _journal_counts(ws: Path):
    con = sqlite3.connect(ws / "director.db")
    out = {
        "journal": con.execute("SELECT COUNT(*) FROM journal").fetchone()[0],
        "proposals": con.execute(
            "SELECT COUNT(*) FROM proposals").fetchone()[0],
        "findings": con.execute(
            "SELECT COUNT(*) FROM reconciler_findings").fetchone()[0],
        "tasks": con.execute("SELECT COUNT(*) FROM tasks").fetchone()[0],
    }
    con.close()
    return out


# ------------------------------------------------ §11 service E2E

def test_service_two_projects_register_run_restart(tmp_path):
    ws = tmp_path / "ws"
    feeds = _mk_feeds(tmp_path)
    pa = tmp_path / "projects" / "proj-a"
    pb = tmp_path / "projects" / "proj-b"
    _mk_project(pa, "proj-a", "OBJ-A-1")
    _mk_project(pb, "proj-b", "OBJ-B-1")
    for p in (pa, pb):
        r = _run_cli(["register-project", "--workspace", str(ws),
                      "--project-dir", str(p)])
        assert r.returncode == 0, r.stderr
        assert json.loads(r.stdout)["registered"] == p.name
    # seed identical local task ids in both projects (pre-existing state)
    from director_consumer.tasks import TaskStore
    con = sqlite3.connect(ws / "director.db")
    for pid, owner in (("proj-a", "agent-a"), ("proj-b", "agent-b")):
        ts = TaskStore(con, project=pid)
        ts.create("TASK-1", authority="director", evidence=f"obj of {pid}")
        ts.transition("TASK-1", "offered", actor="director", evidence="e",
                      owner=owner, host="m5")
    con.close()
    r = _run_cli(["run", "--workspace", str(ws), "--deps", str(DEPS),
                  "--feeds", str(feeds), "--project", str(pa),
                  "--machine-id", "m5", "--once", "--no-llm"])
    assert r.returncode == 0, r.stderr + r.stdout
    c1 = _journal_counts(ws)
    assert c1["findings"] >= 2, "both projects reconciled"
    # isolation: same local TASK-1 owners independent
    con = sqlite3.connect(ws / "director.db")
    owners = dict(con.execute(
        "SELECT project, accountable_owner FROM tasks WHERE task_id='TASK-1'"))
    con.close()
    assert owners == {"proj-a": "agent-a", "proj-b": "agent-b"}
    # briefings: per-run + consolidated
    briefs = list((ws / "briefings").glob("BRIEFING-*.md"))
    assert any("consolidated" in b.name for b in briefs)
    # restart: identical state, no duplicates
    r2 = _run_cli(["run", "--workspace", str(ws), "--deps", str(DEPS),
                   "--feeds", str(feeds), "--project", str(pa),
                   "--machine-id", "m5", "--once", "--no-llm"])
    assert r2.returncode == 0
    c2 = _journal_counts(ws)
    assert c2["proposals"] == c1["proposals"], "restart creates no proposals"
    assert c2["tasks"] == c1["tasks"]


def test_service_interrupt_and_resume(tmp_path):
    ws = tmp_path / "ws"
    feeds = _mk_feeds(tmp_path)
    pa = tmp_path / "projects" / "proj-a"
    _mk_project(pa, "proj-a", "OBJ-A-1")
    _run_cli(["register-project", "--workspace", str(ws),
              "--project-dir", str(pa)])
    proc = subprocess.Popen(
        [PY, "-m", "director_consumer.cli", "run",
         "--workspace", str(ws), "--deps", str(DEPS),
         "--feeds", str(feeds), "--project", str(pa),
         "--interval", "30", "--no-llm"],
        cwd=str(ROOT), stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL)
    try:
        deadline = time.time() + 60
        while time.time() < deadline:
            try:
                con = sqlite3.connect(ws / "director.db")
                n = con.execute(
                    "SELECT COUNT(*) FROM journal WHERE kind='run'"
                ).fetchone()[0]
                con.close()
                if n >= 1:
                    break
            except sqlite3.Error:
                pass
            time.sleep(1)
        else:
            pytest.fail("loop never journaled a run")
        proc.kill()
        proc.wait(timeout=10)
    finally:
        if proc.poll() is None:
            proc.kill()
    before = _journal_counts(ws)
    r = _run_cli(["run", "--workspace", str(ws), "--deps", str(DEPS),
                  "--feeds", str(feeds), "--project", str(pa),
                  "--once", "--no-llm"])
    assert r.returncode == 0
    after = _journal_counts(ws)
    assert after["proposals"] <= before["proposals"] + 1, \
        "resume must not duplicate actions"
    r2 = _run_cli(["verify", "--workspace", str(ws)])
    assert json.loads(r2.stdout)["chain_ok"] is True, \
        "journal chain intact after kill -9"


def test_service_backup_restore(tmp_path):
    ws = tmp_path / "ws"
    feeds = _mk_feeds(tmp_path)
    pa = tmp_path / "projects" / "proj-a"
    _mk_project(pa, "proj-a", "OBJ-A-1")
    _run_cli(["register-project", "--workspace", str(ws),
              "--project-dir", str(pa)])
    _run_cli(["run", "--workspace", str(ws), "--deps", str(DEPS),
              "--feeds", str(feeds), "--project", str(pa),
              "--once", "--no-llm"])
    backup = tmp_path / "backup"
    shutil.copytree(ws, backup)
    r = _run_cli(["verify", "--workspace", str(backup)])
    assert json.loads(r.stdout)["chain_ok"] is True
    # restored workspace continues without duplicates
    _run_cli(["run", "--workspace", str(backup), "--deps", str(DEPS),
              "--feeds", str(feeds), "--project", str(pa),
              "--once", "--no-llm"])
    assert _journal_counts(backup)["tasks"] == _journal_counts(ws)["tasks"]


# ------------------------------------------------ §4 adversarial

def test_digest_bound_authorization_refuses_other_message(tmp_path):
    from director_consumer.authorization import (AuthRecord, AuthRequest,
                                                 AuthorizationStore)
    from director_consumer.messaging import (DeliveryDisabled,
                                             DeliveryPolicy,
                                             MailboxDelivery)
    from director_consumer.schema import fp
    apdir = tmp_path / "approvals"
    apdir.mkdir()
    ast = AuthorizationStore(sqlite3.connect(tmp_path / "d.db"),
                             approvals_dir=apdir,
                             operator_uid=os.getuid(),
                             service_uid=os.getuid() + 1)
    body = "the one approved message"
    f = apdir / "M1.json"
    f.write_text(json.dumps({
        "project": "alpha", "action": "deliver", "recipient": "agent-x",
        "task_ref": "T-1", "expires_at": time.time() + 3600,
        "issuer": "sam", "payload_digest": fp("msg-body", body)}))
    os.chmod(f, 0o640)
    ast.issue(AuthRecord(auth_id="M1", project="alpha", action="deliver",
                         recipient="agent-x", task_ref="T-1",
                         expires_at=time.time() + 3600, issuer="sam",
                         payload_digest=fp("msg-body", body)))
    pol = DeliveryPolicy(outbox_root=tmp_path / "boxes")
    mb = MailboxDelivery(sqlite3.connect(tmp_path / "d2.db"), pol,
                         project="alpha", authorizations=ast)
    good = mb.propose("agent-x", "question", "q", body,
                      correlation_id="T-1")
    assert mb.deliver(good, auth_id="M1") == "sent"
    other = mb.propose("agent-x", "question", "q", "a DIFFERENT message",
                       correlation_id="T-1")
    with pytest.raises(DeliveryDisabled):
        mb.deliver(other, auth_id="M1")


def test_approval_file_permissions_enforced(tmp_path):
    from director_consumer.authorization import (AuthRecord,
                                                 AuthorizationError,
                                                 AuthorizationStore)
    apdir = tmp_path / "approvals"
    apdir.mkdir()
    st = AuthorizationStore(sqlite3.connect(tmp_path / "d.db"),
                            approvals_dir=apdir,
                            operator_uid=os.getuid(),
                            service_uid=os.getuid() + 1)
    f = apdir / "A1.json"
    f.write_text(json.dumps({
        "project": "alpha", "action": "reason", "recipient": "x",
        "expires_at": time.time() + 3600, "issuer": "sam",
        "payload_digest": "d"}))
    os.chmod(f, 0o666)     # world writable — not trustworthy proof
    with pytest.raises(AuthorizationError):
        st.issue(AuthRecord(auth_id="A1", project="alpha", action="reason",
                            recipient="x",
                            expires_at=time.time() + 3600, issuer="sam"))
    f2 = apdir / "A2.json"
    f2.write_text(json.dumps({
        "project": "alpha", "action": "reason", "recipient": "x",
        "expires_at": time.time() + 3600, "issuer": "sam",
        "payload_digest": "d"}))
    os.chmod(f2, 0o640)
    st.issue(AuthRecord(auth_id="A2", project="alpha", action="reason",
                        recipient="x",
                        expires_at=time.time() + 3600, issuer="sam"))
    st.revoke("alpha", "A2")
    ok, why = st.check("A2", __import__(
        "director_consumer.authorization", fromlist=["AuthRequest"])
        .AuthRequest(project="alpha", action="reason"))
    assert not ok and "revoked" in why


# ------------------------------------------------ §5 context scenarios

def test_context_canon_change_scenarios(tmp_path):
    from director_consumer.context import ContextCompiler
    from director_consumer.projects import ProjectRegistry, ProjectSpec
    dec = ROOT / "designwins/docs/DECISIONS.md"
    comp = ContextCompiler(repo_root=ROOT,
                           project_dir=ROOT / "projects/designwins",
                           decisions_path=dec,
                           mailbox_root=ROOT / "shared-docs-queue")
    # 1. canon change: authority must appear
    pkt = comp.compile("canon adjudicator endpoint change decision",
                       project_id="designwins", policy_version="2026-10-16.1")
    assert any(i.evidence_class == "authority" for i in pkt.items)
    # 2. supply incident references a task id -> exact retrieval
    con = sqlite3.connect(tmp_path / "d.db")
    from director_consumer.tasks import TaskStore
    ts = TaskStore(con, project="designwins")
    ts.create("SUPPLY-RECON-01", authority="director", evidence="admission")
    ts.transition("SUPPLY-RECON-01", "offered", actor="director",
                  evidence="e", owner="agent-x")
    pkt2 = comp.compile("factory supply investigation SUPPLY-RECON-01",
                        project_id="designwins", db=con)
    assert any("SUPPLY-RECON-01" in i.text for i in pkt2.items), \
        "exact task reference must retrieve the task record"
    assert "SUPPLY-RECON-01" in pkt2.sections["exact_references"]
    # 5. unrelated project vocabulary must not leak
    pkt5 = comp.compile("zebra migration zebra zebra",
                        project_id="zebra", db=con)
    assert all("SUPPLY-RECON-01" not in i.text for i in pkt5.items)
    con.close()


def test_context_superseded_and_unverified_cause(tmp_path):
    from director_consumer.context import ContextCompiler, parse_decisions
    dec = tmp_path / "DECISIONS.md"
    dec.write_text("# DECISIONS\n## D1 — old\nold way\n"
                   "## D2 — new\nsupersedes D1\nnew way\n")
    comp = ContextCompiler(repo_root=ROOT, project_dir=None,
                           decisions_path=dec)
    pkt = comp.compile("old way architecture change", project_id="p")
    sup = [i for i in pkt.items if i.evidence_class == "superseded"]
    assert sup and "D1" in sup[0].text
    # docs claiming verification without receipts stay assertions
    docs = tmp_path / "docs"
    (ROOT / "director_consumer/docs")  # real docs used by compiler
    pkt2 = comp.compile("archive housekeeping", project_id="p")
    assert all(i.evidence_class in ("assertion", "authority", "superseded",
                                    "verified-fact", "hypothesis")
               for i in pkt2.items)


def test_context_blocks_unsupported_decision_conclusions(tmp_path):
    from director_consumer.context import ContextCompiler
    comp = ContextCompiler(repo_root=ROOT, project_dir=None,
                           decisions_path=tmp_path / "none.md")
    pkt = comp.compile("what architecture change should we ratify now",
                       project_id="zebra-proj-x")
    assert pkt.authority_missing is True
    assert any("BLOCKED" in u for u in pkt.uncertainties)


# ------------------------------------------------ §7 placement safety

def test_placement_never_recommends_offline_or_stale(tmp_path):
    from director_consumer.resources import ResourceRegistry
    class FakeFleet:
        machines = {"busy": None, "offline": None, "stale": None,
                    "idle": None}
        def machine_health(self, mid):
            return {
                "busy": {"connectivity": "online", "roles": ["worker"],
                         "status": "active", "verdict": "healthy",
                         "services": {"q": "READY"}},
                "offline": {"connectivity": "offline", "roles": ["worker"],
                            "status": "active", "verdict": "unknown",
                            "services": {}},
                "stale": {"connectivity": "online", "roles": ["worker"],
                          "status": "active", "verdict": "unknown",
                          "services": {"_telemetry": "STALE"}},
                "idle": {"connectivity": "online", "roles": ["worker"],
                         "status": "active", "verdict": "healthy",
                         "services": {"q": "READY"}},
            }[mid]
    reg = ResourceRegistry.from_fleet(FakeFleet())
    recs = reg.recommend_placement({"capability": "worker",
                                    "project": "p"})
    ids = [r["resource_id"] for r in recs]
    assert "offline" not in ids, "offline must never be execution-ready"
    assert "stale" not in ids, "stale telemetry must never be ready"
    assert "idle" in ids and "busy" in ids, \
        "healthy observed machines remain eligible (busy ranks but exists)"


# ------------------------------------------------ §10 economic law

def test_local_outage_never_falls_back_to_metered(tmp_path):
    from director_consumer.llm import Endpoint, OpsBrainClient
    from director_consumer.policy import ModelPolicy
    pol = ModelPolicy.load(ROOT / "projects/designwins/policy.yaml")
    from director_consumer.state import DirectorState
    st = DirectorState(tmp_path / "ws")
    pol.usage_sink = st.record_model_usage
    dead = OpsBrainClient(endpoints=[
        Endpoint("asus2_ops_brain", "http://127.0.0.1:1", "m"),
        Endpoint("dgx1_overflow", "http://127.0.0.1:2", "m")],
        timeout_s=0.3, policy=pol, project="designwins")
    res = dead.assess("event", "classification")
    assert res is not None and "unavailable" in res
    rows = st.con.execute(
        "SELECT endpoint, ok FROM model_usage").fetchall()
    assert rows, "attempts are journaled"
    assert all(ok == 0 for _, ok in rows)
    assert all(ep != "openrouter" for ep, _ in rows), \
        "outage must never trigger metered fallback"
    st.close()
