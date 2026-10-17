"""DIRECTOR-05B tests — authority closure, delivery recovery, multi-project.

Covers the 12 mandated acceptance scenarios; overlaps with 04/05/05a are
marked. All mailboxes/approvals are isolated; no production contact.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from director_consumer.authorization import (AuthRecord, AuthRequest,
                                             AuthorizationError,
                                             AuthorizationStore)
from director_consumer.messaging import (DeliveryDisabled, DeliveryPolicy,
                                         MailboxDelivery)

ROOT = Path(__file__).resolve().parents[1]


def _db(tmp, name="d.db"):
    con = sqlite3.connect(tmp / name, timeout=30)
    con.row_factory = sqlite3.Row
    return con


def _approval(apdir: Path, auth_id: str, **fields):
    f = apdir / f"{auth_id}.json"
    f.write_text(json.dumps(fields))
    os.chmod(f, 0o640)
    return f


def _store(con, tmp, uid_shift=1):
    apdir = tmp / "approvals"
    apdir.mkdir(exist_ok=True)
    return AuthorizationStore(con, approvals_dir=apdir,
                              operator_uid=os.getuid(),
                              service_uid=os.getuid() + uid_shift), apdir


# 1. legacy delivery-bypass attempts fail (P0-1)

def test_legacy_bypass_refused_without_store(tmp_path):
    pol = DeliveryPolicy(enabled=True, authorization_ref="just-a-string",
                         outbox_root=tmp_path / "boxes")
    mb = MailboxDelivery(_db(tmp_path), pol)
    mid = mb.propose("agent-x", "question", "q", "body",
                     correlation_id="T-1")
    with pytest.raises(DeliveryDisabled) as e:
        mb.deliver(mid)
    assert "no authorization service" in str(e.value), \
        "flag+string can never authorize delivery"


def test_legacy_bypass_refused_even_with_flag(tmp_path):
    """The service boundary: even a fully enabled DeliveryPolicy cannot
    deliver without the AuthorizationStore (P0-1)."""
    pol = DeliveryPolicy(enabled=True, authorization_ref="SAM-SAID-SO",
                         outbox_root=tmp_path / "boxes")
    mb = MailboxDelivery(_db(tmp_path), pol)
    mid = mb.propose("agent-x", "status", "s", "body")
    assert mb.deliver.__doc__  # sanity
    with pytest.raises(DeliveryDisabled):
        mb.deliver(mid, auth_id="ANY")


# 2. director cannot forge operator approval (P0-2)

def test_director_identity_cannot_authorize(tmp_path):
    # operator uid == service uid: no trust boundary -> refuse everything
    con = _db(tmp_path)
    apdir = tmp_path / "approvals"
    apdir.mkdir()
    st = AuthorizationStore(con, approvals_dir=apdir,
                            operator_uid=os.getuid(),
                            service_uid=os.getuid())
    _approval(apdir, "A1", project="p", action="reason", recipient="x",
              expires_at=time.time() + 3600, issuer="sam",
              payload_digest="d")
    with pytest.raises(AuthorizationError):
        st.issue(AuthRecord(auth_id="A1", project="p", action="reason",
                            recipient="x", expires_at=time.time() + 3600,
                            issuer="sam"))


def test_empty_artifact_is_not_approval(tmp_path):
    con = _db(tmp_path)
    st, apdir = _store(con, tmp_path)
    _approval(apdir, "EMPTY")   # operator file exists but is empty
    with pytest.raises(AuthorizationError):
        st.issue(AuthRecord(auth_id="EMPTY", project="p", action="reason",
                            recipient="x", expires_at=time.time() + 3600,
                            issuer="sam"))


def test_symlink_and_traversal_refused(tmp_path):
    con = _db(tmp_path)
    st, apdir = _store(con, tmp_path)
    real = tmp_path / "outside.json"
    real.write_text(json.dumps({"project": "p", "action": "reason",
                                "recipient": "x",
                                "expires_at": time.time() + 3600,
                                "issuer": "sam", "payload_digest": "d"}))
    os.symlink(real, apdir / "LINK.json")
    ok, why = st._read_artifact("LINK")
    assert ok is None and ("symlink" in why or "missing" in why
                           or "escapes" in why)
    ok2, why2 = st._read_artifact("../outside")
    assert ok2 is None and "traversal" in why2


# 3/4. scope mismatch + revocation (also in 05)

def test_scope_mismatch_and_revocation(tmp_path):
    con = _db(tmp_path)
    st, apdir = _store(con, tmp_path)
    _approval(apdir, "A1", project="alpha", action="deliver",
              recipient="agent-x", task_ref="T-1",
              expires_at=time.time() + 3600, issuer="sam",
              payload_digest="dg")
    st.issue(AuthRecord(auth_id="A1", project="alpha", action="deliver",
                        recipient="agent-x", task_ref="T-1",
                        expires_at=time.time() + 3600, issuer="sam"))
    ok, why = st.check("A1", AuthRequest(project="beta", action="deliver",
                                         recipient="agent-x", task_ref="T-1",
                                         payload_digest="dg"))
    assert not ok and "mismatch" in why
    st.revoke("alpha", "A1")
    ok2, why2 = st.check("A1", AuthRequest(project="alpha", action="deliver",
                                           recipient="agent-x",
                                           task_ref="T-1",
                                           payload_digest="dg"))
    assert not ok2 and "revoked" in why2


# 5. crash at every delivery boundary — no loss, no duplicates

def _deliver_setup(tmp_path, body="body"):
    con = _db(tmp_path)
    st, apdir = _store(con, tmp_path)
    _approval(apdir, "A1", project="default", action="deliver",
              recipient="agent-x", task_ref="T-1",
              expires_at=time.time() + 3600, issuer="sam",
              payload_digest=__import__(
                  "director_consumer.schema", fromlist=["fp"]
              ).fp("msg-body", body))
    st.issue(AuthRecord(auth_id="A1", project="default", action="deliver",
                        recipient="agent-x", task_ref="T-1",
                        expires_at=time.time() + 3600, issuer="sam"))
    pol = DeliveryPolicy(outbox_root=tmp_path / "boxes")
    mb = MailboxDelivery(con, pol, authorizations=st)
    mid = mb.propose("agent-x", "question", "q", body, correlation_id="T-1")
    return mb, mid


def test_crash_boundary_after_nonce_before_intent(tmp_path):
    # nonce consumed (authorization recorded) but intent never written:
    # a NEW attempt re-enters check() -> nonce replay -> must NOT double
    # deliver; the recoverable path is reconcile from intent-less state
    mb, mid = _deliver_setup(tmp_path)
    from director_consumer.schema import fp
    mb.authorizations.check("A1", AuthRequest(
        project="default", action="deliver", recipient="agent-x",
        task_ref="T-1", nonce=f"deliver:{mid}",
        payload_digest=fp("msg-body", "body")))
    # crashed here: no intent, no file
    with pytest.raises(DeliveryDisabled):
        mb.deliver(mid, auth_id="A1")   # nonce already burned -> refused
    # audit shows the refusal; nothing was written
    assert not list((tmp_path / "boxes").rglob("*.md"))


def test_crash_boundary_after_intent_before_write(tmp_path):
    mb, mid = _deliver_setup(tmp_path)
    mb.deliver(mid, auth_id="A1")
    # simulate crash: state back to 'delivering', file removed
    mb.con.execute("UPDATE messages SET state='delivering' "
                   "WHERE message_id=?", (mid,))
    mb.con.commit()
    for f in (tmp_path / "boxes").rglob("*.md"):
        f.unlink()
    state = mb.deliver(mid, auth_id="A1")   # authorized-retry path
    assert state == "sent"
    assert len(list((tmp_path / "boxes").rglob("*.md"))) == 1


def test_crash_boundary_after_write_before_sent(tmp_path):
    mb, mid = _deliver_setup(tmp_path)
    mb.deliver(mid, auth_id="A1")
    mb.con.execute("UPDATE messages SET state='delivering' "
                   "WHERE message_id=?", (mid,))
    mb.con.commit()
    # file already exists; retry must land on the SAME stable filename
    files_before = sorted(f.name for f in (tmp_path / "boxes").rglob("*.md"))
    mb.deliver(mid, auth_id="A1")
    files_after = sorted(f.name for f in (tmp_path / "boxes").rglob("*.md"))
    assert files_before == files_after, "retry never creates a 2nd message"
    assert mb.snapshot()[0]["state"] == "sent"


# 6. concurrent delivery -> one durable message

def test_concurrent_delivery_single_message(tmp_path):
    """Multi-process-style concurrency: independent connections racing on
    the same message must produce exactly ONE durable message."""
    mb, mid = _deliver_setup(tmp_path)
    del mb
    results = []
    lock = threading.Lock()
    def attempt():
        try:
            con = sqlite3.connect(tmp_path / "d.db", timeout=30)
            con.row_factory = sqlite3.Row
            st, apdir = _store(con, tmp_path)
            pol = DeliveryPolicy(outbox_root=tmp_path / "boxes")
            local = MailboxDelivery(con, pol, authorizations=st)
            with lock:
                results.append(local.deliver(mid, auth_id="A1"))
        except Exception as e:  # noqa: BLE001
            with lock:
                results.append(type(e).__name__)
    threads = [threading.Thread(target=attempt) for _ in range(6)]
    for t in threads: t.start()
    for t in threads: t.join(timeout=30)
    files = list((tmp_path / "boxes").rglob("*.md"))
    assert len(files) == 1, f"concurrent delivers wrote {len(files)} files"
    assert "sent" in results, f"at least one attempt must succeed: {results}"


# 7/8. ack origin (P0-4)

class UidTransport:
    def __init__(self, uids):
        self.uids = uids
    def verify(self, from_agent, artifact_path):
        try:
            return os.stat(artifact_path).st_uid == self.uids.get(from_agent)
        except OSError:
            return False


def test_forged_ack_stays_unverified_and_real_ack_passes(tmp_path):
    mb, mid = _deliver_setup(tmp_path)
    mb.deliver(mid, auth_id="A1")
    # forged: From: header claims agent-x but transport uid differs
    forged = tmp_path / "forged.md"
    forged.write_text("ack_of: x\nFrom: agent-x\n")
    os.chmod(forged, 0o644)
    transport = UidTransport({"agent-x": os.getuid() + 7})
    state = mb.record_ack(mid, from_agent="agent-x",
                          transport=transport, artifact_path=forged)
    assert state == "ack-unverified", "forged origin is never acceptance"
    # verified: artifact owned by the agent's registered uid
    real = tmp_path / "real.md"
    real.write_text("ack_of: x\nFrom: agent-x\n")
    os.chmod(real, 0o644)
    transport_ok = UidTransport({"agent-x": os.getuid()})
    state2 = mb.record_ack(mid, from_agent="agent-x",
                           transport=transport_ok, artifact_path=real)
    assert state2 == "acked"


# 9. migrated rows keep project identity

def test_migration_preserves_project_identity(tmp_path):
    con = _db(tmp_path)
    con.executescript("""
CREATE TABLE proposals (trigger_key TEXT PRIMARY KEY, ptype TEXT,
 filename TEXT, authority TEXT, created_at REAL, body TEXT,
 written INTEGER DEFAULT 0, written_at REAL);
INSERT INTO proposals VALUES ('k1','message','f.md','sam',1.0,'b',0,NULL);""")
    # legacy proposals carry no project column; the migration must not
    # destroy the row and must record an audit entry (never silent)
    from director_consumer.scope import ensure_scoped
    ensure_scoped(con)
    row = con.execute("SELECT trigger_key, project, ptype FROM proposals "
                      "WHERE trigger_key='k1'").fetchone()
    assert row is not None, "historical row preserved"
    assert row[1] == "default", "unattributable history lands in default"
    log = con.execute("SELECT COUNT(*) FROM migration_log "
                      "WHERE tbl='proposals'").fetchone()[0]
    assert log == 1, "migrations are audited, never silent"
    # rows WITH project identity survive verbatim
    con.executescript("""
CREATE TABLE tasks (task_id TEXT PRIMARY KEY, project TEXT, state TEXT,
 accountable_owner TEXT, execution_agent TEXT, session_id TEXT, host TEXT,
 assignment_authority TEXT, epoch INTEGER, created_at REAL, updated_at REAL,
 last_verified_progress TEXT, correlation_id TEXT);
INSERT INTO tasks VALUES ('T-1','alpha','active','owner','','','h','a',
 1,1.0,2.0,'','');""")
    ensure_scoped(con)
    t = con.execute("SELECT project, accountable_owner FROM tasks "
                    "WHERE task_id='T-1'").fetchone()
    assert tuple(t) == ("alpha", "owner"), "project identity preserved"


# 10. multi-project closure (strengthened 05/05a)

def test_projects_share_nothing_private(tmp_path):
    from director_consumer.incidents import IncidentStore, load_correlations
    from director_consumer.tasks import TaskStore
    import yaml
    con = _db(tmp_path)
    deps = yaml.safe_load(open(ROOT / "director_consumer/deps.yaml"))
    ia = IncidentStore(con, load_correlations(deps), project="alpha")
    ib = IncidentStore(con, load_correlations(deps), project="beta")
    from director_consumer.schema import Assessment, SourceRef
    def mk(rule, tk):
        return Assessment(rule_id=rule, severity="P1", impacts=["x"],
                          action="propose_investigation",
                          authority="agent:x", rationale="r",
                          evidence=[SourceRef("f", "s", None, time.time())],
                          event_kind="k", event_summary="s", trigger_key=tk)
    iid = ia.observe(mk("factory-starvation", "t1"))
    # same rule+subject in beta gets its own incident
    iid2 = ib.observe(mk("factory-starvation", "t1"))
    assert iid != iid2
    assert ib.open_incidents() != [] and ia.open_incidents() != []
    # beta cannot see alpha's recovery evidence
    ia.mark_recovery_evidence(iid, "root-cause-fixed", "alpha fixed")
    assert ib.lifecycle(iid2)["recovery_evidence"] == []


# 11/12. restart + LLM loss: covered by 05a service tests; quick re-proofs

def test_restart_no_duplicate_actions(tmp_path):
    mb, mid = _deliver_setup(tmp_path)
    mb.deliver(mid, auth_id="A1")
    con2 = _db(tmp_path)   # reopen same db
    st2, _ = _store(con2, tmp_path)
    pol = DeliveryPolicy(outbox_root=tmp_path / "boxes")
    mb2 = MailboxDelivery(con2, pol, authorizations=st2)
    assert mb2.deliver(mid, auth_id="A1") == "sent", "terminal state held"
    assert len(list((tmp_path / "boxes").rglob("*.md"))) == 1
