"""Shared acceptance validator (05C closure §3) — ONE contract for every
path that can mark a message acknowledged: record_ack(), reconcile_mailbox()
and reconcile_with_receiver() all route through `validate_acceptance`.

Invariant B: only the correct authenticated agent/session may accept its
assignment. A bare message id, a From: header, a caller verification
boolean, or a copied file UID is never acceptance.
"""
from __future__ import annotations

import json
from typing import Optional

from .schema import fp


def validate_acceptance(persisted_message: dict, envelope: dict,
                        established_origin: Optional[str],
                        session_bindings: Optional[dict],
                        state: str) -> tuple[bool, list[str]]:
    """Validate an acceptance receipt against THIS MESSAGE'S persisted
    assignment. Returns (accepted, problems)."""
    problems = []
    if not envelope:
        return False, ["missing ack envelope"]
    if state not in ("sent", "delivering", "acked"):
        problems.append(f"message state {state!r} does not permit acceptance")
    # envelope completeness
    for fld in ("ack_of", "project", "task_ref", "epoch", "session_id",
                "message_digest"):
        if envelope.get(fld) in (None, ""):
            problems.append(f"envelope missing {fld}")
    # identity: THIS message only
    if envelope.get("ack_of") not in (None, "") and \
            envelope.get("ack_of") != persisted_message.get("message_id"):
        problems.append("ack_of does not match this message")
    if envelope.get("project") not in (None, "") and \
            envelope.get("project") != persisted_message.get("project"):
        problems.append("envelope project mismatch")
    if envelope.get("task_ref") not in (None, "") and \
            envelope.get("task_ref") != persisted_message.get("task_ref"):
        problems.append("envelope task mismatch")
    body = persisted_message.get("body", "")
    if envelope.get("message_digest") and \
            envelope["message_digest"] != fp("msg-body", body):
        problems.append("message digest mismatch")
    # registered binding for project/task/epoch -> session
    if not session_bindings:
        problems.append("missing session bindings (fail closed)")
    else:
        want = session_bindings.get(
            (envelope.get("project"), envelope.get("task_ref"),
             envelope.get("epoch")))
        if want is None:
            problems.append(
                "no registered session binding for project/task/epoch")
        elif envelope.get("session_id") not in (None, "") and \
                envelope["session_id"] != want:
            problems.append(
                "claimed session is not the registered binding for THIS "
                "task/epoch")
    # origin must be ESTABLISHED by the trusted transport/adapter
    if not established_origin:
        problems.append("transport cannot establish session origin")
    elif session_bindings:
        want = session_bindings.get(
            (envelope.get("project"), envelope.get("task_ref"),
             envelope.get("epoch")))
        if want is not None and established_origin != want:
            problems.append(
                f"origin session {established_origin} is not the "
                f"registered binding {want}")
    return (not problems), problems


def scoped_receipt(action_id: str, project: str, task_ref: str, epoch: int,
                   session_id: str, body_digest: str) -> dict:
    """The only receipt shape receiver reconciliation accepts."""
    return {"ack_of": action_id, "project": project, "task_ref": task_ref,
            "epoch": epoch, "session_id": session_id,
            "message_digest": body_digest, "action_id": action_id}
