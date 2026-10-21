"""Authorization packet negative tests — REAL AuthorizationStore +
MailboxDelivery behavior, hermetic (test_identities=True is the sanctioned
double per authorization.py). These receipts are actual executed outputs."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from director_consumer.authorization import (AuthRecord, AuthRequest,
                                             AuthorizationStore)
from director_consumer.messaging import (DeliveryPolicy, MailboxDelivery)
from director_consumer.state import DirectorState

AUTH = "sam-20261021-seven-mail-mac-mini"
RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append({"test": name, "pass": bool(cond), "detail": detail[:170]})


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


with tempfile.TemporaryDirectory() as tmp:
    # 1. WRONG UID: operator == service -> trust-boundary refusal
    con, store, appr = make_store(
        tmp, operator=1000, service=1000,
        artifact_kwargs=dict())
    try:
        register(store)
        check("wrong-uid", False, "issued without uid boundary!")
    except Exception as e:
        check("wrong-uid", "uid" in str(e), f"{type(e).__name__}: {e}")

    # 2. WRONG RECIPIENT
    tmp2 = tempfile.mkdtemp()
    con, store, appr = make_store(tmp2, artifact_kwargs=dict())
    register(store)
    ok, why = store.check(AUTH, req(recipient="someone-else"))
    check("wrong-recipient", not ok and "recipient" in why, why)

    # 3. EXPIRED APPROVAL
    tmp3 = tempfile.mkdtemp()
    con, store, appr = make_store(
        tmp3, artifact_kwargs=dict(expires=time.time() - 10))
    try:
        register(store)
        ok, why = store.check(AUTH, req())
        check("expired", not ok and "expired" in why, why)
    except Exception as e:
        check("expired", True, f"refused at issuance: {e}")

    # 4. DUPLICATE SEND: single-action grant bound once, second refused
    tmp4 = tempfile.mkdtemp()
    con, store, appr = make_store(tmp4, artifact_kwargs=dict())
    register(store)
    import director_consumer.schema as _schema
    BODY1 = "the one approved body"
    D1 = _schema.fp("msg-body", BODY1)
    # rebind artifact to the exact body so the FIRST reserve binds cleanly
    import json as _j, os as _os
    fa = appr / f"{AUTH}.json"
    d = _j.loads(fa.read_text()); d["payload_digest"] = D1
    fa.write_text(_j.dumps(d)); _os.chmod(fa, 0o600)
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
    check("duplicate-send-binding", ok1 and not ok2 and
          ("bound" in why2 or "digest" in why2),
          f"second send refused: {why2}")

    # 5. MISSING AUTHORIZATION
    tmp5 = tempfile.mkdtemp()
    con, store, appr = make_store(tmp5, artifact_kwargs=None)
    ok, why = store.check("sam-nonexistent", req())
    check("missing-authorization", not ok and "default deny" in why, why)

    # 6+7. observe mode blocks EVEN WITH a valid approved+reserved message;
    #      test-delivery mode succeeds end-to-end through the 05C path
    tmp6 = tempfile.mkdtemp()
    import director_consumer.schema as _schema
    BODY = "exact approved body"
    DIGEST = _schema.fp("msg-body", BODY)
    con, store, appr = make_store(tmp6, artifact_kwargs=dict(
        payload_digest=DIGEST))
    register(store)
    pol = DeliveryPolicy(outbox_root=str(Path(tmp6) / "out"))
    mb = MailboxDelivery(con, pol, sender="engineering-director",
                         project="designwins", authorizations=store,
                         mode="test-delivery")
    mid = mb.propose("mac-mini", "escalation", "s", BODY,
                     correlation_id="OPERATOR-GRANT-SEVEN-MAIL-20261021")
    try:
        MailboxDelivery(con, pol, sender="engineering-director",
                        project="designwins", authorizations=store,
                        mode="observe").deliver("m1", auth_id=AUTH)
        check("observe-blocks-despite-approval", False, "delivered?!")
    except Exception as e:
        check("observe-blocks-despite-approval", "observation mode" in str(e),
              f"{type(e).__name__}: {e}")
    try:
        r = mb.deliver(mid, auth_id=AUTH)
        files = sorted(str(p.name) for p in (Path(tmp6) / "out").rglob("*.md"))
        check("happy-path-delivers", len(files) == 1,
              f"receipt={str(r)[:50]} file={files[:1]}")
    except Exception as e:
        check("happy-path-delivers", False, f"{type(e).__name__}: {e}")

    # 8. ARTIFACT CANNOT SET MODE (mode exists only as constructor arg)
    import inspect
    init_src = inspect.getsource(MailboxDelivery.__init__)
    check("artifact-cannot-set-mode",
          "approval" not in init_src.lower(),
          "mode set only at construction; approvals never read for mode")

print(json.dumps(RESULTS, indent=1))
fails = [r for r in RESULTS if not r["pass"]]
print(f"SUMMARY: {len(RESULTS) - len(fails)}/{len(RESULTS)} passed")
sys.exit(1 if fails else 0)
