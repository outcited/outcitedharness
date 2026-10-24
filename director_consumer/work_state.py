"""Work-state visibility for Seven (section D) — one source-stamped view.

Answers: what is every agent doing, what is moving, what is blocked, what
is unobservable. Evidence-bearer model: every state carries its sources
(mailbox, TaskStore, sessions, git, fleet-state); an idle agent is NOT
broken and an unavailable host is UNKNOWN — never healthy, never failed.

Runs inside the existing Director cycle / CLI. Writes
workspace/work-state-latest.json (same pattern as ownership-latest.json).
No new daemon, no second dashboard.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from .sessions import SessionDiscovery

STATES = (
    "AGENT_REACHABLE", "TASK_OFFERED", "TASK_DELIVERED", "ACKNOWLEDGED",
    "WORK_STARTED", "MAKING_PROGRESS", "BLOCKED", "STALLED",
    "RESULT_SUBMITTED", "VERIFIED_COMPLETE", "UNKNOWN_UNOBSERVABLE",
)

# task-state -> work-state floor (evidence can only refine upward)
TASK_FLOOR = {
    "unassigned": "TASK_OFFERED",
    "offered": "TASK_OFFERED",
    "acknowledged": "ACKNOWLEDGED",
    "active": "WORK_STARTED",
    "submitted": "RESULT_SUBMITTED",
    "verified": "VERIFIED_COMPLETE",
    "blocked": "BLOCKED",
    "stale": "STALLED",
    "conflicted": "BLOCKED",
    "cancelled": "UNKNOWN_UNOBSERVABLE",
    "untrusted-definition": "UNKNOWN_UNOBSERVABLE",
    "closed": "VERIFIED_COMPLETE",
}

OWNERS = ("m4opencode", "m5-opencode", "m5-extraction-provenance",
          "mac-mini", "cr-core", "seven", "seven-director")


@dataclass
class TaskView:
    task_id: str
    owner: str
    task_state: str
    epoch: int
    work_state: str
    evidence: list = field(default_factory=list)
    last_evidence_ts: float = 0.0
    progress_note: str = ""
    actions: list = field(default_factory=list)


@dataclass
class OwnerView:
    owner: str
    reachability: str = "UNKNOWN_UNOBSERVABLE"
    reachability_source: str = ""
    tasks: list = field(default_factory=list)
    summary: str = ""


def _ts_age(ts: float, now: float) -> float:
    return (now - ts) / 3600.0 if ts else 1e9


class WorkStateAggregator:
    def __init__(self, con, project: str, workspace: Path,
                 sessions: Optional[SessionDiscovery] = None,
                 now: Optional[float] = None):
        self.con = con
        self.project = project
        self.workspace = Path(workspace)
        self.sessions = sessions or SessionDiscovery(machine="seven")
        self.now = now if now is not None else time.time()

    # ---- evidence collectors -------------------------------------------
    def _task_rows(self) -> list[dict]:
        cur = self.con.execute(
            "SELECT task_id, state, accountable_owner, execution_agent, "
            "session_id, host, epoch, updated_at, last_verified_progress "
            "FROM tasks WHERE project=?", (self.project,))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def _message_rows(self) -> dict:
        out = {}
        cur = self.con.execute(
            "SELECT message_id, recipient, msg_type, state, created_at, "
            "sent_at, acked_at, correlation_id FROM messages WHERE project=?",
            (self.project,))
        cols = [d[0] for d in cur.description]
        for r in cur.fetchall():
            d = dict(zip(cols, r))
            out[d.get("correlation_id") or d["message_id"]] = d
        return out

    def _session_rows(self) -> list:
        try:
            return self.sessions.discover()
        except Exception:  # noqa: BLE001 — visibility must never crash a cycle
            return []

    def _fleet(self) -> dict:
        p = self.workspace / "fleet-state.json"
        try:
            return json.loads(p.read_text())
        except (OSError, json.JSONDecodeError):
            return {}

    def _git_activity(self, task_id: str) -> list:
        """Commits/worktrees that NAME this task id — source activity that
        is genuinely about the task (a bare recent commit is NOT progress)."""
        hits = []
        wt_root = self.workspace.parent / "worktrees"
        for repo in [wt_root] if wt_root.exists() else []:
            for wt in sorted(repo.glob("*")):
                try:
                    r = subprocess.run(
                        ["git", "-C", str(wt), "log", "--oneline",
                         "--all", f"--grep={task_id}", "-n", "3"],
                        capture_output=True, text=True, timeout=10)
                    for line in (r.stdout or "").splitlines():
                        if line.strip():
                            hits.append(f"git:{wt.name}:{line.strip()[:80]}")
                except Exception:  # noqa: BLE001
                    continue
        return hits

    # ---- aggregation ----------------------------------------------------
    def build(self) -> dict:
        tasks = self._task_rows()
        msgs = self._message_rows()
        sess = self._session_rows()
        fleet = self._fleet()
        sess_by_task = {}
        for s in sess:
            for tid in getattr(s, "task_ids", []) or []:
                sess_by_task.setdefault(tid, []).append(s)

        task_views = []
        for t in tasks:
            tid = t["task_id"]
            floor = TASK_FLOOR.get(t["state"], "UNKNOWN_UNOBSERVABLE")
            ev, actions = [], []
            msg = msgs.get(tid)
            last_ts = 0.0
            if msg:
                ev.append(f"mail:{msg['state']}:sent={bool(msg.get('sent_at'))}"
                          f":acked={bool(msg.get('acked_at'))}")
                last_ts = max(last_ts, msg.get("acked_at") or 0,
                              msg.get("sent_at") or 0)
                if msg["state"] == "sent" and floor == "TASK_OFFERED":
                    floor = "TASK_DELIVERED"
            srows = sess_by_task.get(tid, [])
            for s in srows:
                ev.append(f"session:{s.session_id[:18]}:{s.process_health}")
                last_ts = max(last_ts, getattr(s, "last_activity_ts", 0) or 0)
            if srows and floor in ("TASK_DELIVERED", "TASK_OFFERED"):
                floor = "WORK_STARTED"
            git_hits = self._git_activity(tid)
            for h in git_hits:
                ev.append(h)
            if git_hits and floor == "WORK_STARTED":
                floor = "MAKING_PROGRESS"
            if t.get("last_verified_progress"):
                ev.append(f"verified_progress:{t['last_verified_progress'][:60]}")
                floor = "MAKING_PROGRESS" if floor in (
                    "WORK_STARTED", "TASK_DELIVERED") else floor
            updated = t.get("updated_at")
            if isinstance(updated, str):
                try:
                    last_ts = max(last_ts, time.mktime(
                        time.strptime(updated[:19], "%Y-%m-%d %H:%M:%S")))
                except (ValueError, OverflowError):
                    pass
            # stall: evidence exists but has aged with no terminal state
            if floor not in ("VERIFIED_COMPLETE", "UNKNOWN_UNOBSERVABLE") \
                    and last_ts and _ts_age(last_ts, self.now) > 24:
                floor = "STALLED"
                actions.append("inspect latest evidence; classify "
                               "waiting/blocked/failed/completed")
                actions.append("scoped request to owner via approved "
                               "delivery path if permitted")
            if t["state"] == "blocked":
                actions.append("identify blocker; escalate only if "
                               "authorization/product decision needed")
            task_views.append(TaskView(
                task_id=tid, owner=t.get("accountable_owner") or "",
                task_state=t["state"], epoch=t.get("epoch") or 0,
                work_state=floor, evidence=ev[:8],
                last_evidence_ts=last_ts, actions=actions))

        owner_views = []
        for owner in OWNERS:
            mine = [tv for tv in task_views if tv.owner == owner]
            reach, src = "UNKNOWN_UNOBSERVABLE", ""
            # owner reachability from fleet-state (host health) + any ACK
            for host, meta in (fleet.get("hosts") or {}).items():
                if owner.split("-")[0] in host or owner in host:
                    status = str((meta or {}).get("status", "")).lower()
                    if status in ("online", "ok", "healthy", "reachable"):
                        reach, src = "AGENT_REACHABLE", f"fleet-state:{host}"
                    elif status in ("offline", "unreachable", "unknown"):
                        reach = "UNKNOWN_UNOBSERVABLE"
                        src = f"fleet-state:{host}:{status or 'unknown'}"
            if any(tv.work_state in ("WORK_STARTED", "MAKING_PROGRESS",
                                     "ACKNOWLEDGED") for tv in mine):
                reach, src = "AGENT_REACHABLE", "task-evidence"
            summary = {}
            for tv in mine:
                summary[tv.work_state] = summary.get(tv.work_state, 0) + 1
            owner_views.append(OwnerView(
                owner=owner, reachability=reach, reachability_source=src,
                tasks=[asdict(tv) for tv in mine],
                summary=json.dumps(summary) if summary else "no tasks"))

        out = {
            "schema": "harness.seven-work-state.v1",
            "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                           time.gmtime(self.now)),
            "owners": [asdict(o) for o in owner_views],
            "unowned_tasks": [asdict(tv) for tv in task_views if not tv.owner],
            "note": "idle != broken; unavailable host -> UNKNOWN_UNOBSERVABLE; "
                    "commit activity counts only when it names the task id",
        }
        path = self.workspace / "work-state-latest.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(out, indent=1))
        os.replace(tmp, path)
        return out

    @staticmethod
    def render(view: dict) -> str:
        lines = ["== SEVEN WORK-STATE =="]
        for o in view["owners"]:
            lines.append(f"{o['owner']:28s} {o['reachability']:22s} "
                         f"{o['summary']} [{o['reachability_source']}]")
            for t in o["tasks"]:
                lines.append(f"    {t['task_id']:34s} {t['work_state']:20s} "
                             f"epoch={t['epoch']} state={t['task_state']}")
                for a in t["actions"]:
                    lines.append(f"        action: {a}")
        for t in view.get("unowned_tasks", []):
            lines.append(f"UNOWNED {t['task_id']:26s} {t['work_state']:20s} "
                         f"state={t['task_state']}")
        return "\n".join(lines)
