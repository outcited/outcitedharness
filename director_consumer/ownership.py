"""Task-ownership reconciliation (§6) — read-only, evidence-tiered.

Evidence law:
  - commit-with-task-id on an agent branch/worktree = CLAIM evidence
  - session todo / title mention = CLAIM evidence
  - mailbox message = ASSIGNMENT HINT ONLY (never proof of ownership)
  - an agent claiming a task is never sufficient authority alone
  - two claimants => conflicting ownership claims
  - no activity > stale_days => stale/abandoned
  - otherwise UNKNOWN (never guessed from a session title)

Outputs: per-task ownership records + recommendations + proposed message
drafts. No live leases, no reassignment (observe mode).
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

TASK_RE = re.compile(r"\b([A-Z][A-Z0-9]+(?:-[A-Z0-9]+)+-\d+)\b")
STALE_DAYS = 7


@dataclass
class Claim:
    agent: str
    task_id: str
    evidence_kind: str      # commit | branch | session | mailbox
    evidence: str           # locator (sha, branch, session id, filename)
    ts: float = 0.0
    strength: int = 2       # commit/branch=2 (claim), session=2, mailbox=1 (hint)


@dataclass
class OwnershipRecord:
    task_id: str
    state: str = "unknown"  # confirmed-owner | delegation | conflicting |
                            # stale | unknown
    claimants: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    recommendation: str = ""


def _git(repo: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args],
                       capture_output=True, text=True, timeout=30)
    return r.stdout if r.returncode == 0 else ""


def scan_git_claims(repo: Path, agent_of_branch=None,
                    since_days: int = 30) -> list[Claim]:
    """Task IDs from commit subjects + branches. Author mapping is
    branch-based when provided (project config); otherwise agent=unknown."""
    claims: list[Claim] = []
    out = _git(repo, "log", "--all", f"--since={since_days} days ago",
               "--pretty=%H|%ct|%s")
    for line in out.splitlines():
        parts = line.split("|", 2)
        if len(parts) != 3:
            continue
        sha, ts, subject = parts
        for tid in TASK_RE.findall(subject):
            claims.append(Claim(agent="unknown", task_id=tid,
                                evidence_kind="commit",
                                evidence=f"commit:{sha[:8]}",
                                ts=float(ts), strength=2))
    for line in _git(repo, "branch", "-a").splitlines():
        b = line.strip().lstrip("* ").strip()
        agent = agent_of_branch(b) if agent_of_branch else "unknown"
        for tid in TASK_RE.findall(b):
            claims.append(Claim(agent=agent, task_id=tid,
                                evidence_kind="branch", evidence=b,
                                strength=2))
    return claims


def scan_mailbox_hints(mail_root: Path) -> list[Claim]:
    """shared-docs-queue filenames/bodies carry task IDs as HINTS only."""
    claims: list[Claim] = []
    if not mail_root.exists():
        return claims
    for f in mail_root.rglob("*.md"):
        for tid in TASK_RE.findall(f.name):
            claims.append(Claim(agent="mailbox", task_id=tid,
                                evidence_kind="mailbox", evidence=str(f),
                                strength=1))
    return claims


def scan_session_claims(sessions: list[dict]) -> list[Claim]:
    claims = []
    for s in sessions:
        text = f"{s.get('title', '')} {s.get('todo_text', '')}"
        for tid in TASK_RE.findall(text):
            claims.append(Claim(
                agent=s.get("agent", "unknown"), task_id=tid,
                evidence_kind="session",
                evidence=f"session:{s.get('id', '?')}", strength=2))
    return claims


class OwnershipReconciler:
    def __init__(self, stale_days: int = STALE_DAYS):
        self.stale_s = stale_days * 86400.0

    def reconcile(self, claims: list[Claim], now: float) -> list[OwnershipRecord]:
        by_task: dict[str, list[Claim]] = {}
        for c in claims:
            by_task.setdefault(c.task_id, []).append(c)
        out = []
        for tid, cs in sorted(by_task.items()):
            # an "unknown" agent never establishes ownership (§6)
            claimants = sorted({c.agent for c in cs
                                if c.strength >= 2 and c.agent != "unknown"})
            hints = sorted({c.agent for c in cs if c.strength < 2})
            evidence = [f"{c.evidence_kind}:{c.evidence}" for c in cs]
            newest = max((c.ts for c in cs), default=0.0)
            rec = OwnershipRecord(task_id=tid, claimants=claimants,
                                  evidence=evidence)
            if len(claimants) >= 2:
                rec.state = "conflicting"
                rec.recommendation = (
                    f"conflicting ownership claims: {', '.join(claimants)} — "
                    f"propose a message asking them to settle who owns {tid}; "
                    f"mailbox hints ({', '.join(hints) or 'none'}) are not "
                    f"proof")
            elif len(claimants) == 1:
                if newest and now - newest > self.stale_s:
                    rec.state = "stale"
                    rec.recommendation = (
                        f"{claimants[0]} claims {tid} but evidence is "
                        f"{(now - newest) / 86400:.0f} days old — propose "
                        f"status check, do not reassign")
                else:
                    rec.state = "confirmed-owner"
                    rec.recommendation = (
                        f"{claimants[0]} owns {tid} (claim evidence); "
                        f"monitor only")
            else:
                rec.state = "unknown"
                rec.recommendation = (
                    f"ownership UNKNOWN for {tid}: only hints "
                    f"({', '.join(hints) or 'none'}) — propose asking who "
                    f"owns it rather than assigning")
            out.append(rec)
        return out

    def conflict_events(self, records: list[OwnershipRecord]) -> list[dict]:
        """Feed events for the consumer pipeline (ownership feed)."""
        evs = []
        for r in records:
            if r.state == "conflicting":
                evs.append({"kind": "ownership_conflict",
                            "summary": f"conflicting ownership claims for "
                                       f"{r.task_id}: "
                                       f"{', '.join(r.claimants)}",
                            "attrs": {"task": r.task_id,
                                      "services": [], "feed": "ownership"},
                            "refs": r.evidence[:3]})
            elif r.state == "stale":
                evs.append({"kind": "ownership_stale",
                            "summary": f"stale assignment: {r.task_id} "
                                       f"claimed by {', '.join(r.claimants)}",
                            "attrs": {"task": r.task_id,
                                      "services": [], "feed": "ownership"},
                            "refs": r.evidence[:3]})
        return evs
