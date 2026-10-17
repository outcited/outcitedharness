"""05C corrective acceptance — NEGATIVE tests first (review discipline).

Each test asserts the corrected contract; against 322cdd95 every one
fails for its intended reason (recorded in ACCEPTANCE-LEDGER-05C.md).
"""
from __future__ import annotations

import json
import multiprocessing as mp
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from director_consumer.authorization import (AuthRecord, AuthRequest,
                                             AuthorizationStore)
from director_consumer.incidents import IncidentStore, RecoveryError, load_correlations
from director_consumer.messaging import (DeliveryDisabled, DeliveryPolicy,
                                         MailboxDelivery)
from director_consumer.continuity import FactoryContinuity

ROOT = Path(__file__).resolve().parents[1]
DEPS = ROOT / "director_consumer" / "deps.yaml"


def _db(tmp, n="d.db"):
    con = sqlite3.connect(tmp / n, timeout=30)
    con.row_factory = sqlite3.Row
    return con


def _approval(apdir, auth_id, **fields):
    f = apdir / f"{auth_id}.json"
    f.write_text(json.dumps(fields))
    os.chmod(f, 0o640)
    return f


def _store(con, tmp):
    apdir = tmp / "approvals"
    apdir.mkdir(exist_ok=True)
    return AuthorizationStore(con, approvals_dir=apdir,
                              operator_uid=os.getuid(),
                              service_uid=os.getuid() + 1,
                              test_identities=True), apdir


# =============================================== 1. REHEARSAL SAFETY

def test_rehearsal_refuses_non_test_targets_and_leaves_them_unchanged(tmp_path):
    from director_consumer.rehearse import prepare_workspace, RehearsalRefusal
    # a git repository must be refused and left byte-identical
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    marker = repo / "precious.txt"
    marker.write_text("do not delete")
    before = sorted(p.name for p in repo.iterdir())
    with pytest.raises(RehearsalRefusal):
        prepare_workspace(repo)
    assert sorted(p.name for p in repo.iterdir()) == before, \
        "refusal must leave the target untouched"
    assert marker.read_text() == "do not delete"
    # home-like target refused
    home = tmp_path / "home"
    home.mkdir()
    (home / ".bashrc").write_text("x")
    with pytest.raises(RehearsalRefusal):
        prepare_workspace(home)
    assert (home / ".bashrc").exists()
    # existing director workspace refused (director.db present)
    ws = tmp_path / "old-ws"
    ws.mkdir()
    (ws / "director.db").write_text("state")
    with pytest.raises(RehearsalRefusal):
        prepare_workspace(ws)
    assert (ws / "director.db").read_text() == "state"
    # symlink refused
    link = tmp_path / "link"
    os.symlink(str(marker), link)
    with pytest.raises(RehearsalRefusal):
        prepare_workspace(link)
    # a clean empty dir yields a NEW isolated child
    clean = tmp_path / "clean"
    clean.mkdir()
    child = prepare_workspace(clean)
    assert child.parent == clean and child.name.startswith("rehearsal-")
    assert child.is_dir() and not os.path.islink(child)


# ============================ 2. ONE APPROVAL, ONE IMMUTABLE ACTION

def _setup_delivery(tmp, body="investigate supply", subject="q",
                    msg_type="question", correlation="T-1",
                    transport="nas-inbox"):
    con = _db(tmp)
    st, apdir = _store(con, tmp)
    digest = __import__("director_consumer.schema", fromlist=["fp"]) \
        .fp("msg-body", body)
    _approval(apdir, "A1", project="proj-a", action="deliver",
              recipient="agent-x", task_ref=correlation,
              expires_at=time.time() + 3600, issuer="sam",
              payload_digest=digest, msg_type=msg_type, subject=subject,
              transport=transport, assignment_epoch=1,
              session_binding="ses-owner", action_id="")
    st.issue(AuthRecord(auth_id="A1", project="proj-a", action="deliver",
                        recipient="agent-x", task_ref=correlation,
                        expires_at=time.time() + 3600, issuer="sam",
                        payload_digest=digest))
    pol = DeliveryPolicy(outbox_root=tmp / "inbox", require_existing_root=False)
    mb = MailboxDelivery(con, pol, authorizations=st, project="proj-a",
                         mode="test-delivery", transport_name=transport)
    return mb, digest


def test_identical_body_cannot_become_second_action(tmp_path):
    mb, digest = _setup_delivery(tmp_path)
    mid = mb.propose("agent-x", "question", "q", "investigate supply",
                     correlation_id="T-1")
    assert mb.deliver(mid, auth_id="A1", assignment_epoch=1) == "sent"
    # same body, different SUBJECT
    m2 = mb.propose("agent-x", "question", "DIFFERENT SUBJECT",
                    "investigate supply", correlation_id="T-1")
    with pytest.raises(DeliveryDisabled):
        mb.deliver(m2, auth_id="A1", assignment_epoch=1)
    # same body, different MESSAGE TYPE
    m3 = mb.propose("agent-x", "status", "q", "investigate supply",
                    correlation_id="T-1")
    with pytest.raises(DeliveryDisabled):
        mb.deliver(m3, auth_id="A1", assignment_epoch=1)
    # same body, different RECIPIENT (destination)
    m4 = mb.propose("agent-y", "question", "q", "investigate supply",
                    correlation_id="T-1")
    with pytest.raises(DeliveryDisabled):
        mb.deliver(m4, auth_id="A1", assignment_epoch=1)
    # same body, different TASK
    m5 = mb.propose("agent-x", "question", "q", "investigate supply",
                    correlation_id="T-OTHER")
    with pytest.raises(DeliveryDisabled):
        mb.deliver(m5, auth_id="A1", assignment_epoch=1)
    # same body, different ASSIGNMENT EPOCH
    m6 = mb.propose("agent-x", "question", "q", "investigate supply",
                    correlation_id="T-1", assignment_epoch=2)
    with pytest.raises(DeliveryDisabled):
        mb.deliver(m6, auth_id="A1", assignment_epoch=2)
    assert len(list((tmp_path / "inbox").rglob("*.md"))) == 1, \
        "one approval = one immutable action"


def test_conflicting_artifact_never_overwritten(tmp_path):
    mb, _ = _setup_delivery(tmp_path)
    mid = mb.propose("agent-x", "question", "q", "investigate supply",
                     correlation_id="T-1")
    mb.deliver(mid, auth_id="A1", assignment_epoch=1)
    art = next((tmp_path / "inbox").rglob("*.md"))
    original = art.read_text()
    art.write_text("TAMPERED DIFFERENT CONTENT")
    # deliver again (state already sent -> idempotent short circuit) ...
    assert mb.deliver(mid, auth_id="A1", assignment_epoch=1) == "sent"
    # ... but the reconciling writer must never clobber conflicting bytes
    con = mb.con
    con.execute("UPDATE messages SET state='delivering' WHERE message_id=?",
                (mid,))
    con.commit()
    r = mb.deliver(mid, auth_id="A1", assignment_epoch=1)
    assert r == "conflict", "different content, same identity = integrity conflict"
    assert art.read_text() == "TAMPERED DIFFERENT CONTENT", \
        "conflicting evidence is never overwritten"
    assert original != art.read_text()


# ================================================ 3. ACKNOWLEDGMENT ORIGIN

class TokenTransport:
    """Trusted adapter that ESTABLISHES origin from the artifact's
    embedded session token — not from caller-declared text."""
    def __init__(self, tokens):
        self.tokens = tokens      # artifact_path -> true session id

    def verify_origin(self, artifact_path):
        return self.tokens.get(str(artifact_path))


def test_missing_bindings_and_spoofed_session_cannot_ack(tmp_path):
    mb, _ = _setup_delivery(tmp_path)
    mid = mb.propose("agent-x", "question", "q", "investigate supply",
                     correlation_id="T-1")
    mb.deliver(mid, auth_id="A1", assignment_epoch=1)
    from director_consumer.schema import fp as _fp
    digest = _fp("msg-body", "investigate supply")
    art = tmp_path / "ack.md"
    art.write_text("ack")
    os.chmod(art, 0o644)
    env = {"ack_of": mid, "project": "proj-a", "task_ref": "T-1",
           "epoch": 1, "session_id": "ses-owner", "message_digest": digest}
    # 1) missing session_bindings must NOT bypass checks
    st = mb.record_ack(mid, from_agent="agent-x",
                       transport=TokenTransport({str(art): "ses-owner"}),
                       artifact_path=art, envelope=env, session_bindings=None)
    assert st == "ack-unverified", "missing bindings must fail closed"
    # 2) wrong same-UID session CLAIMING the owner's session id is rejected:
    #    the transport establishes true origin = ses-other
    bindings = {("proj-a", "T-1", 1): "ses-owner"}
    st2 = mb.record_ack(mid, from_agent="agent-x",
                        transport=TokenTransport({str(art): "ses-other"}),
                        artifact_path=art, envelope=env,
                        session_bindings=bindings)
    assert st2 == "ack-unverified", \
        "caller-declared session text is not origin proof"
    assert mb.snapshot()  # quarantine preserved
    # 3) genuine origin (transport confirms ses-owner) is accepted
    st3 = mb.record_ack(mid, from_agent="agent-x",
                        transport=TokenTransport({str(art): "ses-owner"}),
                        artifact_path=art, envelope=env,
                        session_bindings=bindings)
    assert st3 == "acked"
    # and a later forgery cannot downgrade
    st4 = mb.record_ack(mid, from_agent="agent-x",
                        transport=TokenTransport({str(art): "ses-evil"}),
                        artifact_path=art,
                        envelope={**env, "message_digest": "x"},
                        session_bindings=bindings)
    assert st4 == "ack-unverified"
    assert mb.snapshot()[0]["state"] in ("acked", "ack-unverified")


# ================================================== 4. VERIFICATION CLOSURE

def _incident(tmp):
    con = _db(tmp)
    deps = json.loads(json.dumps(__import__("yaml").safe_load(
        open(DEPS))))
    st = IncidentStore(con, load_correlations(deps), project="alpha")
    from director_consumer.schema import Assessment, SourceRef
    a = Assessment(rule_id="factory-junk-flood", severity="P2",
                   impacts=["intake"], action="propose_investigation",
                   authority="agent:m5-opencode", rationale="suspected x",
                   evidence=[SourceRef("factory", "s", None, time.time())],
                   event_kind="junk_flood", event_summary="junk",
                   trigger_key=f"t-{time.time_ns()}")
    return st, st.observe(a)


def test_resolve_without_evidence_never_hides_incident(tmp_path):
    st, iid = _incident(tmp_path)
    with pytest.raises(RecoveryError):
        st.resolve(iid, "looks done")      # no receipt: refused
    assert any(x.get("incident_id") == iid for x in st.open_incidents()), \
        "unresolved incident must remain visible"


def test_receipt_validation_rejects_rubbish(tmp_path):
    st, iid = _incident(tmp_path)
    # five nonempty fields is NOT sufficient: scope must match, age must be
    # valid, result must be meaningful
    bad_scope = {"source": "s", "scope": "some other incident",
                 "observed_at": "2026-10-17T00:00:00Z", "method": "m",
                 "result": "r"}
    with pytest.raises(RecoveryError):
        st.resolve(iid, "fixed", receipt=bad_scope)
    bad_time = {"source": "s", "scope": f"incident:{iid}",
                "observed_at": "not-a-time", "method": "m", "result": "r"}
    with pytest.raises(RecoveryError):
        st.resolve(iid, "fixed", receipt=bad_time)
    stale = {"source": "s", "scope": f"incident:{iid}",
             "observed_at": "2020-01-01T00:00:00Z", "method": "m",
             "result": "r"}
    with pytest.raises(RecoveryError):
        st.resolve(iid, "fixed", receipt=stale)
    ok = {"source": "jobs table", "scope": f"incident:{iid}",
          "observed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
          "method": "count", "result": "0 new junk"}
    st.resolve(iid, "service recovered", receipt=ok)
    row = st.con.execute("SELECT verified_cause, state FROM incidents "
                         "WHERE incident_id=?", (iid,)).fetchone()
    assert row[0] == "0 new junk" and row[1] == "resolved"


def test_verifier_rejects_false_report_claims(tmp_path):
    from director_consumer.rehearse import VerifierFixture
    src = tmp_path / "source-facts.json"
    src.write_text(json.dumps({"junk_dead_jobs": 5}))
    v = VerifierFixture(src)
    assert v.verify({"claim": "junk is zero"})["verdict"] == "reject"
    assert v.verify({"claim": "junk is zero", "junk_dead_jobs": 0
                     })["verdict"] == "reject", \
        "a false claim about the source must be rejected"
    assert v.verify({"claim": "junk present", "junk_dead_jobs": 5
                     })["verdict"] == "accept"


def test_investigation_completion_is_not_production_recovery(tmp_path):
    st, iid = _incident(tmp_path)
    # investigation task can complete; the INCIDENT stays open
    st.lifecycle_transition(iid, "investigating", "d", "diagnosis")
    assert st.lifecycle(iid)["lifecycle_state"] == "investigating"
    assert any(x.get("incident_id") == iid for x in st.open_incidents())


# ========================================= 5. FAILURE TESTS THAT PROVE IT

def test_after_write_before_sent_failpoint_real(tmp_path):
    from director_consumer.rehearse import crash_child
    mb, _ = _setup_delivery(tmp_path)
    mid = mb.propose("agent-x", "question", "q", "investigate supply",
                     correlation_id="T-1")
    proc = subprocess.Popen(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, %r); "
         "from director_consumer.rehearse import crash_child; "
         "crash_child('after-write-before-sent', %r, %r, 'A1')" % (
             str(ROOT), str(tmp_path), mid)])
    proc.wait(timeout=60)
    assert proc.returncode == 9, "failpoint must kill the child exactly"
    art = list((tmp_path / "inbox").rglob(f"*{mid}*.md"))
    assert len(art) == 1, "the artifact was written before the crash"
    row = mb.con.execute("SELECT state FROM messages WHERE message_id=?",
                         (mid,)).fetchone()[0]
    assert row == "delivering", "checkpoint: sent was never recorded"
    # resume completes WITHOUT a second artifact
    assert mb.deliver(mid, auth_id="A1", assignment_epoch=1) == "sent"
    assert len(list((tmp_path / "inbox").rglob(f"*{mid}*.md"))) == 1


def test_recurrence_rejects_old_evidence_but_accepts_current(tmp_path):
    st, iid = _incident(tmp_path)
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    receipt = {"source": "jobs", "scope": f"incident:{iid}",
               "observed_at": now, "method": "scan", "result": "clean"}
    # legal recovery path up to recovery_verified (occurrence 1)
    for to in ("investigating", "mitigation_proposed", "repair_in_progress",
               "recovery_observed"):
        st.lifecycle_transition(iid, to, "d", "e")
    st.mark_recovery_evidence(iid, "root-cause-fixed", "fix-1", receipt=receipt)
    assert st.lifecycle_transition(iid, "recovery_verified", "d", "e") == \
        "recovery_verified"
    st.lifecycle_transition(iid, "closed", "d", "e")
    # recurrence -> new occurrence
    st.lifecycle_transition(iid, "detected", "d", "returned")
    st.lifecycle_transition(iid, "investigating", "d", "e")
    st.lifecycle_transition(iid, "mitigation_proposed", "d", "e")
    st.lifecycle_transition(iid, "repair_in_progress", "d", "e")
    st.lifecycle_transition(iid, "recovery_observed", "d", "e")
    # OLD-occurrence evidence alone must fail ...
    with pytest.raises(RecoveryError):
        st.lifecycle_transition(iid, "recovery_verified", "d", "e")
    # ... and CURRENT-occurrence evidence must succeed
    st.mark_recovery_evidence(iid, "root-cause-fixed", "fix-2", receipt=receipt)
    assert st.lifecycle_transition(iid, "recovery_verified", "d", "e") == \
        "recovery_verified"


def test_lost_ack_receiver_reconciliation(tmp_path):
    """Receiver accepted but the ack was lost: reconciliation must find
    the receiver receipt and settle the message without resending."""
    from director_consumer.rehearse import Receiver
    mb, _ = _setup_delivery(tmp_path)
    mid = mb.propose("agent-x", "question", "q", "investigate supply",
                     correlation_id="T-1")
    mb.deliver(mid, auth_id="A1", assignment_epoch=1)
    art = next((tmp_path / "inbox").rglob(f"*{mid}*.md"))
    recv = Receiver(tmp_path / "receiver")
    assert recv.accept(art, mid) == "accepted"
    # ack lost: sender still 'sent'
    assert mb.con.execute("SELECT state FROM messages WHERE message_id=?",
                          (mid,)).fetchone()[0] == "sent"
    settled = mb.reconcile_with_receiver(recv)
    assert mid in settled, "receiver receipt settles the lost ack"
    assert mb.con.execute("SELECT state FROM messages WHERE message_id=?",
                          (mid,)).fetchone()[0] == "acked"
    # and nothing is resent: receiver sees exactly one acceptance
    assert len(recv.receipts()) == 1


def test_concurrent_callers_every_outcome_accounted(tmp_path):
    mb, _ = _setup_delivery(tmp_path)
    mid = mb.propose("agent-x", "question", "q", "investigate supply",
                     correlation_id="T-1")
    barrier = mp.Barrier(6)

    def racer(q, ws, mid, barrier):
        try:
            con = sqlite3.connect(ws + "/d.db", timeout=30)
            con.row_factory = sqlite3.Row
            apdir = Path(ws) / "approvals"
            st = AuthorizationStore(con, approvals_dir=apdir,
                                    operator_uid=os.getuid(),
                                    service_uid=os.getuid() + 1,
                                    test_identities=True)
            mbb = MailboxDelivery(con, DeliveryPolicy(outbox_root=Path(ws) / "inbox"),
                                  authorizations=st, project="proj-a",
                                  mode="test-delivery")
            barrier.wait(timeout=30)      # release all racers together
            q.put(mbb.deliver(mid, auth_id="A1", assignment_epoch=1))
        except Exception as e:  # noqa: BLE001
            q.put(type(e).__name__)

    import functools
    fn = functools.partial(_racer_entry, tmp_path=str(tmp_path), mid=mid)
    q = mp.Queue()
    procs = [mp.Process(target=_racer_entry,
                        args=(q, str(tmp_path), mid, barrier))
             for _ in range(6)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=60)
    outcomes = [q.get(timeout=10) for _ in range(6)]
    files = list((tmp_path / "inbox").rglob(f"*{mid}*.md"))
    assert all(o == "sent" for o in outcomes), \
        f"every caller must observe a settled outcome: {outcomes}"
    assert len(files) == 1, "exactly one durable message"
    row = mb.con.execute("SELECT state FROM messages WHERE message_id=?",
                         (mid,)).fetchone()[0]
    assert row == "sent"


def _racer_entry(q, ws, mid, barrier):
    try:
        con = sqlite3.connect(ws + "/d.db", timeout=30)
        con.row_factory = sqlite3.Row
        apdir = Path(ws) / "approvals"
        st = AuthorizationStore(con, approvals_dir=apdir,
                                operator_uid=os.getuid(),
                                service_uid=os.getuid() + 1,
                                test_identities=True)
        mbb = MailboxDelivery(con, DeliveryPolicy(outbox_root=Path(ws) / "inbox"),
                              authorizations=st, project="proj-a",
                              mode="test-delivery",
                              transport_name="nas-inbox")
        barrier.wait(timeout=30)
        q.put(mbb.deliver(mid, auth_id="A1", assignment_epoch=1))
    except Exception as e:  # noqa: BLE001
        q.put(type(e).__name__)


# ================================================ 6. FACTORY UNITS

def test_chunks_and_jobs_are_not_summed_as_documents(tmp_path):
    con = sqlite3.connect(tmp_path / "f.db")
    con.executescript("CREATE TABLE factory_chunks (state TEXT, "
                      "lease_expires_at TEXT);")
    for _ in range(10):
        con.execute("INSERT INTO factory_chunks VALUES ('queued', NULL)")
    con.commit()
    con.close()
    con = sqlite3.connect(tmp_path / "p.db")
    con.executescript("CREATE TABLE jobs (state TEXT, corpus_key TEXT, "
                      "source_path TEXT);")
    for _ in range(5):
        con.execute("INSERT INTO jobs VALUES ('pending', 'pdf:a', '/x.pdf')")
    con.commit()
    con.close()
    fc = FactoryContinuity(factory_db=tmp_path / "f.db",
                           pipeline_db=tmp_path / "p.db")
    m = fc.assess([])
    assert m.ready_backlog_docs == "unknown", \
        "chunks + jobs are different populations; a document total is a lie"
    assert m.lane_counts == {"rust_chunks": 10, "substrate_jobs": 5}


def test_starvation_never_divides_mismatched_populations(tmp_path):
    con = sqlite3.connect(tmp_path / "f.db")
    con.executescript("CREATE TABLE factory_chunks (state TEXT, "
                      "lease_expires_at TEXT);")
    for _ in range(200):
        con.execute("INSERT INTO factory_chunks VALUES ('queued', NULL)")
    con.commit()
    con.close()
    fx = tmp_path / "fx"
    fx.mkdir()
    (fx / "done-a.txt").write_text("x\n" * 50)
    fc = FactoryContinuity(factory_db=tmp_path / "f.db", facet_root=fx)
    m = fc.assess([{"t": 0.0, "total": 0}, {"t": 3600.0, "total": 50}])
    assert m.starvation_hours == "unknown", \
        "queue items over facet-pass docs is not a rate division"
    assert m.starvation_basis == "population-mismatch"
    assert m.lane_counts == {"rust_chunks": 200, "substrate_jobs": "unknown"}


def test_insufficient_evidence_emits_no_idle_prophecy(tmp_path):
    con = sqlite3.connect(tmp_path / "f.db")
    con.executescript("CREATE TABLE factory_chunks (state TEXT, "
                      "lease_expires_at TEXT);")
    con.commit()
    con.close()
    l0 = tmp_path / "l0.jsonl"
    l0.write_text(json.dumps({"source_path": "/vault/a.pdf",
                              "doc_class": "datasheet", "page_count": 3}))
    bl = tmp_path / "bl.json"
    bl.write_text(json.dumps(["/vault/a.pdf"]))
    fc = FactoryContinuity(factory_db=tmp_path / "f.db", l0_index=l0,
                           backlog_paths=bl,
                           class_costs={"datasheet": {"hours_per_doc": 0.01}})
    m = fc.assess([])
    kinds = [e.kind for e in fc.events(m)]
    assert "queue_empty" not in kinds, \
        "with inventory elsewhere, 'workers will idle' is a false prophecy"
    assert "starvation_risk" not in kinds
    assert not any("workers will idle" in e.summary for e in fc.events(m))
