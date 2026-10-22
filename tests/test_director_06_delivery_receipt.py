"""06 delivery receipt: every eligible candidate gets a typed, recorded
outcome — `delivered == 0` can never masquerade as 'nothing refused'.

Answers the 2026-10-19 review ask on issue #2: a source-stamped receipt
separating (a) eligible proposals, (b) no-recipient skips, (c) approval
refusals, (d) transport failures, (e) sent message ids — with TaskStore
owner + epoch binding (never body text, never the constant 1).
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from director_consumer.authorization import AuthorizationStore
from director_consumer.consumer import DirectorConsumer, FeedConfig
from director_consumer.schema import fp

DEPS = Path(__file__).resolve().parents[1] / "director_consumer" / "deps.yaml"

POLICY = """\
schema: 1
version: "test"
authority:
  human_owner: sam
delivery:
  enabled: true
  authorization_ref: {ref}
  approvals_dir: "{approvals}"
"""


def build_consumer(tmp_path: Path, ref: str = "workloop-pilot"):
    proj = tmp_path / "designwins"
    proj.mkdir(exist_ok=True)
    apdir = tmp_path / "approvals"
    apdir.mkdir(exist_ok=True)
    box = tmp_path / "mailbox"
    box.mkdir(exist_ok=True)
    (proj / "policy.yaml").write_text(
        POLICY.format(ref=f'"{ref}"' if ref else '""', approvals=apdir))
    consumer = DirectorConsumer(
        workspace=tmp_path / "ws", deps_map=DEPS, feeds={},
        project_dir=proj, mailbox_root=box)
    consumer.mail.policy.outbox_root = box
    # test double identities: operator uid = file owner (real uid),
    # service uid differs -> the trust boundary holds (05C pattern)
    consumer.authz = AuthorizationStore(
        consumer.state.con, approvals_dir=apdir,
        operator_uid=os.getuid(), service_uid=os.getuid() + 1,
        test_identities=True)
    return consumer, apdir, box


def write_artifact(apdir: Path, *, auth_id="workloop-pilot",
                   project="designwins", recipient="m5-agent-x",
                   task_ref="T-DEL-1", body="task body"):
    # the operator artifact binds the exact message payload (05C law):
    # payload_digest is the fp() of the body the delivery will carry.
    art = {
        "auth_id": auth_id, "project": project, "action": "deliver",
        "recipient": recipient, "task_ref": task_ref,
        "expires_at": time.time() + 3600.0, "issuer": "sam",
        "payload_digest": fp("msg-body", body), "spend_scope": "none",
        "max_spend_usd": 0.0,
    }
    (apdir / f"{auth_id}.json").write_text(json.dumps(art))
    return art


def add_proposal(consumer, tk: str, *, body="task body",
                 authority="sam", written=1):
    consumer.state.con.execute(
        "INSERT OR REPLACE INTO proposals(trigger_key, project, ptype, "
        "filename, authority, created_at, body, written, written_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (tk, consumer.project_name, "task", f"{tk}.md", authority,
         time.time(), body, written, time.time()))
    consumer.state.con.commit()


def add_task(consumer, tk: str, owner="m5-agent-x", epoch=1):
    consumer.tasks.create(tk, authority="sam", evidence="{}")
    if owner:
        consumer.tasks.transition(tk, "offered", actor="director",
                                  evidence="test", owner=owner)
    consumer.state.con.execute(
        "UPDATE tasks SET epoch=? WHERE task_id=? AND project=?",
        (epoch, tk, consumer.project_name))
    consumer.state.con.commit()


def test_no_policy_ref_fails_closed_with_reason(tmp_path):
    consumer, apdir, _ = build_consumer(tmp_path, ref="")
    add_proposal(consumer, "T-DEL-1")
    add_task(consumer, "T-DEL-1")
    outcomes = consumer._deliver_authorized()
    assert [o["outcome"] for o in outcomes] == ["approval_refused"]
    assert "fail closed" in outcomes[0]["reason"]
    assert outcomes[0]["trigger_key"] == "T-DEL-1"


def test_missing_artifact_is_approval_refused_not_silent(tmp_path):
    consumer, apdir, _ = build_consumer(tmp_path)
    add_proposal(consumer, "T-DEL-1")
    add_task(consumer, "T-DEL-1")
    outcomes = consumer._deliver_authorized()
    assert outcomes[0]["outcome"] == "approval_refused"
    assert "issuance refused" in outcomes[0]["reason"]


def test_transport_failure_recorded_not_swallowed(tmp_path):
    consumer, apdir, _ = build_consumer(tmp_path)
    write_artifact(apdir)
    add_proposal(consumer, "T-DEL-1")
    add_task(consumer, "T-DEL-1")
    # break the transport path: outbox root that cannot exist as parent
    consumer.mail.policy.require_existing_root = True
    consumer.mail.policy.outbox_root = tmp_path / "no-such" / "inbox"
    outcomes = consumer._deliver_authorized()
    assert outcomes[0]["outcome"] == "transport_refused"
    assert outcomes[0]["reason"], "refusal must carry a reason"


def test_no_task_and_no_owner_are_explicit_skips(tmp_path):
    consumer, apdir, _ = build_consumer(tmp_path)
    write_artifact(apdir)
    add_proposal(consumer, "T-NO-TASK")
    add_proposal(consumer, "T-NO-OWNER")
    add_task(consumer, "T-NO-OWNER", owner=None)
    outcomes = {o["trigger_key"]: o for o in consumer._deliver_authorized()}
    assert outcomes["T-NO-TASK"]["outcome"] == "skipped_no_task"
    assert outcomes["T-NO-OWNER"]["outcome"] == "skipped_no_owner"


def test_sent_path_binds_owner_epoch_and_message_id(tmp_path):
    consumer, apdir, box = build_consumer(tmp_path)
    write_artifact(apdir)
    add_proposal(consumer, "T-DEL-1")
    add_task(consumer, "T-DEL-1", epoch=7)   # F3: epoch != 1 proof
    outcomes = consumer._deliver_authorized()
    assert outcomes[0]["outcome"] == "sent", outcomes[0]
    assert outcomes[0]["epoch"] == 7
    assert outcomes[0]["recipient"] == "m5-agent-x"
    mid = outcomes[0]["message_id"]
    state = consumer.state.con.execute(
        "SELECT state FROM messages WHERE message_id=?", (mid,)
    ).fetchone()[0]
    assert state == "sent"
    assert list(box.glob("*/*.md")), "artifact must exist in the outbox"
    # identity-stable rerun: same message id, no duplicate
    outcomes2 = consumer._deliver_authorized()
    assert outcomes2[0]["message_id"] == mid


def test_receipt_separates_mixed_outcomes(tmp_path):
    consumer, apdir, box = build_consumer(tmp_path)
    write_artifact(apdir, task_ref="T-DEL-1", body="body one")
    add_proposal(consumer, "T-DEL-1", body="body one")
    add_task(consumer, "T-DEL-1")
    add_proposal(consumer, "T-DONE", body="delivered: true\nold")
    add_task(consumer, "T-DONE")
    add_proposal(consumer, "T-OTHER-TASK")   # outside the artifact's scope
    add_task(consumer, "T-OTHER-TASK")
    outcomes = {o["trigger_key"]: o
                for o in consumer._deliver_authorized()}
    assert outcomes["T-DEL-1"]["outcome"] == "sent"
    assert outcomes["T-DONE"]["outcome"] == "skipped_already_delivered"
    # the out-of-scope candidate: artifact-level issue passes (project/
    # action/recipient match) but the task-scope check at deliver time
    # refuses — typed as transport_refused with the reason kept, never
    # collapsed into the sent/skip counts.
    assert outcomes["T-OTHER-TASK"]["outcome"] == "transport_refused"
    assert outcomes["T-OTHER-TASK"]["reason"]
