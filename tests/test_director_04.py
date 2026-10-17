"""DIRECTOR-04 tests — §10 matrix: task ownership, delivery/ack, recovery
lifecycle, manufacturer feed, funnel admission, isolation, restart safety.

Simulated agents and isolated test mailboxes only. No production contact.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from director_consumer.incidents import (IncidentStore, RecoveryError,
                                         load_correlations)
from director_consumer.manufacturers import ManufacturerTelemetry
from director_consumer.funnel import build_funnel
from director_consumer.messaging import (DeliveryDisabled, DeliveryPolicy,
                                         MailboxDelivery)
from director_consumer.tasks import (FencingError, TaskStore, TransitionError,
                                     reconcile_request)

ROOT = Path(__file__).resolve().parents[1]
DEPS = ROOT / "director_consumer" / "deps.yaml"


def _db(tmp_path, name="d.db"):
    con = sqlite3.connect(tmp_path / name)
    con.row_factory = sqlite3.Row
    return con


# ------------------------------------------------ §2 task ownership

def test_task_lifecycle_happy_path(tmp_path):
    ts = TaskStore(_db(tmp_path), project="designwins")
    ts.create("SUPPLY-RECON-01", authority="director",
              evidence="shadow:queue-empty+inventory")
    ts.transition("SUPPLY-RECON-01", "offered", actor="director",
                  evidence="offered to agent-m5 by authority policy",
                  owner="agent:m5-opencode")
    ts.transition("SUPPLY-RECON-01", "acknowledged", actor="agent:m5-opencode",
                  evidence="ack mail 20261016T…_m5-opencode_answer_ack.md")
    ts.transition("SUPPLY-RECON-01", "active", actor="agent:m5-opencode",
                  evidence="session ses_x started, branch fix/supply")
    ts.transition("SUPPLY-RECON-01", "submitted", actor="agent:m5-opencode",
                  evidence="PR #12 + evidence pack")
    ts.transition("SUPPLY-RECON-01", "verified", actor="director",
                  evidence="factory_chunks queued > 0 measured 2026-10-17")
    ts.transition("SUPPLY-RECON-01", "closed", actor="director",
                  evidence="verified, no recurrence in 24h")
    assert ts.get("SUPPLY-RECON-01").state == "closed"


def test_illegal_transition_rejected(tmp_path):
    ts = TaskStore(_db(tmp_path))
    ts.create("T-1", authority="director", evidence="e")
    with pytest.raises(TransitionError):
        ts.transition("T-1", "verified", actor="x", evidence="e")   # unassigned->verified
    with pytest.raises(TransitionError):
        ts.transition("T-1", "active", actor="x", evidence="e")


def test_evidence_mandatory(tmp_path):
    ts = TaskStore(_db(tmp_path))
    ts.create("T-2", authority="director", evidence="e")
    with pytest.raises(TransitionError):
        ts.transition("T-2", "offered", actor="x", evidence="")


def test_two_agents_claim_same_task_conflict(tmp_path):
    ts = TaskStore(_db(tmp_path))
    ts.create("EVIDENCE-SUPPLY-04", authority="director",
              evidence="recon of 17 unknown tasks")
    ts.transition("EVIDENCE-SUPPLY-04", "offered", actor="director",
                  evidence="offer A", owner="agent-a")
    # second claim arrives from another agent -> conflicted
    ts.transition("EVIDENCE-SUPPLY-04", "conflicted", actor="agent-b",
                  evidence="agent-b claims same task via branch b/task")
    rec = ts.get("EVIDENCE-SUPPLY-04")
    assert rec.state == "conflicted"
    req = reconcile_request("EVIDENCE-SUPPLY-04", ["agent-a", "agent-b"],
                            ["commit:x", "branch:b/task"])
    assert req["proposed"] is True and "accountable owner" in req["question"]


def test_ack_then_disappear_goes_stale_with_fence(tmp_path):
    ts = TaskStore(_db(tmp_path))
    ts.create("T-3", authority="director", evidence="e")
    ts.transition("T-3", "offered", actor="director", evidence="offer",
                  owner="agent-a")
    ts.transition("T-3", "acknowledged", actor="agent-a", evidence="ack")
    rec = ts.transition("T-3", "stale", actor="director",
                        evidence="no progress evidence in 7d")
    assert rec.state == "stale"
    # stale holder cannot silently reclaim with an old epoch
    with pytest.raises(FencingError):
        ts.transition("T-3", "active", actor="agent-a",
                      evidence="reclaim attempt", expect_epoch=0)


def test_owner_migration_keeps_identity(tmp_path):
    ts = TaskStore(_db(tmp_path))
    ts.create("T-4", authority="director", evidence="e")
    ts.transition("T-4", "offered", actor="director", evidence="offer",
                  owner="agent-a", host="m5")
    rec = ts.transition("T-4", "acknowledged", actor="agent-a",
                        evidence="ack; moving to ryzen-2",
                        host="ryzen-2")   # machine changes
    assert rec.host == "ryzen-2" and rec.accountable_owner == "agent-a", \
        "stable task identity survives machine migration"


def test_fencing_epoch_rejects_stale_reclaim(tmp_path):
    ts = TaskStore(_db(tmp_path))
    ts.create("T-5", authority="director", evidence="e")
    ts.transition("T-5", "offered", actor="director", evidence="offer",
                  owner="agent-a")
    rec1 = ts.get("T-5")
    ts.transition("T-5", "offered", actor="director", evidence="re-offer",
                  owner="agent-b")      # epoch bumps
    with pytest.raises(FencingError):
        ts.transition("T-5", "acknowledged", actor="agent-a",
                      evidence="stale ack", expect_epoch=rec1.epoch)


def test_failed_verification_returns_to_active(tmp_path):
    ts = TaskStore(_db(tmp_path))
    ts.create("T-6", authority="director", evidence="e")
    for st, ev, actor in (("offered", "offer", "director"),
                          ("acknowledged", "ack", "agent-a"),
                          ("active", "work", "agent-a"),
                          ("submitted", "PR", "agent-a")):
        ts.transition("T-6", st, actor=actor, evidence=ev,
                      owner="agent-a" if st == "offered" else "")
    rec = ts.transition("T-6", "active", actor="director",
                        evidence="verification FAILED: tests 3/10")
    assert rec.state == "active"


# ------------------------------------------------ §3 delivery + ack

def test_delivery_gate_closed_by_default(tmp_path):
    con = _db(tmp_path)
    pol = DeliveryPolicy.load(None, tmp_path / "boxes")
    mb = MailboxDelivery(con, pol)
    mid = mb.propose("m5-opencode", "question", "q", "body")
    with pytest.raises(DeliveryDisabled):
        mb.deliver(mid)
    # state stays proposed; audit records the refusal
    assert mb.snapshot()[0]["state"] == "proposed"


def _store_backed_delivery(tmp_path, con):
    """Real authorization service: operator approval artifact + store."""
    import os
    from director_consumer.authorization import AuthRecord, AuthorizationStore
    from director_consumer.schema import fp
    apdir = tmp_path / "approvals"
    apdir.mkdir(exist_ok=True)
    ast = AuthorizationStore(con, approvals_dir=apdir,
                             operator_uid=os.getuid(),
                             service_uid=os.getuid() + 1)
    return ast


def test_delivery_and_ack_are_distinct(tmp_path):
    con = _db(tmp_path)
    import os
    from director_consumer.authorization import AuthRecord, AuthorizationStore
    from director_consumer.schema import fp
    apdir = tmp_path / "approvals"
    apdir.mkdir(exist_ok=True)
    ast = AuthorizationStore(con, approvals_dir=apdir,
                             operator_uid=os.getuid(),
                             service_uid=os.getuid() + 1)
    pol = DeliveryPolicy(outbox_root=tmp_path / "boxes")
    mb = MailboxDelivery(con, pol, authorizations=ast)
    body = "body here"
    mid = mb.propose("m5-opencode", "question", "q", body,
                     correlation_id="T-ACK")
    f = apdir / "A-ACK.json"
    f.write_text(json.dumps({
        "project": "default", "action": "deliver",
        "recipient": "m5-opencode", "task_ref": "T-ACK",
        "expires_at": time.time() + 3600, "issuer": "sam",
        "payload_digest": fp("msg-body", body)}))
    os.chmod(f, 0o640)
    ast.issue(AuthRecord(auth_id="A-ACK", project="default",
                         action="deliver", recipient="m5-opencode",
                         task_ref="T-ACK",
                         expires_at=time.time() + 3600, issuer="sam",
                         payload_digest=fp("msg-body", body)))
    assert mb.deliver(mid, auth_id="A-ACK") == "sent"
    snap = mb.snapshot()[0]
    assert snap["state"] == "sent" and snap["acked_at"] is None, \
        "delivery is never acknowledgment"
    # unattributable ack text is evidence, NOT acceptance...
    assert mb.record_ack(mid, ack_body="looking into it",
                         from_agent="someone-else") == "ack-unverified"
    # ...only a verified sender matching the recipient is acceptance
    assert mb.record_ack(mid, ack_body="looking into it",
                         from_agent="m5-opencode",
                         verified_sender=True) == "acked"
    assert mb.snapshot()[0]["state"] == "acked"


def test_duplicate_delivery_idempotent(tmp_path):
    con = _db(tmp_path)
    import os
    from director_consumer.authorization import AuthRecord, AuthorizationStore
    from director_consumer.schema import fp
    apdir = tmp_path / "approvals"
    apdir.mkdir(exist_ok=True)
    ast = AuthorizationStore(con, approvals_dir=apdir,
                             operator_uid=os.getuid(),
                             service_uid=os.getuid() + 1)
    pol = DeliveryPolicy(outbox_root=tmp_path / "boxes")
    mb = MailboxDelivery(con, pol, authorizations=ast)
    body = "body"
    mid = mb.propose("m5-opencode", "question", "q", body,
                     correlation_id="T-DUP")
    f = apdir / "A-DUP.json"
    f.write_text(json.dumps({
        "project": "default", "action": "deliver",
        "recipient": "m5-opencode", "task_ref": "T-DUP",
        "expires_at": time.time() + 3600, "issuer": "sam",
        "payload_digest": fp("msg-body", body)}))
    os.chmod(f, 0o640)
    ast.issue(AuthRecord(auth_id="A-DUP", project="default",
                         action="deliver", recipient="m5-opencode",
                         task_ref="T-DUP",
                         expires_at=time.time() + 3600, issuer="sam",
                         payload_digest=fp("msg-body", body)))
    mb.deliver(mid, auth_id="A-DUP")
    mb.deliver(mid, auth_id="A-DUP")   # duplicate send (authorized retry)
    files = list((tmp_path / "boxes").rglob("*.md"))
    assert len(files) == 1, "duplicate delivery rewrites the same artifact"
    assert con.execute("SELECT attempts FROM messages "
                       "WHERE message_id=?", (mid,)).fetchone()[0] == 1


def test_delivery_without_ack_expires_and_escalates(tmp_path):
    con = _db(tmp_path)
    import os
    from director_consumer.authorization import AuthRecord, AuthorizationStore
    from director_consumer.schema import fp
    apdir = tmp_path / "approvals"
    apdir.mkdir(exist_ok=True)
    ast = AuthorizationStore(con, approvals_dir=apdir,
                             operator_uid=os.getuid(),
                             service_uid=os.getuid() + 1)
    pol = DeliveryPolicy(outbox_root=tmp_path / "boxes")
    mb = MailboxDelivery(con, pol, authorizations=ast)
    body = "body"
    mid = mb.propose("m5-opencode", "question", "q", body, ttl_s=-1,
                     correlation_id="T-EXP")
    f = apdir / "A-EXP.json"
    f.write_text(json.dumps({
        "project": "default", "action": "deliver",
        "recipient": "m5-opencode", "task_ref": "T-EXP",
        "expires_at": time.time() + 3600, "issuer": "sam",
        "payload_digest": fp("msg-body", body)}))
    os.chmod(f, 0o640)
    ast.issue(AuthRecord(auth_id="A-EXP", project="default",
                         action="deliver", recipient="m5-opencode",
                         task_ref="T-EXP",
                         expires_at=time.time() + 3600, issuer="sam",
                         payload_digest=fp("msg-body", body)))
    mb.deliver(mid, auth_id="A-EXP")
    expired = mb.expire_due()
    assert mid in expired
    assert mb.snapshot()[0]["state"] == "expired"


def test_crash_between_commit_and_delivery_recovers(tmp_path):
    con = _db(tmp_path)
    import os
    from director_consumer.authorization import AuthRecord, AuthorizationStore
    from director_consumer.schema import fp
    apdir = tmp_path / "approvals"
    apdir.mkdir(exist_ok=True)
    ast = AuthorizationStore(con, approvals_dir=apdir,
                             operator_uid=os.getuid(),
                             service_uid=os.getuid() + 1)
    pol = DeliveryPolicy(outbox_root=tmp_path / "boxes")
    mb = MailboxDelivery(con, pol, authorizations=ast)
    body = "body"
    mid = mb.propose("m5-opencode", "status", "s", body,
                     correlation_id="T-REC")
    f = apdir / "A-REC.json"
    f.write_text(json.dumps({
        "project": "default", "action": "deliver",
        "recipient": "m5-opencode", "task_ref": "T-REC",
        "expires_at": time.time() + 3600, "issuer": "sam",
        "payload_digest": fp("msg-body", body)}))
    os.chmod(f, 0o640)
    ast.issue(AuthRecord(auth_id="A-REC", project="default",
                         action="deliver", recipient="m5-opencode",
                         task_ref="T-REC",
                         expires_at=time.time() + 3600, issuer="sam",
                         payload_digest=fp("msg-body", body)))
    mb.deliver(mid, auth_id="A-REC")
    # simulate crash BEFORE the state write: state back to proposed
    con.execute("UPDATE messages SET state='proposed' WHERE message_id=?",
                (mid,))
    con.commit()
    recovered = mb.reconcile_mailbox()
    assert mid in recovered
    assert mb.snapshot()[0]["state"] == "sent"


# ------------------------------------------------ §4 recovery lifecycle

def _incident(tmp_path):
    con = _db(tmp_path)
    deps = json.loads(json.dumps(
        __import__("yaml").safe_load(DEPS.read_text())))
    st = IncidentStore(con, load_correlations(deps), project="designwins")
    from director_consumer.schema import Assessment, SourceRef
    a = Assessment(rule_id="factory-junk-flood", severity="P2",
                   impacts=["intake"], action="propose_investigation",
                   authority="agent:m5-opencode", rationale="r",
                   evidence=[SourceRef("factory", "s", None, time.time())],
                   event_kind="junk_flood", event_summary="AppleDouble 176709",
                   trigger_key=f"t-{time.time_ns()}")
    iid = st.observe(a)
    return con, st, iid


def test_symptom_clear_is_not_verified_recovery(tmp_path):
    con, st, iid = _incident(tmp_path)
    st.lifecycle_transition(iid, "investigating", "director", "triaged")
    st.lifecycle_transition(iid, "mitigation_proposed", "director",
                            "propose factory-side ._ filter")
    st.lifecycle_transition(iid, "repair_in_progress", "agent:mac-mini",
                            "filter deployed")
    st.lifecycle_transition(iid, "recovery_observed", "director",
                            "dead-jobs count flat 24h")
    st.mark_recovery_evidence(iid, "symptom-clear",
                              "no new dead jobs since deploy")
    with pytest.raises(RecoveryError):
        st.lifecycle_transition(iid, "recovery_verified", "director",
                                "symptom gone")
    # root-cause evidence arrives (contamination actually stopped)
    st.mark_recovery_evidence(iid, "root-cause-fixed",
                              "new-delivery scan: 0 ._ files in 3 batches")
    assert st.lifecycle_transition(iid, "recovery_verified", "director",
                                   "root cause measured absent") == \
        "recovery_verified"
    assert st.lifecycle_transition(iid, "closed", "director",
                                   "no recurrence") == "closed"


def test_sentinel_scenario_requires_fresh_run(tmp_path):
    con, st, iid = _incident(tmp_path)   # any incident for lifecycle law
    st.lifecycle_transition(iid, "investigating", "director",
                            "known defects: dir perms + PIPELINE_DB")
    st.lifecycle_transition(iid, "repair_in_progress", "agent:m5-opencode",
                            "repair request prepared")
    st.lifecycle_transition(iid, "recovery_observed", "director",
                            "launcher restarted")
    with pytest.raises(RecoveryError):
        st.lifecycle_transition(iid, "recovery_verified", "director",
                                "looks up")
    st.mark_recovery_evidence(iid, "verified-fresh-run",
                              "sentinel hourly exit 0, LKG refreshed")
    st.lifecycle_transition(iid, "recovery_verified", "director",
                            "fresh successful watcher runs")
    assert st.lifecycle(iid)["lifecycle_state"] == "recovery_verified"


def test_closed_only_from_verified(tmp_path):
    con, st, iid = _incident(tmp_path)
    st.lifecycle_transition(iid, "investigating", "director", "e")
    with pytest.raises(RecoveryError):
        st.lifecycle_transition(iid, "closed", "director", "e")


def test_recurrence_after_close_reopens(tmp_path):
    con, st, iid = _incident(tmp_path)
    for to, ev in (("investigating", "e"), ("mitigation_proposed", "e"),
                   ("repair_in_progress", "e"), ("recovery_observed", "e")):
        st.lifecycle_transition(iid, to, "director", ev)
    st.mark_recovery_evidence(iid, "root-cause-fixed", "e")
    st.lifecycle_transition(iid, "recovery_verified", "director", "e")
    st.lifecycle_transition(iid, "closed", "director", "e")
    st.lifecycle_transition(iid, "detected", "director",
                            "recurrence: junk flood returned")
    assert st.lifecycle(iid)["lifecycle_state"] == "detected"


# ------------------------------------------------ §5 manufacturer feed

def test_manufacturer_feed_unavailable_never_fabricates(tmp_path):
    mt = ManufacturerTelemetry(telemetry_path=tmp_path / "nope.jsonl")
    records, markers = mt.read()
    assert records == []
    assert markers and markers[0].kind == "feed_missing"
    assert "handoff proposed" in markers[0].detail


def test_manufacturer_repeated_failures_raise_investigation(tmp_path):
    p = tmp_path / "mfr.jsonl"
    rows = [
        {"manufacturer": "acme", "method": "mcp", "endpoint": "v2",
         "endpoint_version": "2.1", "ts": "2026-10-16T10:00:00Z",
         "outcome": "rate-limited", "documents_delivered": 0,
         "documents_failed": 10},
        {"manufacturer": "acme", "method": "mcp", "endpoint": "v2",
         "endpoint_version": "2.1", "ts": "2026-10-16T11:00:00Z",
         "outcome": "auth", "documents_delivered": 0,
         "documents_failed": 10},
        {"manufacturer": "acme", "method": "mcp", "endpoint": "v2",
         "endpoint_version": "2.2", "ts": "2026-10-16T12:00:00Z",
         "outcome": "http_500", "documents_delivered": 0,
         "documents_failed": 10},
    ]
    p.write_text("\n".join(json.dumps(r) for r in rows))
    mt = ManufacturerTelemetry(telemetry_path=p)
    records, _ = mt.read()
    h = mt.assess(records)
    evs = mt.events(records, h)
    assert evs and evs[0].kind == "mfr_endpoint_failure"
    assert "do not assume" in h.notes[0] or "verify" in h.notes[0], \
        "endpoint change is a hypothesis until verified"
    assert any("2.2" in n or "version" in n for n in h.notes)


# ------------------------------------------------ §6 funnel admission

class _M:
    def __init__(self, **kw):
        self.acquisition_files = 66994
        self.rust_queued = kw.get("queued", 0)
        self.substrate_pending = kw.get("pending", 0)
        self.rust_claimed = 0
        self.ledger_done_total = 5200
        self.ready_by_class = kw.get("by_class", {"datasheet": 1060})


def test_backlog_cannot_enter_queue_is_admission_gap():
    f = build_funnel(_M(queued=0, pending=0))
    assert f.holdup == "admission-gap"
    assert any("eligible rescan docs" in e for e in f.holdup_evidence)
    assert f.prioritized == "unknown" and any(
        "factory_targets" in n for n in f.notes)


def test_queues_unavailable_not_confused_with_gap():
    m = _M()
    m.rust_queued = "unknown"
    m.substrate_pending = "unknown"
    f = build_funnel(m)
    assert f.holdup == "queues-unavailable"


# ------------------------------------------------ isolation + restart

def test_two_projects_isolated_tasks_and_authority(tmp_path):
    ts1 = TaskStore(_db(tmp_path, "p1.db"), project="alpha")
    ts2 = TaskStore(_db(tmp_path, "p2.db"), project="beta")
    ts1.create("T-X", authority="alpha-director", evidence="e")
    ts2.create("T-X", authority="beta-director", evidence="e")
    ts1.transition("T-X", "offered", actor="alpha-director", evidence="e",
                   owner="agent-a")
    assert ts1.get("T-X").state == "offered"
    assert ts2.get("T-X").state == "unassigned", \
        "projects never share task state"


def test_restart_and_replay_no_duplicate_messages(tmp_path):
    import os
    from director_consumer.authorization import AuthRecord, AuthorizationStore
    from director_consumer.schema import fp
    con = _db(tmp_path)
    apdir = tmp_path / "approvals"
    apdir.mkdir(exist_ok=True)
    body = "body"
    f = apdir / "A-RR.json"
    f.write_text(json.dumps({
        "project": "default", "action": "deliver",
        "recipient": "m5-opencode", "task_ref": "T-RR",
        "expires_at": time.time() + 3600, "issuer": "sam",
        "payload_digest": fp("msg-body", body)}))
    os.chmod(f, 0o640)
    ast = AuthorizationStore(con, approvals_dir=apdir,
                             operator_uid=os.getuid(),
                             service_uid=os.getuid() + 1)
    ast.issue(AuthRecord(auth_id="A-RR", project="default",
                         action="deliver", recipient="m5-opencode",
                         task_ref="T-RR",
                         expires_at=time.time() + 3600, issuer="sam",
                         payload_digest=fp("msg-body", body)))
    pol = DeliveryPolicy(outbox_root=tmp_path / "boxes")
    mb = MailboxDelivery(con, pol, authorizations=ast)
    mid = mb.propose("m5-opencode", "question", "q", body,
                     correlation_id="T-RR")
    con.close()
    # "restart": new connection, same db
    con2 = _db(tmp_path)
    ast2 = AuthorizationStore(con2, approvals_dir=apdir,
                              operator_uid=os.getuid(),
                              service_uid=os.getuid() + 1)
    mb2 = MailboxDelivery(con2, pol, authorizations=ast2)
    mid2 = mb2.propose("m5-opencode", "question", "q", body,
                       correlation_id="T-RR")  # same id
    assert mid == mid2
    mb2.deliver(mid2, auth_id="A-RR")
    assert len(list((tmp_path / "boxes").rglob("*.md"))) == 1
    assert con2.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 1


def test_model_policy_blocks_child_paths():
    from director_consumer.policy import ModelPolicy
    pol = ModelPolicy.load(ROOT / "projects/designwins/policy.yaml")
    ok, why = pol.check("openrouter", "child-agent-reasoning")
    assert not ok and "METERED REFUSED" in why
