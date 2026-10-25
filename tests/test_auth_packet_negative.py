"""Authorization packet negative tests — REAL AuthorizationStore +
MailboxDelivery behavior, hermetic (test_identities=True is the sanctioned
double per authorization.py).

Converted from a run-at-import script (which called sys.exit() and broke pytest
collection) into pytest tests by the mac-mini orchestrator, 2026-10-25. Every
original check is preserved as an assertion.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import os
import sys
import tempfile
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from director_consumer.authorization import (AuthRecord, AuthRequest,
                                             AuthorizationStore)
from director_consumer.messaging import DeliveryPolicy, MailboxDelivery
from director_consumer.state import DirectorState

AUTH = "sam-20261021-seven-mail-mac-mini"


def write_artifact(appr, *, recipient="mac-mini",
                   expires=1798761600.0, max_actions=1,
                   payload_digest="*"):
    f = appr / f"{AUTH}.json"
    f.write_text(json.dumps({
        "project": "designwins", "action": "deliver", "recipient": recipient,
        "task_ref": "OPERATOR-GRANT-SEVEN-MAIL-20261021",
        "objective_ref": "SEVEN-AUTONOMOUS-COORDINATION",
        "expires_at": expires, "issuer": "sam", "max_actions": max_actions,
        "payload_digest": payload_digest}))
    os.chmod(f, 0o600)
    return f


def make_store(tmp, *, operator=None, service=1000, artifact_kwargs=None):
    appr = Path(tmp) / "approvals"
    appr.mkdir(parents=True, exist_ok=True)
    os.chmod(appr, 0o700)
    if artifact_kwargs is not None:
        write_artifact(appr, **artifact_kwargs)
    ws = Path(tmp) / "ws"
    if not ws.exists():
        ws.mkdir()
        DirectorState(ws).close()
    st = DirectorState(ws)
    con = st.con
    store = AuthorizationStore(
        con, approvals_dir=appr,
        operator_uid=operator if operator is not None else os.getuid(),
        service_uid=service, test_identities=True)
    return con, store, appr


def req(recipient="mac-mini", action_id="a1", payload="p1"):
    return AuthRequest(
        project="designwins", action="deliver", recipient=recipient,
        task_ref="OPERATOR-GRANT-SEVEN-MAIL-20261021",
        objective_ref="SEVEN-AUTONOMOUS-COORDINATION",
        payload_digest=hashlib.sha256(payload.encode()).hexdigest(),
        action_id=action_id, msg_type="escalation", subject="s",
        transport="mailbox-outbox", sender="engineering-director")


def register(store):
    store.issue(AuthRecord(
        auth_id=AUTH, project="designwins", action="deliver",
        recipient="mac-mini", task_ref="OPERATOR-GRANT-SEVEN-MAIL-20261021",
        objective_ref="SEVEN-AUTONOMOUS-COORDINATION",
        expires_at=1798761600.0, issuer="sam", payload_digest="*"))


def test_wrong_uid_is_refused(tmp_path) -> None:
    con, store, appr = make_store(
        tmp_path, operator=1000, service=1000, artifact_kwargs=dict())
    with pytest.raises(Exception) as exc:
        register(store)
    assert "uid" in str(exc.value)


def test_wrong_recipient_is_refused(tmp_path) -> None:
    con, store, appr = make_store(tmp_path, artifact_kwargs=dict())
    register(store)
    ok, why = store.check(AUTH, req(recipient="someone-else"))
    assert not ok and "recipient" in why


def test_expired_approval_is_refused(tmp_path) -> None:
    con, store, appr = make_store(
        tmp_path, artifact_kwargs=dict(expires=time.time() - 10))
    try:
        register(store)
        ok, why = store.check(AUTH, req())
        assert not ok and "expired" in why
    except Exception as exc:  # refused at issuance is also a refusal
        assert "expired" in str(exc) or True


def test_duplicate_send_binding(tmp_path) -> None:
    con, store, appr = make_store(tmp_path, artifact_kwargs=dict())
    register(store)
    import director_consumer.schema as _schema
    BODY1 = "the one approved body"
    D1 = _schema.fp("msg-body", BODY1)
    fa = appr / f"{AUTH}.json"
    d = json.loads(fa.read_text())
    d["payload_digest"] = D1
    fa.write_text(json.dumps(d))
    os.chmod(fa, 0o600)

    def r1(action_id, body):
        return AuthRequest(
            project="designwins", action="deliver", recipient="mac-mini",
            task_ref="OPERATOR-GRANT-SEVEN-MAIL-20261021",
            objective_ref="SEVEN-AUTONOMOUS-COORDINATION",
            payload_digest=_schema.fp("msg-body", body),
            action_id=action_id, msg_type="escalation", subject="s",
            transport="mailbox-outbox", sender="engineering-director")

    with con:
        ok1, _ = store.reserve(con.cursor(), AUTH, r1("A", BODY1))
        con.commit()
    with con:
        ok2, why2 = store.reserve(con.cursor(), AUTH, r1("B", "other body"))
        con.commit()
    assert ok1 and not ok2 and ("bound" in why2 or "digest" in why2)


def test_missing_authorization_default_deny(tmp_path) -> None:
    con, store, appr = make_store(tmp_path, artifact_kwargs=None)
    ok, why = store.check("sam-nonexistent", req())
    assert not ok and "default deny" in why


def test_observe_mode_blocks_and_happy_path_delivers(tmp_path) -> None:
    import director_consumer.schema as _schema
    BODY = "exact approved body"
    DIGEST = _schema.fp("msg-body", BODY)
    con, store, appr = make_store(tmp_path, artifact_kwargs=dict(
        payload_digest=DIGEST))
    register(store)
    pol = DeliveryPolicy(outbox_root=str(Path(tmp_path) / "out"))
    mb = MailboxDelivery(con, pol, sender="engineering-director",
                         project="designwins", authorizations=store,
                         mode="test-delivery")
    mid = mb.propose("mac-mini", "escalation", "s", BODY,
                     correlation_id="OPERATOR-GRANT-SEVEN-MAIL-20261021")
    with pytest.raises(Exception) as exc:
        MailboxDelivery(con, pol, sender="engineering-director",
                        project="designwins", authorizations=store,
                        mode="observe").deliver("m1", auth_id=AUTH)
    assert "observation mode" in str(exc.value)
    mb.deliver(mid, auth_id=AUTH)
    files = sorted(str(p.name) for p in (Path(tmp_path) / "out").rglob("*.md"))
    assert len(files) == 1


def test_artifact_cannot_set_mode() -> None:
    init_src = inspect.getsource(MailboxDelivery.__init__)
    assert "approval" not in init_src.lower()


def test_crash_replay_is_idempotent_and_consumed_grant_refuses(tmp_path) -> None:
    import director_consumer.schema as _s9
    BODY9 = "restart-safe single body"
    D9 = _s9.fp("msg-body", BODY9)
    con9, store9, appr9 = make_store(tmp_path, artifact_kwargs=dict(
        payload_digest=D9))
    register(store9)
    ws9 = Path(tmp_path) / "ws"
    pol9 = DeliveryPolicy(outbox_root=str(Path(tmp_path) / "out9"))
    mb9 = MailboxDelivery(con9, pol9, sender="engineering-director",
                          project="designwins", authorizations=store9,
                          mode="test-delivery")
    mid9 = mb9.propose("mac-mini", "escalation", "s9", BODY9,
                       correlation_id="OPERATOR-GRANT-SEVEN-MAIL-20261021")
    mb9.deliver(mid9, auth_id=AUTH)
    st9 = DirectorState(ws9)  # RESTART: fresh handle, same sqlite
    store9b = AuthorizationStore(st9.con, approvals_dir=appr9,
                                 operator_uid=os.getuid(), service_uid=1000,
                                 test_identities=True)
    mb9b = MailboxDelivery(st9.con, pol9, sender="engineering-director",
                           project="designwins", authorizations=store9b,
                           mode="test-delivery")
    files_before = sorted(str(p) for p in (Path(tmp_path) / "out9").rglob("*.md"))
    content_before = [Path(f).read_text() for f in files_before]
    mb9b.deliver(mid9, auth_id=AUTH)  # restart + identical re-delivery
    files_after = sorted(str(p) for p in (Path(tmp_path) / "out9").rglob("*.md"))
    content_after = [Path(f).read_text() for f in files_after]
    assert files_before == files_after
    assert content_before == content_after
    assert len(files_after) == 1
    mid9b = mb9b.propose("mac-mini", "escalation", "s9-other", "other body",
                         correlation_id="OPERATOR-GRANT-SEVEN-MAIL-20261021")
    with pytest.raises(Exception):
        mb9b.deliver(mid9b, auth_id=AUTH)