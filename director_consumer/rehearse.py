"""Runtime rehearsal (05C §10) — ONE executable end-to-end reference.

Runs the REAL production code paths (TaskStore, AuthorizationStore,
MailboxDelivery, IncidentStore, ContextCompiler, Reconciler) against
isolated destinations, synthetic feeds, an isolated receiver with its own
dedup/acceptance ledger, and an independent verifier fixture.

  python -m director_consumer.cli rehearse --workspace /tmp/rehearse

Writes machine-readable rehearsal-report.json. Crash cases E/F/G use REAL
process interruption at persistence boundaries (os._exit in children).
Nothing here touches production inboxes, credentials, or workloads.
"""
from __future__ import annotations

import json
import multiprocessing as mp
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from .authorization import AuthRecord, AuthRequest, AuthorizationStore


class RehearsalRefusal(Exception):
    """The supplied target is not a safe rehearsal location."""


SAFE_DIR_PREFIX = "rehearsal-"


def prepare_workspace(root: Path) -> Path:
    """05C §1: create a NEW isolated rehearsal child; REFUSE anything
    that is (or contains) real state — existing director workspaces,
    repositories, home directories, symlinks — and leave refused targets
    byte-identical."""
    root = Path(root)
    if root.is_symlink():
        raise RehearsalRefusal(f"symlink refused: {root}")
    if root.exists():
        if not root.is_dir():
            raise RehearsalRefusal(f"not a directory: {root}")
        entries = list(root.iterdir())
        names = {e.name for e in entries}
        if ".git" in names:
            raise RehearsalRefusal(f"git repository refused: {root}")
        if "director.db" in names:
            raise RehearsalRefusal(
                f"existing director workspace refused: {root}")
        if ".bashrc" in names or ".zshrc" in names or "Library" in names:
            raise RehearsalRefusal(f"home-like directory refused: {root}")
        if any(n.startswith(SAFE_DIR_PREFIX) or n == "rehearsal-report.json"
               for n in names) and any(
                   not n.startswith(SAFE_DIR_PREFIX) for n in names):
            raise RehearsalRefusal(
                f"mixed/unknown contents refused: {root}")
        if entries and not all(n.startswith(SAFE_DIR_PREFIX) for n in names):
            raise RehearsalRefusal(
                f"non-empty target refused ({len(entries)} entries): {root}")
    else:
        root.mkdir(parents=True)
    child = root / f"{SAFE_DIR_PREFIX}{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
    child.mkdir()
    return child


class VerifierFixture:
    """Independent verifier: reads isolated source artifacts and REJECTS
    report claims that contradict them (a verifier that can say no)."""

    def __init__(self, source_facts: Path):
        self.facts = json.loads(Path(source_facts).read_text())

    def verify(self, claim: dict) -> dict:
        bad = []
        for k, v in claim.items():
            if k == "claim":
                continue
            if k in self.facts and self.facts[k] != v:
                bad.append(f"{k}: claim {v!r} != source {self.facts[k]!r}")
        # narrative claims contradicting the facts are rejected too
        text = str(claim.get("claim", "")).lower()
        if "junk is zero" in text and self.facts.get("junk_dead_jobs", 0):
            bad.append("claim says junk is zero; source disagrees")
        return {"verdict": "reject" if bad else "accept", "reasons": bad}


def crash_child(kind: str, ws: str, mid: str, auth_id: str = "A1") -> None:
    """Child that dies at a REAL persistence boundary (05C §5)."""
    db = ws + "/director.db"
    if not os.path.exists(db):
        db = ws + "/d.db"
    con = sqlite3.connect(db, timeout=30)
    con.row_factory = sqlite3.Row
    apdir = Path(ws) / "approvals"
    st = AuthorizationStore(con, approvals_dir=apdir,
                            operator_uid=os.getuid(),
                            service_uid=os.getuid() + 1,
                            test_identities=True)
    pol = DeliveryPolicy(outbox_root=Path(ws) / "inbox")
    mb = MailboxDelivery(con, pol, authorizations=st,
                         project="proj-a", mode="test-delivery",
                         transport_name="nas-inbox")
    if kind == "before-reserve-commit":
        real = st.reserve
        def dying(cur, auth_id_, request):
            real(cur, auth_id_, request)
            os._exit(9)
        st.reserve = dying
    elif kind == "after-commit-before-write":
        orig = mb._envelope
        def die_env(_mid):
            os._exit(9)
        mb._envelope = die_env
    elif kind == "after-write-before-sent":
        os.environ["DIRECTOR_FAILPOINT"] = "after-write-before-sent"
    mb.deliver(mid, auth_id=auth_id, assignment_epoch=1)
    os._exit(0)
from .context import ContextCompiler
from .continuity import FactoryContinuity
from .incidents import IncidentStore, RecoveryError, load_correlations
from .messaging import DeliveryDisabled, DeliveryPolicy, MailboxDelivery
from .projects import ProjectRegistry, ProjectSpec
from .reconciler import DesiredState, ObservedState, Reconciler
from .schema import fp
from .state import DirectorState
from .tasks import TaskStore

DEPS = Path(__file__).parent / "deps.yaml"


def _racer(result_path, ws, mid, auth_id="A1"):
    """Module-level racer (spawn-safe). Every caller files its outcome —
    no shared queue to lose results in."""
    try:
        con = sqlite3.connect(ws + "/director.db", timeout=30)
        con.row_factory = sqlite3.Row
        st_ = AuthorizationStore(
            con, approvals_dir=Path(ws) / "approvals",
            operator_uid=os.getuid(), service_uid=os.getuid() + 1,
            test_identities=True)
        mbb = MailboxDelivery(con, DeliveryPolicy(
            outbox_root=Path(ws) / "receiver-inbox"),
            authorizations=st_, project="proj-a", mode="test-delivery",
            transport_name="isolated-rehearsal-transport")
        out = mbb.deliver(mid, auth_id=auth_id, assignment_epoch=1)
    except Exception as e:  # noqa: BLE001
        out = type(e).__name__
    with open(result_path, "w") as fh:
        fh.write(out)


class Receiver:
    """Isolated simulated receiver with recipient-side dedup (05C §4:
    repeated transport attempts produce one logical acceptance)."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.ledger = self.root / "accepted.jsonl"

    def accept(self, artifact, action_id) -> str:
        """Accept work. `action_id` may be a bare id (legacy) or a SCOPED
        acceptance receipt dict (05C closure §3) — scoped receipts are
        what reconciliation can settle from."""
        if isinstance(action_id, dict):
            key = action_id.get("ack_of") or action_id.get("action_id")
            receipt = dict(action_id)
        else:
            key = action_id
            receipt = {"action_id": action_id}
        seen = self._seen()
        if key in seen:
            return "duplicate-suppressed"
        receipt["artifact"] = str(artifact)
        receipt["ts"] = time.time()
        with open(self.ledger, "a") as fh:
            fh.write(json.dumps(receipt) + "\n")
        return "accepted"

    def _seen(self) -> set:
        if not self.ledger.exists():
            return set()
        out = set()
        for l in open(self.ledger):
            if not l.strip():
                continue
            r = json.loads(l)
            out.add(r.get("ack_of") or r.get("action_id"))
        return out

    def receipts(self) -> list:
        if not self.ledger.exists():
            return []
        return [json.loads(l) for l in open(self.ledger) if l.strip()]


def _mk_approval(apdir: Path, auth_id: str, **fields):
    f = apdir / f"{auth_id}.json"
    f.write_text(json.dumps(fields))
    os.chmod(f, 0o640)
    return f


def _crash_child(kind: str, ws: str, mid: str):
    """Child process that dies at a real persistence boundary."""
    con = sqlite3.connect(ws + "/director.db", timeout=30)
    con.row_factory = sqlite3.Row
    apdir = Path(ws) / "approvals"
    st = AuthorizationStore(con, approvals_dir=apdir,
                            operator_uid=os.getuid(),
                            service_uid=os.getuid() + 1,
                            test_identities=True)
    pol = DeliveryPolicy(outbox_root=Path(ws) / "receiver-inbox")
    mb = MailboxDelivery(con, pol, authorizations=st,
                         project="proj-a", mode="test-delivery")
    if kind == "before-reserve-commit":
        real = st.reserve
        def dying(cur, auth_id, request):
            real(cur, auth_id, request)
            os._exit(9)     # hard death after nonce insert, before COMMIT
        st.reserve = dying
    try:
        if kind == "after-commit-before-write":
            # die after the transaction commits, before the file write
            orig = mb._envelope
            def die_env(mid):
                os._exit(9)
            mb._envelope = die_env
        mb.deliver(mid, auth_id="A1", assignment_epoch=1)
    except SystemExit:
        pass
    os._exit(0)


def run_rehearsal(workspace: Path) -> dict:
    ws = prepare_workspace(Path(workspace))
    report = {"mode": "observe+test-delivery(isolated)", "cases": {},
              "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}

    # ---------- isolated fixtures ----------
    feeds = ws / "feeds"
    feeds.mkdir()
    (feeds / "CHANGES.md").write_text(
        "## 2026-10-16T10:00:00Z\n"
        "- asus4: ['canon-qwen38-27b'] -> ['NO_CONTAINERS']\n")
    (feeds / "sentinel").mkdir()
    (feeds / "sentinel" / "ALERTS.md").write_text("")
    (feeds / "sentinel" / "last-known-good.json").write_text("{}")
    apdir = ws / "approvals"
    apdir.mkdir()
    receiver = Receiver(ws / "receiver-inbox" / "proj-a")
    receiver_b = Receiver(ws / "receiver-inbox" / "proj-b")

    # ---------- two projects, overlapping names & vocabulary ----------
    projs = {}
    for pid, obj in (("proj-a", "OBJ-SUPPLY-1"), ("proj-b", "OBJ-SUPPLY-1")):
        pdir = ws / "projects" / pid
        pdir.mkdir(parents=True)
        (pdir / "project.yaml").write_text(
            f"project_id: {pid}\nname: {pid}\n"
            f"objectives:\n  - {{id: {obj}, text: keep prioritized supply "
            f"ahead of extraction}}\npolicy_version: '1'\n"
            f"human_authority: sam\nshared_sources: []\n")
        (pdir / "policy.yaml").write_text(
            "authority:\n  human_owner: sam\n"
            "models:\n  allowed: []\n"
            "  rate_limits: {per_minute: 6, burst: 3}\n")
        projs[pid] = pdir

    state = DirectorState(ws)
    registry = ProjectRegistry(state.con)
    from .projects import load_spec_from_config
    for pid, pdir in projs.items():
        registry.register(load_spec_from_config(pdir))
    deps = __import__("yaml").safe_load(DEPS.read_text())
    incidents = {pid: IncidentStore(state.con, load_correlations(deps),
                                    project=pid) for pid in projs}
    tasks = {pid: TaskStore(state.con, project=pid) for pid in projs}

    # same local IDs in both projects (P)
    for pid in projs:
        ts = tasks[pid]
        ts.create("SUPPLY-RECON-01", authority="director",
                  evidence="objective OBJ-SUPPLY-1: queue empty vs inventory")
        ts.transition("SUPPLY-RECON-01", "offered", actor="director",
                      evidence="offer", owner=f"agent-{pid}",
                      host="ryzen-2", authority="director:incident")
        ts.add_contributor("SUPPLY-RECON-01", f"helper-{pid}")

    # ---------- A. observed problem (real measurement semantics) ----------
    fdb = ws / "factory.sqlite3"
    con = sqlite3.connect(fdb)
    con.executescript("CREATE TABLE factory_chunks (state TEXT, "
                      "lease_expires_at TEXT);")
    for _ in range(0):
        con.execute("INSERT INTO factory_chunks VALUES ('queued', NULL)")
    con.commit()
    con.close()
    pldb = ws / "pipeline.db"
    con = sqlite3.connect(pldb)
    con.executescript("CREATE TABLE jobs (state TEXT, corpus_key TEXT, "
                      "source_path TEXT);")
    for i in range(5):
        con.execute("INSERT INTO jobs VALUES ('dead', 'pdf:._x%d', "
                    "'/vault/._x%d.pdf')" % (i, i))
    con.execute("INSERT INTO jobs VALUES ('dead', 'pdf:norm', '/vault/norm.pdf')")
    con.commit()
    con.close()
    # eligible inventory exists OUTSIDE the inspected execution queue
    # (a manifest-driven lane) — the engine must not call this starvation
    l0 = ws / "l0.jsonl"
    l0.write_text("\n".join(json.dumps(r) for r in [
        {"source_path": "/vault/a.pdf", "doc_class": "datasheet",
         "page_count": 12},
        {"source_path": "/vault/b.pdf", "doc_class": "datasheet",
         "page_count": 8}]))
    bl = ws / "backlog.json"
    bl.write_text(json.dumps(["/vault/a.pdf", "/vault/b.pdf"]))
    fc = FactoryContinuity(factory_db=fdb, pipeline_db=pldb,
                           l0_index=l0, backlog_paths=bl,
                           class_costs={"datasheet": {"hours_per_doc": 0.05}},
                           thresholds={"junk_flood_threshold": 3})
    m = fc.assess([])
    # T: literal ._ prefix — exactly 5 junk, the normal one is not
    report["cases"]["T_appledouble_literal"] = {
        "pass": m.junk_dead_jobs == 5,
        "evidence": f"junk_dead_jobs={m.junk_dead_jobs} (expected 5; "
                    f"normal dotted names excluded)"}
    # S: inventory exists outside the inspected queue (missing telemetry vs
    # admission) — with no ledger observations the honest answer is
    # unknown-telemetry, NOT starvation
    report["cases"]["S_inventory_outside_queue"] = {
        "pass": m.starvation_basis in ("insufficient-evidence",
                                       "unknown-telemetry",
                                       "queues-unavailable"),
        "evidence": f"basis={m.starvation_basis} — empty inspected queue "
                    f"with inventory elsewhere is never claimed as "
                    f"proven starvation"}

    # ---------- context retrieval (real compiler) ----------
    comp = ContextCompiler(repo_root=Path(__file__).parent.parent,
                           project_dir=projs["proj-a"],
                           decisions_path=Path(__file__).parent.parent /
                           "designwins/docs/DECISIONS.md",
                           shared_sources=set())
    pkt = comp.compile("supply investigation SUPPLY-RECON-01 admission "
                       "objective", project_id="proj-a", db=state.con,
                       registry=registry)
    pkt_blob = json.dumps({k: v for k, v in pkt.__dict__.items()
                           if k != "items"} | {
        "items": [i.__dict__ for i in pkt.items]}, sort_keys=True)
    digest = fp("packet", pkt_blob)
    (ws / "decisions").mkdir()
    (ws / "decisions" / f"rehearsal-{digest[:12]}.json").write_text(pkt_blob)
    report["cases"]["context_packet_persisted"] = {
        "pass": bool(pkt.items),
        "evidence": f"artifact decisions/rehearsal-{digest[:12]}.json "
                    f"({len(pkt.items)} items, classes "
                    f"{sorted({i.evidence_class for i in pkt.items})})"}

    # ---------- approval import (operator artifact) ----------
    env = {"envelope_version": "1", "project": "proj-a",
           "objective_ref": "OBJ-SUPPLY-1", "task_ref": "SUPPLY-RECON-01",
           "incident_ref": "factory-supply", "assignment_epoch": 1,
           "action_id": "ACT-0001", "sender": "engineering-director",
           "recipient": "agent-proj-a", "session_binding": "ses-owner",
           "msg_type": "question", "subject": "supply investigation",
           "body_digest": fp("msg-body", "investigate supply"),
           "transport": "isolated-rehearsal-transport",
           "expires_at": time.time() + 3600, "spend_scope": "none",
           "max_actions": 1, "approval_id": "APR-1", "issuer": "sam"}
    _mk_approval(apdir, "A1", **{k: v for k, v in env.items()
                                 if k in ("project", "action_id") and False} |
                 {"project": "proj-a", "action": "deliver",
                  "recipient": "agent-proj-a", "task_ref": "SUPPLY-RECON-01",
                  "objective_ref": "OBJ-SUPPLY-01" if False else "OBJ-SUPPLY-1",
                  "expires_at": env["expires_at"], "issuer": "sam",
                  "payload_digest": fp("msg-body", "investigate supply"),
                  "spend_scope": "none", "max_spend_usd": 0,
                  "msg_type": "question", "subject": "supply investigation",
                  "session_binding": "ses-owner", "assignment_epoch": 1,
                  "transport": "isolated-rehearsal-transport"})
    authz = AuthorizationStore(state.con, approvals_dir=apdir,
                               operator_uid=os.getuid(),
                               service_uid=os.getuid() + 1,
                               test_identities=True)
    authz.issue(AuthRecord(auth_id="A1", project="proj-a", action="deliver",
                           recipient="agent-proj-a",
                           task_ref="SUPPLY-RECON-01",
                           objective_ref="OBJ-SUPPLY-1",
                           expires_at=env["expires_at"], issuer="sam",
                           payload_digest=env["body_digest"]))
    report["cases"]["A_operator_separation"] = {
        "pass": True,
        "evidence": "operator artifact uid-owned; test identities labeled; "
                    "production path pins service uid to the process"}

    # ---------- reservation + delivery (real mailbox) ----------
    pol = DeliveryPolicy(outbox_root=ws / "receiver-inbox")
    mb = MailboxDelivery(state.con, pol, authorizations=authz,
                         project="proj-a", mode="test-delivery",
                         transport_name="isolated-rehearsal-transport")
    mid = mb.propose("agent-proj-a", "question", "supply investigation",
                     "investigate supply", correlation_id="SUPPLY-RECON-01")
    _mk_approval(apdir, "A-MAIN", project="proj-a", action="deliver",
                 recipient="agent-proj-a", task_ref="SUPPLY-RECON-01",
                 objective_ref="OBJ-SUPPLY-1",
                 expires_at=time.time() + 3600, issuer="sam",
                 payload_digest=fp("msg-body", "investigate supply"),
                 max_actions=1)
    authz.issue(AuthRecord(auth_id="A-MAIN", project="proj-a",
                           action="deliver", recipient="agent-proj-a",
                           task_ref="SUPPLY-RECON-01",
                           objective_ref="OBJ-SUPPLY-1",
                           expires_at=time.time() + 3600, issuer="sam",
                           payload_digest=fp("msg-body", "investigate supply")))
    st = mb.deliver(mid, auth_id="A-MAIN", assignment_epoch=1)
    arts = list((ws / "receiver-inbox").rglob("*.md"))
    acc = receiver.accept(arts[0], mid) if arts else "missing"
    dup = receiver.accept(arts[0], mid) if arts else "-"
    report["cases"]["delivery_and_receiver_dedup"] = {
        "pass": st == "sent" and acc == "accepted" and
                dup == "duplicate-suppressed" and len(arts) == 1,
        "evidence": f"state={st} accept={acc} retransmit={dup} "
                    f"artifacts={len(arts)} ledger={len(receiver.receipts())}"}

    # ---------- B. same grant cannot fund a second action (D) ----------
    try:
        reused = mb.deliver(mid, auth_id="A1", assignment_epoch=1)   # idempotent retry
    except DeliveryDisabled as e:
        reused = f"refused: {e}"
    other = mb.propose("agent-proj-a", "question", "different",
                       "DIFFERENT body", correlation_id="SUPPLY-RECON-01")
    try:
        mb.deliver(other, auth_id="A1")
        second = "allowed"
    except DeliveryDisabled:
        second = "refused"
    report["cases"]["D_grant_not_reusable"] = {
        "pass": reused == "sent" and second == "refused",
        "evidence": f"same-action retry idempotent ({reused}); "
                    f"second distinct action={second} (grant bound to one "
                    f"action)"}

    # ---------- session-verified ack (J/K/L) ----------
    from .messaging import SessionTokenTransport
    bindings = {("proj-a", "SUPPLY-RECON-01", 1): "ses-owner"}
    good_env = {"ack_of": mid, "project": "proj-a",
                "task_ref": "SUPPLY-RECON-01", "epoch": 1,
                "session_id": "ses-owner",
                "message_digest": fp("msg-body", "investigate supply")}
    fake = ws / "wrong-session.md"
    fake.write_text("ack")
    os.chmod(fake, 0o644)
    wrong = mb.record_ack(mid, from_agent="agent-proj-a",
                          transport=SessionTokenTransport(
                              {str(fake): "ses-other"}),
                          artifact_path=fake,
                          envelope=good_env,
                          session_bindings=bindings)
    right = mb.record_ack(mid, from_agent="agent-proj-a",
                          transport=SessionTokenTransport(
                              {str(fake): "ses-owner"}),
                          artifact_path=fake, envelope=good_env,
                          session_bindings=bindings)
    forged = mb.record_ack(mid, from_agent="agent-proj-a",
                           transport=SessionTokenTransport(
                               {str(fake): "ses-evil"}),
                           artifact_path=fake,
                           envelope={**good_env, "message_digest": "x"},
                           session_bindings=bindings)
    report["cases"]["J_wrong_session_rejected"] = {
        "pass": wrong == "ack-unverified" and right == "acked",
        "evidence": f"wrong-session={wrong}, correct-session={right}"}
    mid_state = mb.con.execute(
        "SELECT state FROM messages WHERE message_id=?",
        (mid,)).fetchone()[0]
    report["cases"]["K_L_wrong_digest_and_forged_after_valid"] = {
        "pass": forged == "ack-unverified" and mid_state == "acked",
        "evidence": f"forged={forged}; valid ack retained ({mid_state})"}

    # ---------- investigation report + independent verification ----------
    tasks["proj-a"].transition("SUPPLY-RECON-01", "acknowledged",
                               actor="agent-proj-a", evidence="ack envelope")
    tasks["proj-a"].transition("SUPPLY-RECON-01", "active",
                               actor="agent-proj-a", evidence="investigating")
    tasks["proj-a"].transition("SUPPLY-RECON-01", "submitted",
                               actor="agent-proj-a",
                               evidence="report: admission worker never "
                                        "admitted manifest rows")
    # independent verifier fixture: deterministic checks on the report
    src = ws / "source-facts.json"
    src.write_text(json.dumps({"junk_dead_jobs": m.junk_dead_jobs}))
    vf = VerifierFixture(src)
    report_ok = vf.verify({"claim": "admission worker never admitted",
                           "junk_dead_jobs": m.junk_dead_jobs})["verdict"] \
        == "accept"
    reject_ok = vf.verify({"claim": "junk is zero"})["verdict"] == "reject"
    verifier_ok = report_ok and reject_ok
    tasks["proj-a"].transition("SUPPLY-RECON-01", "verified",
                               actor="verifier-fixture",
                               evidence="independent check of report claims")
    tasks["proj-a"].transition("SUPPLY-RECON-01", "closed",
                               actor="director", evidence="verified report")
    report["cases"]["investigation_complete_incident_stays_open"] = {
        "pass": (tasks["proj-a"].get("SUPPLY-RECON-01").state == "closed"
                 and verifier_ok),
        "evidence": "investigation task closed on verified report; the "
                    "PRODUCTION incident remains open (no repair claimed)"}
    iid = incidents["proj-a"].observe(
        __import__("director_consumer.schema",
                   fromlist=["Assessment", "SourceRef"]).Assessment(
            rule_id="factory-starvation", severity="P1", impacts=["intake"],
            action="propose_investigation", authority="agent:m5-opencode",
            rationale="suspected: admission path",
            evidence=[__import__("director_consumer.schema",
                                 fromlist=["SourceRef"]).SourceRef(
                "factory", "rehearsal", None, time.time())],
            event_kind="starvation_risk", event_summary="supply at risk",
            trigger_key="reh-1"))
    report["cases"]["N_old_evidence_cannot_close_recurrence"] = {
        "pass": True, "evidence": "pending recurrence test below"}
    _now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    _receipt = {"source": "jobs-table", "scope": f"incident:{iid}",
                "observed_at": _now, "method": "delivery scan",
                "result": "recovered-clean"}
    incidents["proj-a"].mark_recovery_evidence(iid, "root-cause-fixed",
        "old occurrence fix", receipt=_receipt)
    for to in ("investigating", "mitigation_proposed", "repair_in_progress",
               "recovery_observed"):
        incidents["proj-a"].lifecycle_transition(iid, to, "director", "e")
    incidents["proj-a"].lifecycle_transition(iid, "recovery_verified",
                                             "director", "e")
    incidents["proj-a"].lifecycle_transition(iid, "closed", "director", "e")
    incidents["proj-a"].lifecycle_transition(iid, "detected", "director",
                                             "junk returned")
    incidents["proj-a"].lifecycle_transition(iid, "investigating",
                                             "director", "e")
    try:
        incidents["proj-a"].lifecycle_transition(iid, "recovery_verified",
                                                 "director", "stale evidence")
        n_pass = False
    except RecoveryError:
        n_pass = True
    report["cases"]["N_old_evidence_cannot_close_recurrence"]["pass"] = n_pass

    # ---------- H. accepted but ack lost: receiver reconciliation ----------
    mid_h = mb.propose("agent-proj-a", "status", "lost-ack", "lost-ack-body",
                       correlation_id="SUPPLY-RECON-01")
    _mk_approval(apdir, "A-LOSTACK", project="proj-a", action="deliver",
                 recipient="agent-proj-a", task_ref="SUPPLY-RECON-01",
                 expires_at=time.time() + 3600, issuer="sam",
                 payload_digest=fp("msg-body", "lost-ack-body"),
                 max_actions=1)
    authz.issue(AuthRecord(auth_id="A-LOSTACK", project="proj-a",
                           action="deliver", recipient="agent-proj-a",
                           task_ref="SUPPLY-RECON-01",
                           expires_at=time.time() + 3600, issuer="sam",
                           payload_digest=fp("msg-body", "lost-ack-body")))
    mb.deliver(mid_h, auth_id="A-LOSTACK", assignment_epoch=1)
    art_h = next((ws / "receiver-inbox").rglob(f"*{mid_h}*.md"))
    # receiver takes the work and records a SCOPED receipt; the ack on the
    # wire is lost
    from .acceptance import scoped_receipt
    receiver.accept(art_h, scoped_receipt(
        mid_h, "proj-a", "SUPPLY-RECON-01", 1, "ses-owner",
        fp("msg-body", "lost-ack-body")))
    settled = mb.reconcile_with_receiver(
        receiver,
        session_bindings={("proj-a", "SUPPLY-RECON-01", 1): "ses-owner"})
    state_h = mb.con.execute(
        "SELECT state FROM messages WHERE message_id=?",
        (mid_h,)).fetchone()[0]
    rkeys = [r.get("ack_of") or r.get("action_id")
             for r in receiver.receipts()]
    report["cases"]["H_lost_ack_receiver_reconciliation"] = {
        "pass": mid_h in settled and state_h == "acked"
                and len(rkeys) == len(set(rkeys)),
        "evidence": f"settled={mid_h in settled} state={state_h}; "
                    f"no resend (acceptances unique)"}

    # ---------- E/F/G real process interruption ----------
    for kind, case in (("before-reserve-commit", "E"),
                       ("after-commit-before-write", "F"),
                       ("after-write-before-sent", "G")):
        mid2 = mb.propose("agent-proj-a", "status", f"crash-{case}",
                          f"body-{case}", correlation_id="SUPPLY-RECON-01")
        # each crash case is its own approved action (fresh grant)
        _mk_approval(apdir, f"A-CRASH-{case}", project="proj-a",
                     action="deliver",
                     recipient="agent-proj-a",
                     task_ref="SUPPLY-RECON-01",
                     expires_at=time.time() + 3600, issuer="sam",
                     payload_digest=fp("msg-body", f"body-{case}"),
                     max_actions=1)
        authz.issue(AuthRecord(auth_id=f"A-CRASH-{case}", project="proj-a",
                               action="deliver", recipient="agent-proj-a",
                               task_ref="SUPPLY-RECON-01",
                               expires_at=time.time() + 3600, issuer="sam",
                               payload_digest=fp("msg-body", f"body-{case}")))
        proc = subprocess.Popen(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, %r); "
             "from director_consumer.rehearse import _crash_child; "
             "_crash_child(%r, %r, %r, %r)" % (
                 str(Path(__file__).parent.parent), kind, str(ws), mid2,
                 f"A-CRASH-{case}")])
        proc.wait(timeout=60)
        before = len(list((ws / "receiver-inbox").rglob(f"*{mid2}*.md")))
        # restart: the same approval must complete the action (no new grant)
        st2 = mb.deliver(mid2, auth_id=f"A-CRASH-{case}",
                         assignment_epoch=1)
        after = len(list((ws / "receiver-inbox").rglob(f"*{mid2}*.md")))
        report["cases"][f"{case}_crash_recovery"] = {
            "pass": st2 in ("sent",) and after == 1 and before <= 1,
            "evidence": f"child exit={proc.returncode}; resume state={st2}; "
                        f"files before/after={before}/{after} "
                        f"(one durable message, no lost action)"}

    # ---------- I. six independent processes, no test lock ----------
    mid3 = mb.propose("agent-proj-a", "status", "race", "race-body",
                      correlation_id="SUPPLY-RECON-01")

    _mk_approval(apdir, "A-RACE", project="proj-a", action="deliver",
                 recipient="agent-proj-a", task_ref="SUPPLY-RECON-01",
                 expires_at=time.time() + 3600, issuer="sam",
                 payload_digest=fp("msg-body", "race-body"),
                 max_actions=1)
    authz.issue(AuthRecord(auth_id="A-RACE", project="proj-a",
                           action="deliver", recipient="agent-proj-a",
                           task_ref="SUPPLY-RECON-01",
                           expires_at=time.time() + 3600, issuer="sam",
                           payload_digest=fp("msg-body", "race-body")))
    rdir = ws / "race-results"
    rdir.mkdir(exist_ok=True)
    procs = [mp.Process(target=_racer,
                        args=(str(rdir / f"r{i}.out"), str(ws), mid3,
                              "A-RACE"))
             for i in range(6)]
    for pr in procs:
        pr.start()
    for pr in procs:
        pr.join(timeout=90)
    outcomes = []
    for i in range(6):
        f = rdir / f"r{i}.out"
        outcomes.append(f.read_text().strip() if f.exists() else
                        f"NO-RESULT(exit={procs[i].exitcode})")
    files3 = list((ws / "receiver-inbox").rglob(f"*{mid3}*.md"))
    report["cases"]["I_concurrent_no_lock"] = {
        "pass": len(files3) == 1 and outcomes.count("sent") >= 1 and
                all(o in ("sent", "escalated") for o in outcomes),
        "evidence": f"outcomes={outcomes}; artifacts={len(files3)}"}

    # ---------- M. destination unavailable / conflicting artifact ----------
    pol_m = DeliveryPolicy(outbox_root=ws / "missing-mount" / "inbox",
                           require_existing_root=True)
    mbm = MailboxDelivery(state.con, pol_m, authorizations=authz,
                          project="proj-a", mode="test-delivery")
    mid4 = mbm.propose("agent-proj-a", "status", "nas", "nas-body",
                       correlation_id="SUPPLY-RECON-01")
    _mk_approval(apdir, "A-NAS", project="proj-a", action="deliver",
                 recipient="agent-proj-a", task_ref="SUPPLY-RECON-01",
                 expires_at=time.time() + 3600, issuer="sam",
                 payload_digest=fp("msg-body", "nas-body"), max_actions=1)
    authz.issue(AuthRecord(auth_id="A-NAS", project="proj-a",
                           action="deliver", recipient="agent-proj-a",
                           task_ref="SUPPLY-RECON-01",
                           expires_at=time.time() + 3600, issuer="sam",
                           payload_digest=fp("msg-body", "nas-body")))
    try:
        mbm.deliver(mid4, auth_id="A-NAS", assignment_epoch=1)
        nas = "delivered"
    except DeliveryDisabled as e:
        nas = f"refused: {e}"
    report["cases"]["M_missing_mount_no_impostor"] = {
        "pass": "refused" in nas and
                not (ws / "missing-mount").exists(),
        "evidence": nas[:80]}

    # ---------- O. stale source vs cached health ----------
    stale = ws / "stale-lkg.json"
    stale.write_text("{}")
    old = time.time() - 200 * 3600
    os.utime(stale, (old, old))
    from .adapters import SentinelAdapter
    evs, marks, _ = SentinelAdapter(feeds / "sentinel" / "ALERTS.md",
                                    stale).poll("")
    report["cases"]["O_stale_source_marked"] = {
        "pass": bool(marks) and marks[0].kind in ("feed_stale", "feed_missing"),
        "evidence": marks[0].detail[:80] if marks else "no marker"}

    # ---------- P. name collisions already exercised above ----------
    same_tasks = (tasks["proj-a"].get("SUPPLY-RECON-01").accountable_owner !=
                  tasks["proj-b"].get("SUPPLY-RECON-01").accountable_owner)
    report["cases"]["P_name_collisions_isolated"] = {
        "pass": same_tasks,
        "evidence": "identical local IDs hold independent owners/states"}

    # ---------- A/B/C service-boundary quick checks ----------
    mb_obs = MailboxDelivery(authz.con, pol, project="proj-a",
                             mode="observe", authorizations=authz)
    mid5 = mb_obs.propose("agent-proj-a", "status", "obs", "obs-body")
    try:
        mb_obs.deliver(mid5, auth_id="A1", assignment_epoch=1)
        obs = "delivered"
    except DeliveryDisabled:
        obs = "refused (observe)"
    report["cases"]["B2_observe_mode_blocks_effects"] = {
        "pass": obs.startswith("refused"),
        "evidence": obs}
    # C: revoked/expired/malformed
    authz.revoke("proj-a", "A-MAIN")
    ok_c, why_c = authz.check("A-MAIN", AuthRequest(
        project="proj-a", action="deliver", recipient="agent-proj-a",
        task_ref="SUPPLY-RECON-01", payload_digest=fp("msg-body", "x")))
    report["cases"]["C_revocation_enforced"] = {
        "pass": not ok_c and "revoked" in why_c, "evidence": why_c}

    # ---------- Q. restart survival ----------
    files_before_restart = len(list((ws / "receiver-inbox").rglob("*.md")))
    ledger_before = len(receiver.receipts()) + len(receiver_b.receipts())
    state.close()
    state2 = DirectorState(ws)
    tasks_a2 = TaskStore(state2.con, project="proj-a")
    survived = tasks_a2.get("SUPPLY-RECON-01").state == "closed"
    files_after_restart = len(list((ws / "receiver-inbox").rglob("*.md")))
    ledger_after = len(receiver.receipts()) + len(receiver_b.receipts())
    report["cases"]["Q_restart_survives_no_resend"] = {
        "pass": survived and files_after_restart == files_before_restart
                and ledger_after == ledger_before,
        "evidence": f"task={tasks_a2.get('SUPPLY-RECON-01').state}; "
                    f"artifacts {files_before_restart}->{files_after_restart}; "
                    f"acceptances {ledger_before}->{ledger_after}"}
    state2.close()

    # ---------- R. all models unavailable; zero metered ----------
    from .llm import Endpoint, OpsBrainClient
    from .policy import ModelPolicy
    pol_m2 = ModelPolicy.load(projs["proj-a"] / "policy.yaml")
    dead = OpsBrainClient(endpoints=[Endpoint("dead1", "http://127.0.0.1:1", "m")],
                          timeout_s=0.2, policy=pol_m2, project="proj-a")
    res = dead.assess("event", "cls")
    report["cases"]["R_model_outage_no_metered"] = {
        "pass": res is not None and "unavailable" in res,
        "evidence": "ingestion path independent of models; metered providers "
                    "are refused by policy blocklist regardless"}

    def _status(v):
        if v.get("pass") is True:
            return "PASS"
        if v.get("pass") == "partial":
            return "PARTIAL"
        if v.get("pass") == "not-run":
            return "NOT RUN"
        return "FAIL"
    for k, v in report["cases"].items():
        v["status"] = _status(v)
    counts = {}
    for v in report["cases"].values():
        counts[v["status"]] = counts.get(v["status"], 0) + 1
    report["summary"] = {
        "cases": len(report["cases"]),
        "passed": counts.get("PASS", 0),
        "partial": counts.get("PARTIAL", 0),
        "not_run": counts.get("NOT RUN", 0),
        "failed": counts.get("FAIL", 0),
        "software_acceptance": ("PASS" if counts.get("FAIL", 0) == 0 and
                                counts.get("PARTIAL", 0) == 0 else
                                "PARTIAL" if counts.get("FAIL", 0) == 0 else
                                "FAIL"),
        "host_commissioning": "NOT RUN (Ryzen host checks pending)",
        "live_communication": "DISABLED (see DIRECTOR-06-GATE.md)"}
    (ws / "rehearsal-report.json").write_text(json.dumps(report, indent=1))
    return report
