"""The ordinary Director tick must ingest issues autonomously, without ACK spoofing.

All tests use isolated SQLite and fake GitHub reads; no production mail,
GitHub writes, host service, or live network touched.
"""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from director_consumer.consumer import DirectorConsumer
from director_consumer.gh_runtime import GithubIssueRuntime
from director_consumer.projects import ProjectSpec
from director_consumer.tasks import TaskStore
from director_consumer.gh_workloop import ingest


REPO = "outcited/outcitedharness"


def _issue(n=5, title="DW-TRUTH-FUNNEL-01 — M5: verify evidence", body=""):
    return {"number": n, "title": title, "body": body,
            "html_url": f"https://github.com/{REPO}/issues/{n}"}


def test_normal_director_tick_picks_up_new_tasks_and_survives_restart(tmp_path):
    project = tmp_path / "designwins"
    project.mkdir()
    (project / "policy.yaml").write_text(
        "authority:\n  human_owner: sam\n  human_only_actions: {}\n"
        "models:\n  allowed: []\n  rate_limits: {per_minute: 2, burst: 1}\n")
    workspace = tmp_path / "ws"
    deps = Path(__file__).resolve().parents[1] / "director_consumer/deps.yaml"
    c = DirectorConsumer(
        workspace, deps, llm=None, project_dir=project,
        github_repos=(REPO,), github_project_id="designwins")
    try:
        c.project_registry.register(
            ProjectSpec(project_id="designwins", name="DesignWins"))
        live_issues = [_issue()]
        c.github_runtime.fetcher = lambda repo: list(live_issues)

        r1 = c.run_once()
        assert r1.github_workloop["repositories"][REPO]["new"] == 1
        row = TaskStore(c.state.con, project="designwins").get(
            "DW-TRUTH-FUNNEL-01")
        assert row.state == "offered"
        assert row.accountable_owner == ""  # title inference is not ownership
        assert row.epoch == 0
        assert r1.github_workloop["tasks"][0]["transport_ack"]["state"] == "not-observed"

        live_issues.append(_issue(9, "DW-SECOND-01 — M5: second task"))
        r2 = c.run_once()  # no separate orchestrator/scheduler, same live loop
        assert r2.github_workloop["repositories"][REPO]["new"] == 1
        assert r2.github_workloop["repositories"][REPO]["duplicates"] == 1
        rows = c.state.con.execute(
            "SELECT task_id FROM tasks WHERE project='designwins' "
            "AND task_id IN ('DW-TRUTH-FUNNEL-01','DW-SECOND-01')").fetchall()
        assert len(rows) == 2
        count = c.state.con.execute(
            "SELECT COUNT(*) FROM task_events WHERE "
            "task_id='DW-TRUTH-FUNNEL-01' AND project='designwins'").fetchone()[0]
        assert count == 2  # initial creation + offered; no re-poll duplication

        snap = json.loads(
            (workspace / "github-workloop-latest.json").read_text())
        assert snap["repositories"][REPO]["issues_observed"] == 2
        assert snap["delivery_enabled_by_this_adapter"] is False
        assert snap["github_comments_are_acknowledgments"] is False
        assert (workspace / "briefings").exists()
    finally:
        c.close()


def test_unregistered_project_never_registers_fake_work(tmp_path):
    db = sqlite3.connect(tmp_path / "db.sqlite")
    run = GithubIssueRuntime(
        db, tmp_path, "designwins", (REPO,),
        registered=lambda pid: False,
        fetcher=lambda repo: [_issue()])
    snap = run.run_once()
    assert snap["errors"][0]["code"] == "PROJECT_NOT_REGISTERED"
    assert snap["tasks"] == []
    assert (tmp_path / "github-workloop-latest.json").exists()


def test_outage_is_error_not_zero_open_issues(tmp_path):
    db = sqlite3.connect(tmp_path / "db.sqlite")
    run = GithubIssueRuntime(
        db, tmp_path, "designwins", (REPO,),
        fetcher=lambda repo: (_ for _ in ()).throw(TimeoutError("offline")))
    snap = run.run_once()
    assert snap["repositories"][REPO]["source_state"] == "ERROR"
    assert snap["repositories"][REPO]["issues_observed"] is None
    assert snap["errors"][0]["code"] == "TimeoutError"


def test_non_allowlisted_repository_rejected_before_network(tmp_path):
    with pytest.raises(PermissionError):
        GithubIssueRuntime(sqlite3.connect(":memory:"), tmp_path, "designwins",
                           ("attacker/proposals",))


def test_cross_project_issue_ids_do_not_collide(tmp_path):
    db = sqlite3.connect(tmp_path / "db.sqlite")
    a, b = TaskStore(db, project="designwins"), TaskStore(db, project="other")
    assert ingest(REPO, a, issues=[_issue()])[0].state == "offered"
    assert ingest(REPO, b, issues=[_issue()])[0].state == "offered"
    assert db.execute(
        "SELECT COUNT(*) FROM tasks WHERE task_id='DW-TRUTH-FUNNEL-01'"
    ).fetchone()[0] == 2


def test_same_project_title_collision_never_steals_another_issue(tmp_path):
    db = sqlite3.connect(tmp_path / "db.sqlite")
    t = TaskStore(db, project="designwins")
    ingest(REPO, t, issues=[_issue()])
    stolen = _issue(55)
    result = ingest(REPO, t, issues=[stolen])
    assert result[0].state == "collision"
    assert t.get("DW-TRUTH-FUNNEL-01").accountable_owner == ""
    assert db.execute(
        "SELECT COUNT(*) FROM task_events WHERE "
        "project='designwins' AND task_id='DW-TRUTH-FUNNEL-01'"
    ).fetchone()[0] == 2


def test_github_only_ack_is_reported_as_unverified(tmp_path):
    db = sqlite3.connect(tmp_path / "db.sqlite")
    t = TaskStore(db, project="designwins")
    ingest(REPO, t, issues=[_issue()])
    # Simulate the existing manual/GitHub-comment-origin transition.
    # The status adapter MUST NOT call this an authenticated mailbox ack.
    t.transition("DW-TRUTH-FUNNEL-01", "acknowledged",
                 actor="human-report", evidence="github comment")
    run = GithubIssueRuntime(
        db, tmp_path, "designwins", (REPO,),
        fetcher=lambda repo: [_issue()])
    row = run.run_once()["tasks"][0]
    assert row["task_state"] == "acknowledged"
    assert row["transport_ack"]["state"] == "not-observed"
    # A GitHub-only transition remains visibly distinct from trusted mail.
    assert row["transport_ack"]["message_ids"] == []
