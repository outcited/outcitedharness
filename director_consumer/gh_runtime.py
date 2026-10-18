"""Periodic, observation-only GitHub work orders for the EXISTING Director loop.

GitHub issues are untrusted definitions, never delivery authorization.
No issue/comment may turn a task into an attributable agent acknowledgment.
This adapter writes task definitions to the project-scoped TaskStore and a
source-stamped, atomic status snapshot for the operator briefing.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Callable, Optional

from . import gh_workloop
from .tasks import TaskStore


class GithubIssueRuntime:
    def __init__(self, con: sqlite3.Connection, workspace: Path,
                 project_id: str, repositories: tuple[str, ...],
                 registered: Optional[Callable[[str], bool]] = None,
                 fetcher: Optional[Callable[[str], list[dict]]] = None):
        if not project_id:
            raise ValueError("a project_id is required for issue ingest")
        if not repositories:
            raise ValueError("explicit repositories required; no implicit scan")
        bad = set(repositories) - set(gh_workloop.ALLOWLISTED_REPOS)
        if bad:
            raise PermissionError(f"unapproved GitHub repositories: {sorted(bad)}")
        self.con = con
        self.workspace = Path(workspace)
        self.project_id = project_id
        self.repositories = tuple(dict.fromkeys(repositories))
        self.registered = registered
        self.fetcher = fetcher or gh_workloop.fetch_open_issues

    def _mail_ack(self, task_id: str, source_key: str,
                  epoch: Optional[int]) -> dict:
        """Never turn a GitHub reply or stale assignment into a trusted ACK.

        The existing MailboxDelivery.record_ack() validates the sender,
        scoped message digest, task, epoch and session origin before it marks
        a message acked. Here we *observe* that persisted result and confirm
        the delivery action envelope still matches this task's current epoch.
        Only a separately authorized live assignment can have epoch > 0.
        """
        none = {"state": "not-observed", "message_ids": []}
        if not epoch or epoch < 1:
            return none
        try:
            rows = self.con.execute(
                "SELECT m.message_id, a.envelope_json FROM messages m "
                "JOIN action_envelopes a ON a.action_id=m.message_id "
                "AND a.project=m.project "
                "WHERE m.project=? AND m.state='acked' "
                "AND m.acked_at IS NOT NULL "
                "AND m.correlation_id IN (?,?) "
                "ORDER BY m.acked_at DESC LIMIT 10",
                (self.project_id, task_id, source_key)).fetchall()
        except sqlite3.OperationalError:
            return none
        good = []
        for mid, raw_envelope in rows:
            try:
                env = json.loads(raw_envelope)
                if (env.get("project") == self.project_id
                    and env.get("task_ref") in (task_id, source_key)
                    and int(env.get("assignment_epoch", -1)) == epoch
                    and env.get("action_id") == mid):
                    good.append(mid)
            except (ValueError, TypeError, json.JSONDecodeError):
                continue
        return {"state": "recorded-mailbox-ack" if good else "not-observed",
                "message_ids": good[:3]}

    def run_once(self) -> dict:
        observed_at = time.time()
        report = {
            "schema": "director.github-workloop-status.v1",
            "observed_at": observed_at,
            "project_id": self.project_id,
            "mode": "observe-only",
            "delivery_enabled_by_this_adapter": False,
            "github_comments_are_acknowledgments": False,
            "repositories": {},
            "tasks": [],
            "errors": [],
        }
        if self.registered is not None and not self.registered(self.project_id):
            report["errors"].append({
                "code": "PROJECT_NOT_REGISTERED",
                "project_id": self.project_id,
                "detail": "register project before ingest; no tasks changed",
            })
            self._snapshot(report)
            return report
        store = TaskStore(self.con, project=self.project_id)
        for repo in self.repositories:
            repo_result = {"source_state": "UNKNOWN", "observed_at": observed_at,
                           "issues_observed": None, "new": 0, "duplicates": 0,
                           "untrusted": 0, "collisions": 0}
            report["repositories"][repo] = repo_result
            try:
                issues = self.fetcher(repo)
                # A transient fetch failure is NOT equivalent to 0 open issues.
                if not isinstance(issues, list):
                    raise TypeError("GitHub issues response was not a list")
                entries = gh_workloop.ingest(repo, store, issues=issues,
                                             now=observed_at)
                repo_result.update(source_state="observed",
                                   issues_observed=len(issues))
                for rec in entries:
                    if rec.state == "duplicate":
                        repo_result["duplicates"] += 1
                    elif rec.state == gh_workloop.UNTRUSTED_STATE:
                        repo_result["untrusted"] += 1
                    elif rec.state == "collision":
                        repo_result["collisions"] += 1
                    else:
                        repo_result["new"] += 1
                    row = self.con.execute(
                        "SELECT state, accountable_owner, epoch, correlation_id "
                        "FROM tasks WHERE project=? AND task_id=?",
                        (self.project_id, rec.task_id)).fetchone()
                    # Existing same-title task from another repo must NOT
                    # inherit this issue's status/owner.
                    source_key = f"{repo}#{rec.number}"
                    correct_source = bool(row and row[3] == source_key)
                    report["tasks"].append({
                        "task_id": rec.task_id,
                        "url": rec.url,
                        "repo": repo,
                        "definition_state": rec.state,
                        "missing_fields": rec.missing_fields,
                        "task_state": row[0] if correct_source else "UNKNOWN",
                        "owner_label": row[1] if correct_source else "UNKNOWN",
                        "assignment_epoch": row[2] if correct_source else None,
                        "transport_ack": self._mail_ack(rec.task_id, source_key,
                                                        row[2])
                            if correct_source else {"state": "not-observed",
                                                    "message_ids": []},
                    })
            except Exception as exc:  # noqa: BLE001 — preserve other feeds
                repo_result.update(source_state="ERROR", issues_observed=None)
                report["errors"].append({
                    "repo": repo, "code": type(exc).__name__,
                    "detail": str(exc)[:240],
                })
        self._snapshot(report)
        return report

    def _snapshot(self, report: dict) -> None:
        dest = self.workspace / "github-workloop-latest.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        temp = dest.with_suffix(".json.tmp")
        temp.write_text(json.dumps(report, sort_keys=True, indent=2))
        os.replace(temp, dest)
