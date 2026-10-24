"""OpenCode session discovery (§5) — read-only.

Sources (never injected into, never modified):
  - ~/.local/share/opencode/opencode.db (session/workspace/todo schema,
    versioned by the OpenCode CLI itself)
  - process table via pgrep (health only; a terminal tab is NOT an agent
    identity and a dead terminal is NOT proof of task completion)

Every field carries provenance; unobserved fields are UNKNOWN.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

UNKNOWN = "unknown"


@dataclass
class SessionRecord:
    session_id: str
    machine: str = UNKNOWN
    agent: str = UNKNOWN
    project: str = UNKNOWN
    repo_worktree: str = UNKNOWN
    branch: str = UNKNOWN
    task_ids: list[str] = field(default_factory=list)
    kind: str = UNKNOWN        # human-interactive | managed | unknown
    last_activity_ts: float = 0.0
    task_state: str = UNKNOWN
    process_health: str = UNKNOWN
    owner: str = UNKNOWN
    parent_id: str = ""
    title: str = ""
    provenance: dict = field(default_factory=dict)


def _ro(db: Path) -> Optional[sqlite3.Connection]:
    if not db.exists():
        return None
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        con.row_factory = sqlite3.Row
        return con
    except sqlite3.Error:
        return None


def opencode_processes() -> int:
    """Count live opencode processes (health signal only)."""
    for pat in ("opencode",):
        try:
            r = subprocess.run(["pgrep", "-f", pat], capture_output=True,
                               text=True, timeout=5)
            n = len([x for x in r.stdout.split() if x.strip()])
            if n:
                return n
        except Exception:  # noqa: BLE001
            pass
    return 0


class SessionDiscovery:
    def __init__(self, opencode_db: Optional[Path] = None,
                 machine: str = UNKNOWN,
                 agent_kinds: Optional[dict[str, str]] = None,
                 now: Optional[float] = None):
        self.db = Path(opencode_db) if opencode_db else \
            Path.home() / ".local/share/opencode/opencode.db"
        self.machine = machine
        self.agent_kinds = agent_kinds or {}
        self.now = now if now is not None else time.time()

    def discover(self) -> list[SessionRecord]:
        con = _ro(self.db)
        if con is None:
            return []
        rows = []
        try:
            rows = con.execute(
                "SELECT id, title, time_created, parent_id, directory, "
                "branch FROM session ORDER BY time_created DESC LIMIT 200"
            ).fetchall()
        except sqlite3.Error:
            # older/newer schema — try the minimal column set
            try:
                rows = con.execute(
                    "SELECT id, title, time_created, '' AS parent_id, "
                    "directory, '' AS branch FROM session "
                    "ORDER BY time_created DESC LIMIT 200").fetchall()
            except sqlite3.Error:
                con.close()
                return []
        todo_text = {}
        try:
            for r in con.execute("SELECT session_id, content, status "
                                 "FROM todo LIMIT 500"):
                todo_text.setdefault(r["session_id"], []).append(
                    f"{r['status']}:{r['content']}")
        except sqlite3.Error:
            pass
        con.close()

        import re
        task_re = re.compile(r"\b([A-Z][A-Z0-9]+(?:-[A-Z0-9]+)+-\d+)\b")
        procs = opencode_processes()
        out = []
        for r in rows:
            todos = todo_text.get(r["id"], [])
            text = f"{r['title'] or ''} {' '.join(todos)}"
            s = SessionRecord(
                session_id=r["id"], machine=self.machine,
                title=r["title"] or "",
                parent_id=r["parent_id"] or "",
                last_activity_ts=float(r["time_created"] or 0) / (
                    1000.0 if (r["time_created"] or 0) > 1e12 else 1.0),
                repo_worktree=r["directory"] or UNKNOWN,
                branch=r["branch"] or UNKNOWN,
                task_ids=task_re.findall(text),
                task_state=(", ".join(todos[:2]) if todos else UNKNOWN),
                process_health=("processes-visible" if procs else
                                "no-process-observed"),
                provenance={"db": str(self.db)})
            # subagents are managed by definition; roots stay unknown unless
            # config says otherwise — a title is never an identity
            if s.parent_id:
                s.kind = "managed"
            s.agent = UNKNOWN   # never guessed from a title (§5)
            out.append(s)
        return out

    @staticmethod
    def to_events(records: list[SessionRecord]) -> list[dict]:
        """Only material session events feed the pipeline."""
        evs = []
        for s in records:
            if s.task_ids and s.process_health == "no-process-observed":
                evs.append({"kind": "session_disconnected",
                            "summary": f"session {s.session_id} has open task "
                                       f"ids {','.join(s.task_ids)} but no "
                                       f"observed process (NOT proof of "
                                       f"completion)",
                            "attrs": {"feed": "sessions", "services": [],
                                      "box": s.machine},
                            "refs": [f"opencode.session:{s.session_id}"]})
        return evs
