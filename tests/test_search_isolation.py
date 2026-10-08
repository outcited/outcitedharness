"""Agent isolation guard tests (P3 directive 2026-10-08)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.agent_guard import assigned_branch, check  # noqa: E402

REPO = Path(__file__).resolve().parents[1]


def test_this_worktree_is_assigned_and_matches():
    assert assigned_branch(REPO) == "search/evidence-retrieval-v0"
    assert check(REPO) == 0


def test_no_assignment_is_no_assertion(tmp_path):
    (tmp_path / ".agent-branch").unlink(missing_ok=True)
    assert assigned_branch(tmp_path) is None
    assert check(tmp_path) == 0


def test_branch_mismatch_blocks(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".agent-branch").write_text("some/other-branch\n")
    assert check(tmp_path) == 1


def test_matching_assignment_in_fresh_repo(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    head = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True).stdout.strip()
    (tmp_path / ".agent-branch").write_text(head + "\n")
    assert check(tmp_path) == 0
