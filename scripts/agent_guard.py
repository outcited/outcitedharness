"""Agent isolation guard (PRD-SEARCH-01 review, P3 directive 2026-10-08).

A pre-commit assertion that a worktree commits only to its assigned branch.
Assignment lives in ``.agent-branch`` at the worktree root (one line: the
branch name). No assignment file -> no assertion (unprotected worktrees
keep working; each lane opts in).

Rationale: a concurrent session switched this repo's checked-out branch
mid-commit and a search commit briefly landed on the curve lane's branch.
With per-agent worktrees plus this guard, that class of crossing is a hard
stop instead of a surprise.

The shim in .git/hooks/pre-commit (shared across worktrees) delegates here
only when the checked-out branch carries this script — it is a no-op on
lanes that do not adopt it, so installing the shim never blocks anyone
else.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def current_branch(root: Path) -> str:
    out = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True, cwd=str(root))
    return out.stdout.strip()


def assigned_branch(root: Path) -> str | None:
    assignment = root / ".agent-branch"
    if not assignment.exists():
        return None
    value = assignment.read_text().strip()
    return value or None


def check(root: Path | None = None) -> int:
    root = (root or Path(__file__).resolve().parents[1])
    expected = assigned_branch(root)
    if expected is None:
        return 0
    actual = current_branch(root)
    if actual != expected:
        print(
            f"agent guard: worktree is assigned to '{expected}' but HEAD is "
            f"'{actual}'.", file=sys.stderr)
        print("Refusing to commit. Switch branches in your own worktree — "
              "never change another agent's checked-out branch.",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(check())
