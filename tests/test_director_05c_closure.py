"""05C closure — cross-path contract regressions (negative first).

Three invariants:
  A. only the exact approved action executes
  B. only the authenticated agent/session accepts its assignment
  C. only successful, source-supported verification closes work

Every test here is written against the reviewed implementation d3310620
and is expected to FAIL there for its stated reason before the fix.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from director_consumer.authorization import (AuthRecord, AuthRequest,
                                             AuthorizationStore)
from director_consumer.incidents import IncidentStore, RecoveryError, load_correlations
from director_consumer.messaging import (DeliveryDisabled, DeliveryPolicy,
                                         MailboxDelivery, SessionTokenTransport)
from director_consumer.schema import fp

ROOT = Path(__file__).resolve().parents[1]
DEPS = ROOT / "director_consumer" / "deps.yaml"
NOW = lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _db(tmp, n="d.db"):
    con = sqlite3.connect(tmp / n, timeout=30)
    con.row_factory = sqlite3.Row
    return con


def _approval(apdir, auth_id, **fields):
    f = apdir / f"{auth_id}.json"
    f.write_text(json.dumps(fields))
    os.chmod(f, 0o640)


def _full_setup(tmp, body="investigate supply", subject="q",
                msg_type="question", task="T-1", epoch=1,
                transport="nas-inbox", recipient="agent-x"):
    con = _db(tmp)
    apdir = tmp / "approvals"
    apdir.mkdir(exist_ok=True)
    st = AuthorizationStore(con, approvals_dir=apdir,
                            operator_uid=os.getuid(),
                            service_uid=os.getuid() + 1,
                            test_identities=True)
    digest = fp("msg-body", body)
    _approval(apdir, "A1", project="proj-a", action="deliver",
              recipient=recipient, task_ref=task, objective_ref="OBJ-1",
              expires_at=time.time() + 3600, issuer="sam",
              payload_digest=digest, msg_type=msg_type, subject=subject,
              transport=transport, assignment_epoch=epoch,
              session_binding="ses-owner", max_actions=1)
    st.issue(AuthRecord(auth_id="A1", project="proj-a", action="deliver",
                        recipient=recipient, task_ref=task,
                        objective_ref="OBJ-1",
                        expires_at=time.time() + 3600, issuer="sam",
                        payload_digest=digest))
    pol = DeliveryPolicy(outbox_root=tmp / "inbox", require_existing_root=False)
    mb = MailboxDelivery(con, pol, authorizations=st, project="proj-a",
                         mode="test-delivery", transport_name=transport)
    return mb, st, digest


# ============================ §5 approved-action boundary ====================

def test_one_grant_cannot_fund_second_explicit_action_id(tmp_path):
    mb, st, digest = _full_setup(tmp_path)
    m1 = mb.propose("agent-x", "question", "q", "investigate supply",
                    correlation_id="T-1")
    assert mb.deliver(m1, auth_id="A1", assignment_epoch=1) == "sent"
    # identical content, explicitly supplied SECOND message id
    m2 = mb.propose("agent-x", "question", "q", "investigate supply",
                    correlation_id="T-1", message_id="EXPLICIT-ID-2")
    with pytest.raises(DeliveryDisabled):
        mb.deliver(m2, auth_id="A1", assignment_epoch=1), \
            "a single-action grant must never fund a second action id"
    assert len(list((tmp_path / "inbox").rglob("*.md"))) == 1


def test_omitted_action_id_never_makes_a_reusable_grant(tmp_path):
    """The artifact may omit action_id only if reservation binds the
    grant atomically to the one action that consumes it."""
    mb, st, digest = _full_setup(tmp_path)
    m1 = mb.propose("agent-x", "question", "q", "investigate supply",
                    correlation_id="T-1")
    assert mb.deliver(m1, auth_id="A1", assignment_epoch=1) == "sent"
    m2 = mb.propose("agent-x", "question", "q", "investigate supply",
                    correlation_id="T-1", message_id="ANOTHER-ID")
    with pytest.raises(DeliveryDisabled):
        mb.deliver(m2, auth_id="A1", assignment_epoch=1)


def test_modified_message_cannot_change_reserved_output(tmp_path):
    mb, st, digest = _full_setup(tmp_path)
    m1 = mb.propose("agent-x", "question", "q", "investigate supply",
                    correlation_id="T-1")
    mb.deliver(m1, auth_id="A1", assignment_epoch=1)
    art = next((tmp_path / "inbox").rglob("*.md"))
    sent = art.read_text()
    # someone edits the message row AFTER reservation
    mb.con.execute("UPDATE messages SET body='TAMPERED CONTENT' "
                   "WHERE message_id=?", (m1,))
    mb.con.commit()
    mb.con.execute("UPDATE messages SET state='delivering' "
                   "WHERE message_id=?", (m1,))
    mb.con.commit()
    mb.deliver(m1, auth_id="A1", assignment_epoch=1)   # resume
    assert art.read_text() == sent, \
        "the persisted approved action, not the mutable row, is what ships"


def test_changed_destination_config_cannot_redirect_reserved_work(tmp_path):
    mb, st, digest = _full_setup(tmp_path)
    m1 = mb.propose("agent-x", "question", "q", "investigate supply",
                    correlation_id="T-1")
    mb.deliver(m1, auth_id="A1", assignment_epoch=1)
    art = next((tmp_path / "inbox").rglob("*.md"))
    assert str(art).startswith(str(tmp_path / "inbox"))
    # destination config changes AFTER reservation
    mb.policy.outbox_root = tmp_path / "SOMEWHERE-ELSE"
    mb.con.execute("UPDATE messages SET state='delivering' "
                   "WHERE message_id=?", (m1,))
    mb.con.commit()
    mb.deliver(m1, auth_id="A1", assignment_epoch=1)
    assert not (tmp_path / "SOMEWHERE-ELSE").exists(), \
        "reserved work must follow the persisted destination"
    assert art.exists()


def test_revoked_expired_unsent_actions_stop_visibly(tmp_path):
    mb, st, digest = _full_setup(tmp_path)
    m1 = mb.propose("agent-x", "question", "q", "investigate supply",
                    correlation_id="T-1")
    # revoke BEFORE any effect
    st.revoke("proj-a", "A1")
    with pytest.raises(DeliveryDisabled):
        mb.deliver(m1, auth_id="A1", assignment_epoch=1)
    state = mb.con.execute("SELECT state FROM messages WHERE message_id=?",
                           (m1,)).fetchone()[0]
    assert state in ("proposed", "revoked-before-send"), \
        "revocation stops the action visibly, never silently"


def test_resume_after_crash_never_consumes_another_approval(tmp_path):
    mb, st, digest = _full_setup(tmp_path)
    m1 = mb.propose("agent-x", "question", "q", "investigate supply",
                    correlation_id="T-1")
    mb.deliver(m1, auth_id="A1", assignment_epoch=1)
    # crash after commit/before write: state back to delivering, no file
    mb.con.execute("UPDATE messages SET state='delivering' "
                   "WHERE message_id=?", (m1,))
    mb.con.commit()
    for f in (tmp_path / "inbox").rglob("*.md"):
        f.unlink()
    # the SAME grant resumes — nonce/grant binding already consumed by this
    # action; no second authorization is requested or granted
    assert mb.deliver(m1, auth_id="A1", assignment_epoch=1) == "sent"
    n = st.con.execute("SELECT COUNT(*) FROM auth_nonces").fetchone()[0]
    assert n == 1, "resuming a pending action never consumes another grant"


# ================================ §3 acknowledgment boundary ================

class ScopedReceiver:
    """Receiver whose accept() returns a SCOPED acceptance receipt, not
    a bare id."""

    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.ledger = self.root / "receipts.jsonl"

    def accept(self, artifact, receipt: dict) -> dict:
        self.receipts().append(receipt)
        with open(self.ledger, "a") as fh:
            fh.write(json.dumps(receipt) + "\n")
        return receipt

    def receipts(self) -> list:
        if not self.ledger.exists():
            return []
        return [json.loads(l) for l in open(self.ledger) if l.strip()]


def test_bare_receiver_id_cannot_acknowledge(tmp_path):
    mb, st, digest = _full_setup(tmp_path)
    m1 = mb.propose("agent-x", "question", "q", "investigate supply",
                    correlation_id="T-1")
    mb.deliver(m1, auth_id="A1", assignment_epoch=1)
    class BareReceiver:
        def receipts(self):
            return [m1]      # bare message id — NOT proof of acceptance
    settled = mb.reconcile_with_receiver(BareReceiver())
    assert settled == [], "a bare id is not an authenticated acceptance"
    state = mb.con.execute("SELECT state FROM messages WHERE message_id=?",
                           (m1,)).fetchone()[0]
    assert state == "sent", "no acked_at, no state change from bare ids"


def test_wrong_task_epoch_session_digest_project_refused(tmp_path):
    mb, st, digest = _full_setup(tmp_path)
    m1 = mb.propose("agent-x", "question", "q", "investigate supply",
                    correlation_id="T-1")
    mb.deliver(m1, auth_id="A1", assignment_epoch=1)
    art = tmp_path / "ack.md"
    art.write_text("ack")
    os.chmod(art, 0o644)
    tok = SessionTokenTransport({str(art): "ses-owner"})
    base = {"ack_of": m1, "project": "proj-a", "task_ref": "T-1",
            "epoch": 1, "session_id": "ses-owner",
            "message_digest": digest}
    bindings = {("proj-a", "T-1", 1): "ses-owner"}
    for bad, why in (
        ({**base, "task_ref": "T-OTHER"}, "task"),
        ({**base, "epoch": 2}, "epoch"),
        ({**base, "session_id": "ses-other"}, "session"),
        ({**base, "message_digest": "bad"}, "digest"),
        ({**base, "project": "proj-b"}, "project"),
    ):
        res = mb.record_ack(m1, from_agent="agent-x", transport=tok,
                            artifact_path=art, envelope=bad,
                            session_bindings=bindings)
        assert res == "ack-unverified", f"{why} mismatch must be refused"


def test_genuine_scoped_acceptance_recovers_lost_ack(tmp_path):
    mb, st, digest = _full_setup(tmp_path)
    m1 = mb.propose("agent-x", "question", "q", "investigate supply",
                    correlation_id="T-1")
    mb.deliver(m1, auth_id="A1", assignment_epoch=1)
    art = next((tmp_path / "inbox").rglob("*.md"))
    recv = ScopedReceiver(tmp_path / "receiver")
    recv.accept(art, {"ack_of": m1, "project": "proj-a", "task_ref": "T-1",
                      "epoch": 1, "session_id": "ses-owner",
                      "message_digest": digest, "action_id": m1})
    settled = mb.reconcile_with_receiver(
        recv, session_bindings={("proj-a", "T-1", 1): "ses-owner"})
    assert m1 in settled
    assert mb.con.execute("SELECT state FROM messages WHERE message_id=?",
                          (m1,)).fetchone()[0] == "acked"


def test_invalid_receipt_after_valid_ack_leaves_state_unchanged(tmp_path):
    mb, st, digest = _full_setup(tmp_path)
    m1 = mb.propose("agent-x", "question", "q", "investigate supply",
                    correlation_id="T-1")
    mb.deliver(m1, auth_id="A1", assignment_epoch=1)
    art = tmp_path / "ack.md"
    art.write_text("ack")
    os.chmod(art, 0o644)
    tok = SessionTokenTransport({str(art): "ses-owner"})
    env = {"ack_of": m1, "project": "proj-a", "task_ref": "T-1",
           "epoch": 1, "session_id": "ses-owner", "message_digest": digest}
    assert mb.record_ack(m1, from_agent="agent-x", transport=tok,
                         artifact_path=art, envelope=env,
                         session_bindings={("proj-a", "T-1", 1): "ses-owner"}
                         ) == "acked"
    bad = mb.record_ack(m1, from_agent="agent-x", transport=tok,
                        artifact_path=art,
                        envelope={**env, "message_digest": "bad"},
                        session_bindings={("proj-a", "T-1", 1): "ses-owner"})
    assert bad == "ack-unverified"
    row = mb.con.execute("SELECT state, acked_at FROM messages "
                         "WHERE message_id=?", (m1,)).fetchone()
    assert row[0] == "acked" and row[1] is not None, \
        "a later forgery never downgrades a valid ack"


# ============================== §4 verification boundary ====================

def _incident(tmp):
    con = _db(tmp)
    deps = __import__("yaml").safe_load(open(DEPS))
    st = IncidentStore(con, load_correlations(deps), project="alpha")
    from director_consumer.schema import Assessment, SourceRef
    a = Assessment(rule_id="factory-junk-flood", severity="P2",
                   impacts=["intake"], action="propose_investigation",
                   authority="agent:m5-opencode", rationale="suspected x",
                   evidence=[SourceRef("factory", "s", None, time.time())],
                   event_kind="junk_flood", event_summary="junk",
                   trigger_key=f"t-{time.time_ns()}")
    return st, st.observe(a)


def test_wellformed_failure_result_cannot_close(tmp_path):
    st, iid = _incident(tmp_path)
    with pytest.raises(RecoveryError):
        st.resolve(iid, "closed", receipt={
            "source": "jobs", "scope": f"incident:{iid}",
            "observed_at": NOW(), "method": "check",
            "result": "FAILED: service still broken"})
    assert any(x.get("incident_id") == iid for x in st.open_incidents())


def test_unresolvable_source_cannot_verify(tmp_path):
    st, iid = _incident(tmp_path)
    with pytest.raises(RecoveryError):
        st.resolve(iid, "closed", receipt={
            "source": "/does/not/resolve.jsonl", "scope": f"incident:{iid}",
            "observed_at": NOW(), "method": "check", "result": "all clean"})
    # a resolvable source passes
    real = tmp_path / "facts.jsonl"
    real.write_text(json.dumps({"junk": 0}) + "\n")
    st.resolve(iid, "service recovered", receipt={
        "source": str(real), "scope": f"incident:{iid}",
        "observed_at": NOW(), "method": "scan", "result": "all clean"})
    row = st.con.execute("SELECT verified_cause, verified_outcome "
                         "FROM incidents WHERE incident_id=?",
                         (iid,)).fetchone()
    assert row[1] == "all clean", "outcome verified"
    assert row[0] is None, \
        "a generic observation is never a verified CAUSAL explanation"


def test_investigation_still_broken_does_not_close_production(tmp_path):
    from director_consumer.tasks import TaskStore
    con = _db(tmp_path)
    ts = TaskStore(con, project="alpha")
    ts.create("INV-1", authority="director", evidence="investigation")
    ts.transition("INV-1", "offered", actor="director", evidence="e",
                  owner="agent-a")
    ts.transition("INV-1", "acknowledged", actor="agent-a", evidence="e")
    ts.transition("INV-1", "active", actor="agent-a", evidence="e")
    ts.transition("INV-1", "submitted", actor="agent-a",
                  evidence="report: root cause still unknown, service "
                           "still broken")
    # verification of the REPORT is about report quality; a finding of
    # "still broken" verifies the investigation but must not touch the
    # production incident
    st = IncidentStore(con, load_correlations(
        __import__("yaml").safe_load(open(DEPS))), project="alpha")
    from director_consumer.schema import Assessment, SourceRef
    iid = st.observe(Assessment(
        rule_id="factory-junk-flood", severity="P2", impacts=["intake"],
        action="propose_investigation", authority="agent:m5-opencode",
        rationale="suspected", evidence=[SourceRef("f", "s", None,
                                                   time.time())],
        event_kind="junk_flood", event_summary="junk", trigger_key="x1"))
    ts.transition("INV-1", "verified", actor="verifier",
                  evidence="report verified as accurate")
    assert any(x.get("incident_id") == iid for x in st.open_incidents()), \
        "a verified investigation never closes the production incident"


def test_generic_observation_never_becomes_cause(tmp_path):
    st, iid = _incident(tmp_path)
    real = tmp_path / "facts.jsonl"
    real.write_text("{}\n")
    st.resolve(iid, "service recovered", receipt={
        "source": str(real), "scope": f"incident:{iid}",
        "observed_at": NOW(), "method": "observation",
        "result": "service is back", "kind": "service-recovery"})
    row = st.con.execute("SELECT verified_cause FROM incidents "
                         "WHERE incident_id=?", (iid,)).fetchone()
    assert row[0] is None, "root cause stays unknown — that is allowed"
    # a causal claim requires an explicit causal receipt
    with pytest.raises(RecoveryError):
        st.establish_cause(iid, receipt={
            "source": str(real), "scope": f"incident:{iid}",
            "observed_at": NOW(), "method": "observation",
            "result": "probably the rsync"})
    st.establish_cause(iid, receipt={
        "source": str(real), "scope": f"incident:{iid}",
        "observed_at": NOW(), "method": "root-cause analysis",
        "result": "unescaped LIKE wildcard in junk query",
        "kind": "root-cause", "causal": True})
    row2 = st.con.execute("SELECT verified_cause FROM incidents "
                          "WHERE incident_id=?", (iid,)).fetchone()
    assert row2[0] == "unescaped LIKE wildcard in junk query"
