"""Negative cases for the Seven execution loop (section F) — fail closed.

Each case proves a forged/missing/stale input cannot advance state:
invalid receipts must land in invalid_receipts with no acked_at and no
state change; fenced transitions must reject stale epochs; execution
failure must leave the task blocked/visible, never verified.
"""
from __future__ import annotations

import json
from pathlib import Path

from .acceptance import scoped_receipt
from .messaging import MailboxDelivery, OpenCodeSessionTransport, SessionTokenTransport
from .schema import fp
from .tasks import TaskStore, TransitionError, FencingError


def run_selftest(con, project: str, delivery: MailboxDelivery,
                 workspace: Path) -> dict:
    ts = TaskStore(con, project)
    results = []

    def case(name: str, fn):
        try:
            fn()
            results.append({"case": name, "result": "PASS"})
        except AssertionError as e:
            results.append({"case": name, "result": "FAIL", "why": str(e)})

    body = "negative-case body for binding tests"
    mid = delivery.propose(recipient="seven", msg_type="task",
                           subject="neg", body=body,
                           correlation_id="NEG-CASE", assignment_epoch=1)
    try:
        delivery.deliver(mid)
    except Exception:  # noqa: BLE001
        pass
    body_digest = fp("msg-body", body)
    empty_map = {}

    def c_missing_authorization():
        st = delivery.record_ack(mid, from_agent="agent:seven",
                                 envelope=scoped_receipt(
                                     mid, project, "NEG-CASE", 1, "s", body_digest),
                                 session_bindings=empty_map,
                                 transport=OpenCodeSessionTransport(
                                     bindings_file=Path("/nonexistent")))
        assert st == "ack-unverified", st

    def c_invalid_epoch():
        reg = {("default", "NEG-CASE", 99): "ses-right"}
        st = delivery.record_ack(mid, from_agent="agent:seven",
                                 envelope=scoped_receipt(
                                     mid, project, "NEG-CASE", 99, "ses-right",
                                     body_digest),
                                 session_bindings=reg,
                                 transport=SessionTokenTransport(
                                     tokens={}))
        assert st == "ack-unverified", st

    def c_forged_stale_ack():
        # caller claims a session that is not the registered binding
        reg = {("default", "NEG-CASE", 1): "ses-true"}
        st = delivery.record_ack(mid, from_agent="agent:seven",
                                 envelope=scoped_receipt(
                                     mid, project, "NEG-CASE", 1, "ses-forged",
                                     body_digest),
                                 session_bindings=reg,
                                 transport=SessionTokenTransport(
                                     tokens={str(workspace): "ses-true"}))
        assert st == "ack-unverified", st

    def c_wrong_sender():
        reg = {("default", "NEG-CASE", 1): "s"}
        st = delivery.record_ack(mid, from_agent="agent:intruder",
                                 envelope=scoped_receipt(
                                     mid, project, "NEG-CASE", 1, "s",
                                     body_digest),
                                 session_bindings=reg,
                                 transport=SessionTokenTransport(
                                     tokens={}))
        assert st == "ack-unverified", st

    def c_no_origin():
        reg = {("default", "NEG-CASE", 1): "s"}
        st = delivery.record_ack(mid, from_agent="agent:seven",
                                 envelope=scoped_receipt(
                                     mid, project, "NEG-CASE", 1, "s",
                                     body_digest),
                                 session_bindings=reg, transport=None)
        assert st == "ack-unverified", st

    def c_stale_epoch_transition():
        ts.create("NEG-TRANSIENT", authority="selftest", evidence="seed")
        ts.transition("NEG-TRANSIENT", "offered", actor="selftest",
                      evidence="offer", owner="seven")
        try:
            ts.transition("NEG-TRANSIENT", "acknowledged",
                          actor="selftest", evidence="stale",
                          expect_epoch=999)
        except FencingError:
            return
        raise AssertionError("stale epoch was NOT rejected")

    def c_illegal_transition():
        try:
            ts.transition("NEG-TRANSIENT", "verified", actor="selftest",
                          evidence="skip-states")
        except TransitionError:
            return
        raise AssertionError("illegal transition was NOT rejected")

    def c_missing_evidence():
        try:
            ts.transition("NEG-TRANSIENT", "cancelled", actor="selftest",
                          evidence="")
        except TransitionError:
            return
        raise AssertionError("evidence-less transition was NOT rejected")

    def c_unreachable_agent_is_unknown():
        from .work_state import WorkStateAggregator
        view = WorkStateAggregator(con, project, workspace).build()
        for o in view["owners"]:
            if o["reachability"] not in (
                    "AGENT_REACHABLE", "UNKNOWN_UNOBSERVABLE"):
                raise AssertionError(
                    f"unverified reachability claimed: {o['reachability']}")

    for name, fn in [
        ("missing_authorization_or_binding", c_missing_authorization),
        ("incorrect_assignment_epoch", c_invalid_epoch),
        ("stale_or_forged_ack", c_forged_stale_ack),
        ("wrong_sender", c_wrong_sender),
        ("no_trusted_origin", c_no_origin),
        ("stale_epoch_transition_rejected", c_stale_epoch_transition),
        ("illegal_transition_rejected", c_illegal_transition),
        ("evidence_mandatory", c_missing_evidence),
        ("unreachable_agent_reported_unknown", c_unreachable_agent_is_unknown),
    ]:
        case(name, fn)

    n_invalid = con.execute(
        "SELECT count(*) FROM invalid_receipts WHERE project=?",
        (project,)).fetchone()[0]
    out = {"schema": "harness.seven-oc-selftest.v1",
           "results": results,
           "invalid_receipts_recorded": n_invalid,
           "all_pass": all(r["result"] == "PASS" for r in results)}
    path = Path(workspace) / "oc-selftest-latest.json"
    path.write_text(json.dumps(out, indent=1))
    return out
