"""Seven OpenCode CLI — visibility + autonomous execution + selftest.

Runs against the REAL workspace/TaskStore using the existing Director
modules (TaskStore, MailboxDelivery, AuthorizationStore, OpenCodeSession-
Transport, SessionDiscovery). No direct state writes; every transition
goes through the evidence-bearing APIs.

Usage:
  python -m director_consumer.oc_cli work-state
  python -m director_consumer.oc_cli oc-exec --task DW-DIRECTOR-TRIAGE-01 \
      --worktree PATH --auth-id sam-20261023-triage01-deliver
  python -m director_consumer.oc_cli oc-selftest
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from .authorization import AuthorizationStore
from .messaging import DeliveryPolicy, MailboxDelivery, OpenCodeSessionTransport
from .oc_selftest import run_selftest
from .sessions import SessionDiscovery
from .seven_exec import run_loop
from .tasks import TaskStore
from .work_state import WorkStateAggregator

DEFAULT_WORKSPACE = Path.home() / "seven-director/workspace"


def _connect(workspace: Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(workspace / "director.db"), timeout=15)
    con.row_factory = sqlite3.Row
    return con


def _mail(con, workspace: Path, auth_ref: str) -> MailboxDelivery:
    policy = DeliveryPolicy(enabled=True, authorization_ref=auth_ref,
                            outbox_root=workspace / "mailboxes",
                            require_existing_root=True)
    return MailboxDelivery(con, policy, project="designwins",
                           authorizations=AuthorizationStore(con),
                           transport=OpenCodeSessionTransport(
                               bindings_file=workspace /
                               "session_bindings.json"),
                           mode="test-delivery",
                           transport_name="authorized-delivery")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="oc_cli")
    ap.add_argument("--workspace", default=str(DEFAULT_WORKSPACE))
    ap.add_argument("--project", default="designwins")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("work-state")
    ex = sub.add_parser("oc-exec")
    ex.add_argument("--task", required=True)
    ex.add_argument("--worktree", required=True)
    ex.add_argument("--auth-id", required=True)
    ex.add_argument("--dry-run", action="store_true")
    sub.add_parser("oc-selftest")

    a = ap.parse_args(argv)
    workspace = Path(a.workspace)
    con = _connect(workspace)

    if a.cmd == "work-state":
        view = WorkStateAggregator(con, a.project, workspace,
                                   sessions=SessionDiscovery(
                                       machine="seven")).build()
        print(WorkStateAggregator.render(view))
        return 0

    if a.cmd == "oc-exec":
        delivery = _mail(con, workspace, a.auth_id)
        report = run_loop(con, a.project, workspace, task_id=a.task,
                          repo_worktree=Path(a.worktree),
                          bindings_path=workspace / "session_bindings.json",
                          delivery=delivery,
                          task_store=TaskStore(con, a.project),
                          dry_run=a.dry_run)
        print(json.dumps(report, indent=1))
        return 0 if report.get("result") in ("verified-complete",
                                             "already-complete") else 1

    if a.cmd == "oc-selftest":
        delivery = _mail(con, workspace, "selftest-no-such-grant")
        out = run_selftest(con, a.project, delivery, workspace)
        print(json.dumps(out, indent=1))
        return 0 if out.get("all_pass") else 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
