"""Execution loop: owner gate, delivery refusal, ack observation, verifier
honesty. No fabricated success — refused gates stop the loop."""
import json
import sqlite3
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest
from director_consumer.execution_loop import (
    ExecutionLoop, OwnerRegistry, Verifier)
from director_consumer.tasks import TaskStore


def _ts(tmp):
    return TaskStore(sqlite3.connect(tmp / "e.db"), project="designwins")


OWNERS = """
agents:
  m5:
    agent_id: m5-agent-x
    source: "roster test"
    ack_channel: "test"
"""


def test_unconfirmed_lane_owner_stops_the_loop(tmp_path):
    ts = _ts(tmp_path)
    ts.create("T-1", authority="sam", evidence="{}")
    owners = OwnerRegistry(tmp_path / "owners.yaml")
    (tmp_path / "owners.yaml").write_text(OWNERS)
    owners = OwnerRegistry(tmp_path / "owners.yaml")
    ver = Verifier(tmp_path)
    lp = ExecutionLoop(ts, owners, ver)
    out = lp.run("T-1", lane="nobody-confirmed", deliver=lambda *a: (True, "x"))
    steps = {s["step"]: s for s in out["steps"]}
    assert steps["resolve-owner"]["ok"] is False
    assert "no confirmed owner" in steps["resolve-owner"]["detail"]
    assert "deliver" not in steps, "delivery must NOT run for unconfirmed owner"


def test_delivery_gate_refusal_stops_and_records(tmp_path):
    ts = _ts(tmp_path)
    ts.create("T-2", authority="sam", evidence="{}")
    (tmp_path / "owners.yaml").write_text(OWNERS)
    lp = ExecutionLoop(ts, OwnerRegistry(tmp_path / "owners.yaml"),
                       Verifier(tmp_path))
    out = lp.run("T-2", lane="m5",
                 deliver=lambda *a: (False,
                                     "REFUSED: no approval artifact"))
    steps = {s["step"]: s for s in out["steps"]}
    assert steps["deliver"]["ok"] is False
    assert "REFUSED" in steps["deliver"]["detail"]
    assert "verify" not in steps


def test_ack_observed_only_from_real_artifact(tmp_path):
    ts = _ts(tmp_path)
    ts.create("T-3", authority="sam", evidence="{}")
    ts.transition("T-3", "offered", actor="d", evidence="x", owner="m5-agent-x")
    (tmp_path / "owners.yaml").write_text(OWNERS)
    box = tmp_path / "mail"
    box.mkdir()
    (box / "ack-T-3.md").write_text("ack T-3 epoch 1 from m5-agent-x\n")
    lp = ExecutionLoop(ts, OwnerRegistry(tmp_path / "owners.yaml"),
                       Verifier(tmp_path))
    out = lp.run("T-3", lane="m5", deliver=None,
                 ack_spec={"verify_how": "mailbox_ack", "mailbox": str(box)})
    steps = {s["step"]: s for s in out["steps"]}
    assert steps["observe-ack"]["ok"] is True
    assert "ack artifact observed" in steps["observe-ack"]["detail"]
    assert ts.get("T-3").state == "acknowledged"


def test_missing_ack_is_not_an_ack(tmp_path):
    ts = _ts(tmp_path)
    ts.create("T-4", authority="sam", evidence="{}")
    ts.transition("T-4", "offered", actor="d", evidence="x", owner="m5-agent-x")
    (tmp_path / "owners.yaml").write_text(OWNERS)
    box = tmp_path / "mail"; box.mkdir()
    lp = ExecutionLoop(ts, OwnerRegistry(tmp_path / "owners.yaml"),
                       Verifier(tmp_path))
    out = lp.run("T-4", lane="m5",
                 ack_spec={"verify_how": "mailbox_ack", "mailbox": str(box),
                           "pattern": "T-4"})
    steps = {s["step"]: s for s in out["steps"]}
    assert steps["observe-ack"]["ok"] is False
    assert ts.get("T-4").state != "acknowledged"


def test_verifier_rejects_unknown_method_not_fake_pass(tmp_path):
    ver = Verifier(tmp_path)
    ok, detail = ver.check("frontend", "T", {"verify_how": "vibes"})
    assert ok is False and "NOT verified" in detail


def test_github_comment_verifier_negative_is_negative(tmp_path):
    ver = Verifier(tmp_path)
    ok, detail = ver.check("status", "T", {
        "verify_how": "github_comment",
        "api_url": "https://api.github.com/repos/outcited/outcitedharness/issues/999999"})
    # 404 body is a dict without html_url for issues endpoint -> negative
    assert ok is False
