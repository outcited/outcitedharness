"""Seven autonomous execution loop (section F) — the working chain.

Wires the EXISTING systems end to end, no direct TaskStore writes:
  TaskStore discovery -> owner/epoch resolution -> MailboxDelivery ->
  operator-controlled OpenCode launch (opencode_launch) ->
  attributable ACK (record_ack + OpenCodeSessionTransport + bindings) ->
  execution observation (SessionDiscovery + work_state) ->
  acceptance verification -> TaskStore.transition (fenced) ->
  board advance. Idempotent: a second run detects done state and does
  not re-execute.
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from typing import Optional

from .acceptance import scoped_receipt
from .messaging import MailboxDelivery, OpenCodeSessionTransport
from .opencode_launch import BindingRegistry, launch
from .schema import fp
from .tasks import TaskStore, TransitionError, FencingError
from .work_state import WorkStateAggregator

SEVEN_USER = "seven"


def capture_failure_evidence(worktree: Path) -> str:
    """Operator-side evidence capture: run the named test files once and
    keep the failure summary. Keeps the model bounded (32K ctx)."""
    import subprocess
    tests = ["tests/test_director_01a.py", "tests/test_director_01b.py",
             "tests/test_director_02.py", "tests/test_director_03.py",
             "tests/test_director_consumer.py"]
    try:
        r = subprocess.run(
            [sys.executable, "-m", "pytest", *tests, "-q", "--tb=line",
             "--no-header", "-p", "no:cacheprovider"],
            cwd=str(worktree), capture_output=True, text=True, timeout=240)
        out = (r.stdout or "") + (r.stderr or "")
    except Exception as e:  # noqa: BLE001
        out = f"capture failed: {e}"
    return out[-12000:]


def build_triage_prompt(task_id: str, worktree: Path) -> str:
    evidence = capture_failure_evidence(worktree)
    return (
        f"READ-ONLY triage for Director task {task_id}. Below is captured "
        "pytest output for the 8 pre-existing director-suite failures "
        "(01a, 01b, 02x3, 03, consumer x2). Do not use any tools "
        "at all. Base your answer ONLY on the captured pytest output "
        "below. If root cause is uncertain say so in evidence. For EACH "
        "evidence (one line citing the assertion or code line), disposition "
        "(lane:<owner> for a real defect needing an owner, or C1 for "
        "acknowledged-wont-fix baseline). Your FIRST and ONLY reply message MUST be the JSON object, no commentary, no exploration, no commands. Output ONLY a JSON object: "
        '{"triage": [{"case": str, "file": str, "evidence": str, '
        '"disposition": str}]}' + "\n\nIMPORTANT: Do not deliberate. Do not narrate. Reply with the JSON object and nothing else. CAPTURED PYTEST OUTPUT:\n" + evidence
    )


def verify_triage_artifact(out_path: Path) -> tuple[bool, list[str]]:
    """Acceptance criteria: 8 classified cases, each with evidence and a
    disposition of lane:<name> or C1."""
    problems = []
    try:
        text = Path(out_path).with_suffix(".out.md").read_text()
    except OSError:
        return False, ["missing execution output artifact"]
    idx = text.find("{")
    data = None
    dec = json.JSONDecoder()
    while idx >= 0:
        try:
            obj, _end = dec.raw_decode(text[idx:])
            if isinstance(obj, dict) and "triage" in obj:
                data = obj
                break
        except json.JSONDecodeError:
            pass
        idx = text.find("{", idx + 1)
    if data is None:
        return False, ["no triage JSON found in artifact"]
    rows = data.get("triage") or []
    if len(rows) < 8:
        problems.append(f"expected 8 triage cases, found {len(rows)}")
    for i, row in enumerate(rows):
        if not row.get("evidence"):
            problems.append(f"case {i} missing evidence")
        disp = str(row.get("disposition") or "")
        if not (disp == "C1" or disp.startswith("lane:")):
            problems.append(f"case {i} disposition {disp!r} not lane:/C1")
        if not row.get("file"):
            problems.append(f"case {i} missing file")
    return (not problems), problems


def run_loop(con, project: str, workspace: Path, task_id: str,
             repo_worktree: Path, bindings_path: Path,
             delivery: MailboxDelivery,
             task_store: Optional[TaskStore] = None,
             dry_run: bool = False,
             opencode_bin: str = "opencode") -> dict:
    ts = task_store or TaskStore(con, project)
    report = {"task_id": task_id, "steps": [], "result": ""}

    def step(name: str, **kw):
        report["steps"].append({"step": name, **kw})

    # 1. discover + 2. resolve owner/epoch (existing registry semantics)
    rec = ts.get(task_id)
    if rec is None:
        report["result"] = "unknown-task"
        return report
    step("discovered", state=rec.state, owner=rec.accountable_owner,
         epoch=rec.epoch)
    if rec.accountable_owner not in (SEVEN_USER, f"{SEVEN_USER}-director"):
        report["result"] = f"not-seven-owned:{rec.accountable_owner}"
        return report
    if rec.state in ("verified", "closed", "cancelled"):
        step("idempotent-skip", reason=f"task already {rec.state}")
        report["result"] = "already-complete"
        return report

    # 3. approved channel: REUSE the operator-authorized delivered
    # assignment (single-action grants bind one message; a re-dispatch is a
    # new authorization event, never silent)
    row = con.execute(
        "SELECT message_id, body FROM messages WHERE project=? AND "
        "correlation_id=? AND state IN (\'sent\',\'delivering\',\'acked\') "
        "ORDER BY sent_at DESC LIMIT 1", (project, task_id)).fetchone()
    if row is not None:
        mid, body = row[0], row[1]
        step("delivered", message_id=mid, reused=True)
    else:
        body = (f"task={task_id} epoch={rec.epoch} "
                f"owner={rec.accountable_owner} "
                f"bounded-read-only-triage worktree={repo_worktree}")
        mid = delivery.propose(recipient=SEVEN_USER, msg_type="task",
                               subject=f"exec:{task_id}", body=body,
                               correlation_id=task_id,
                               assignment_epoch=rec.epoch)
        try:
            delivery.deliver(mid)
        except Exception as e:  # noqa: BLE001 — failures stay visible
            step("delivery-error", error=str(e)[:120])
        step("delivered", message_id=mid, reused=False)

    # 4-5. launch via operator wrapper (process+task+owner+epoch+session)
    prompt = build_triage_prompt(task_id, repo_worktree)
    artifact = workspace / "artifacts" / f"{task_id}-{int(time.time())}.md"
    receipt = launch(task_id=task_id, owner=rec.accountable_owner,
                     epoch=rec.epoch, message_id=mid, project=project,
                     worktree=repo_worktree, prompt=prompt,
                     bindings_path=bindings_path, artifact_path=artifact,
                     opencode_bin=opencode_bin, dry_run=dry_run)
    step("launched", session_id=receipt.session_id,
         exit_code=receipt.exit_code, artifact=str(artifact))

    # 6. attributable ACK through the real validator chain
    body_digest = fp("msg-body", body)
    envelope = scoped_receipt(mid, project, task_id, rec.epoch,
                              receipt.session_id, body_digest)
    registry = BindingRegistry(bindings_path)
    ack_state = delivery.record_ack(
        mid, ack_body="execution-started", from_agent=f"agent:{SEVEN_USER}",
        verified_sender=True,
        transport=OpenCodeSessionTransport(bindings_file=bindings_path),
        artifact_path=artifact,
        envelope=envelope,
        session_bindings=registry.task_epoch_map())
    step("ack", state=ack_state)
    if ack_state != "acked":
        report["result"] = f"ack-failed:{ack_state}"
        return report

    # 7-8. observe + verify against acceptance criteria
    if rec.state == "offered":
        try:
            ts.transition(task_id, "acknowledged", actor="seven-loop",
                          evidence=f"ack:{mid}", expect_epoch=rec.epoch,
                          execution_agent="opencode",
                          session_id=receipt.session_id, host="seven")
        except (TransitionError, FencingError) as e:
            step("transition-error", from_state=rec.state,
                 error=str(e)[:120])
    elif rec.state == "blocked":
        try:
            ts.transition(task_id, "active", actor="seven-loop",
                          evidence=f"ack:{mid} session:{receipt.session_id}",
                          expect_epoch=rec.epoch)
        except (TransitionError, FencingError) as e:
            step("transition-error", from_state=rec.state,
                 error=str(e)[:120])
    ok, problems = verify_triage_artifact(artifact)
    step("verified", accepted=ok, problems=problems)
    # progress event on real execution evidence
    try:
        ts.transition(task_id, "active", actor="seven-loop",
                      evidence=f"session:{receipt.session_id} "
                               f"exit={receipt.exit_code}",
                      expect_epoch=rec.epoch)
    except (TransitionError, FencingError) as e:
        step("transition-error", to_state="active", error=str(e)[:120])
    if receipt.exit_code != 0:
        try:
            ts.transition(task_id, "blocked", actor="seven-loop",
                          evidence=f"execution failed exit={receipt.exit_code}",
                          expect_epoch=rec.epoch)
        except (TransitionError, FencingError):
            pass
        report["result"] = "execution-failed"
        return report
    if ok:
        try:
            ts.transition(task_id, "submitted", actor="seven-loop",
                          evidence=f"artifact:{artifact}", expect_epoch=rec.epoch)
            ts.transition(task_id, "verified", actor="seven-loop",
                          evidence=f"acceptance-passed:{artifact}",
                          expect_epoch=rec.epoch)
        except (TransitionError, FencingError) as e:
            step("transition-error", to_state="verified", error=str(e)[:120])
        report["result"] = "verified-complete"
    else:
        try:
            ts.transition(task_id, "active", actor="seven-loop",
                          evidence=f"acceptance-failed:{problems}",
                          expect_epoch=rec.epoch)
        except (TransitionError, FencingError):
            pass
        report["result"] = "acceptance-failed"

    # 9-10. board advance + stable state view
    view = WorkStateAggregator(con, project, workspace).build()
    step("board", work_state=WorkStateAggregator.render(view).splitlines()[0])
    return report
