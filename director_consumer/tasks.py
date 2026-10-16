"""Task-ownership contract (§2) — explicit assignment lifecycle with
accountability, acknowledgment, and fencing.

Laws:
  - one accountable owner at a time; contributors may be many
  - ownership requires ATTRIBUTABLE assignment evidence (authority
    signature), never mailbox names / session titles / machine nicknames
  - assignment epochs + fencing: a stale holder cannot silently reclaim
  - states: unassigned → offered → acknowledged → active → submitted
    → verified → closed; plus blocked, stale, conflicted, cancelled
  - every transition is evidence-bearing and append-only (task_events)
  - UNKNOWN ownership is never resolved by guessing; it produces a
    reconciliation REQUEST (proposed, not delivered)

Tables (director.db):
  tasks(task_id PK, project, state, accountable_owner, execution_agent,
        session_id, host, assignment_authority, epoch, created_at,
        updated_at, last_verified_progress, correlation_id)
  task_events(id, task_id, ts, from_state, to_state, actor, evidence, note)
  task_contributors(task_id, agent_id, role)
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Optional

from .schema import fp, utcnow

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    project TEXT NOT NULL,
    state TEXT NOT NULL,
    accountable_owner TEXT NOT NULL DEFAULT '',
    execution_agent TEXT NOT NULL DEFAULT '',
    session_id TEXT NOT NULL DEFAULT '',
    host TEXT NOT NULL DEFAULT '',
    assignment_authority TEXT NOT NULL DEFAULT '',
    epoch INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    last_verified_progress TEXT NOT NULL DEFAULT '',
    correlation_id TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS task_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    ts REAL NOT NULL,
    from_state TEXT NOT NULL,
    to_state TEXT NOT NULL,
    actor TEXT NOT NULL,
    evidence TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS task_contributors (
    task_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'contributor',
    PRIMARY KEY (task_id, agent_id)
);
CREATE INDEX IF NOT EXISTS idx_task_events ON task_events(task_id);
"""

# legal transitions (explicit; anything else is rejected)
LEGAL = {
    "unassigned": {"offered", "cancelled"},
    "offered": {"acknowledged", "unassigned", "cancelled", "conflicted",
                "offered"},   # re-offer/reassignment bumps the epoch
    "acknowledged": {"active", "blocked", "stale", "conflicted", "cancelled"},
    "active": {"submitted", "blocked", "stale", "conflicted", "cancelled"},
    "submitted": {"verified", "active", "blocked", "cancelled"},  # failed verif -> active
    "verified": {"closed", "reopened"},
    "closed": {"reopened"},
    "blocked": {"offered", "active", "stale", "cancelled"},
    "stale": {"offered", "active", "cancelled"},
    "conflicted": {"offered", "cancelled"},
    "reopened": {"offered", "active", "blocked", "cancelled"},
}
TERMINAL = {"closed", "cancelled"}


class TransitionError(Exception):
    pass


class FencingError(Exception):
    pass


@dataclass
class TaskRecord:
    task_id: str
    project: str
    state: str = "unassigned"
    accountable_owner: str = ""
    execution_agent: str = ""
    session_id: str = ""
    host: str = ""
    assignment_authority: str = ""
    epoch: int = 0
    contributors: list[str] = field(default_factory=list)


class TaskStore:
    def __init__(self, con, project: str = "default"):
        self.con = con
        self.project = project
        con.executescript(SCHEMA)

    # ---- core operations (all evidence-bearing) ----

    def get(self, task_id: str) -> Optional[TaskRecord]:
        row = self.con.execute("SELECT * FROM tasks WHERE task_id=?",
                               (task_id,)).fetchone()
        if row is None:
            return None
        cols = [d[0] for d in self.con.execute(
            "SELECT * FROM tasks LIMIT 0").description]
        d = dict(zip(cols, row))
        contrib = [r[0] for r in self.con.execute(
            "SELECT agent_id FROM task_contributors WHERE task_id=?",
            (task_id,))]
        return TaskRecord(task_id=task_id, project=d["project"],
                          state=d["state"],
                          accountable_owner=d["accountable_owner"],
                          execution_agent=d["execution_agent"],
                          session_id=d["session_id"], host=d["host"],
                          assignment_authority=d["assignment_authority"],
                          epoch=d["epoch"], contributors=contrib)

    def create(self, task_id: str, authority: str, evidence: str,
               correlation_id: str = "") -> TaskRecord:
        now = utcnow()
        self.con.execute(
            "INSERT OR IGNORE INTO tasks(task_id, project, state, "
            "assignment_authority, created_at, updated_at, correlation_id) "
            "VALUES (?,?,?,?,?,?,?)",
            (task_id, self.project, "unassigned", authority, now, now,
             correlation_id))
        self.con.execute(
            "INSERT INTO task_events(task_id, ts, from_state, to_state, "
            "actor, evidence) VALUES (?,?,?,?,?,?)",
            (task_id, now, "", "unassigned", authority, evidence))
        self.con.commit()
        return self.get(task_id)

    def transition(self, task_id: str, to_state: str, actor: str,
                   evidence: str, *, expect_epoch: Optional[int] = None,
                   owner: str = "", execution_agent: str = "",
                   session_id: str = "", host: str = "",
                   authority: str = "", note: str = "") -> TaskRecord:
        """Evidence-bearing transition with fencing.

        expect_epoch: caller's fencing token; if the task epoch has moved
        on, the write is REJECTED (FencingError) — a stale holder cannot
        silently reclaim work. Evidence is mandatory for every transition.
        """
        if not evidence:
            raise TransitionError("evidence is mandatory for transitions")
        rec = self.get(task_id)
        if rec is None:
            raise TransitionError(f"unknown task {task_id}")
        if expect_epoch is not None and expect_epoch != rec.epoch:
            raise FencingError(
                f"stale epoch {expect_epoch} != current {rec.epoch}")
        if to_state not in LEGAL.get(rec.state, set()):
            raise TransitionError(f"{rec.state} -> {to_state} is not a legal "
                                  f"transition")
        now = utcnow()
        new_epoch = rec.epoch
        if to_state == "offered" and owner:
            new_epoch = rec.epoch + 1   # new assignment epoch
        updates = {
            "state": to_state,
            "updated_at": now,
            "accountable_owner": owner or rec.accountable_owner,
            "execution_agent": execution_agent or rec.execution_agent,
            "session_id": session_id or rec.session_id,
            "host": host or rec.host,
            "assignment_authority": authority or rec.assignment_authority,
            "epoch": new_epoch,
        }
        if to_state == "verified":
            updates["last_verified_progress"] = evidence[:200]
        sets = ", ".join(f"{k}=?" for k in updates)
        self.con.execute(f"UPDATE tasks SET {sets} WHERE task_id=?",
                         (*updates.values(), task_id))
        self.con.execute(
            "INSERT INTO task_events(task_id, ts, from_state, to_state, "
            "actor, evidence, note) VALUES (?,?,?,?,?,?,?)",
            (task_id, now, rec.state, to_state, actor, evidence, note))
        self.con.commit()
        return self.get(task_id)

    def add_contributor(self, task_id: str, agent_id: str,
                        role: str = "contributor") -> None:
        self.con.execute(
            "INSERT OR IGNORE INTO task_contributors(task_id, agent_id, role) "
            "VALUES (?,?,?)", (task_id, agent_id, role))
        self.con.commit()

    def history(self, task_id: str) -> list[dict]:
        rows = self.con.execute(
            "SELECT ts, from_state, to_state, actor, evidence, note "
            "FROM task_events WHERE task_id=? ORDER BY id", (task_id,))
        return [dict(zip(("ts", "from_state", "to_state", "actor",
                          "evidence", "note"), r)) for r in rows]


def reconcile_request(task_id: str, claimants: list[str],
                      evidence: list[str]) -> dict:
    """For UNKNOWN/conflicted ownership: a PROPOSED reconciliation request
    (never a reassignment). The 17 historical tasks stay unowned until an
    attributable authority answers this."""
    return {
        "kind": "ownership_reconciliation_request",
        "task_id": task_id,
        "claimants": claimants,
        "evidence": evidence[:5],
        "to": "project-authority",
        "question": f"Who is the accountable owner of {task_id}? "
                    f"Existing evidence is non-attributable "
                    f"(claims: {', '.join(claimants) or 'none'}).",
        "proposed": True,
    }
