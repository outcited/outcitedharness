"""Desired-state reconciliation (§4) — the gap between what SHOULD be
happening and what IS happening.

Deterministic state comparisons + dependency traversal first; LLM stays
out of this module entirely (callers may attach hypotheses to findings).
Findings are idempotent: identity is hash(rule, subject); repeat polls
inside the cooldown record SUPPRESSED-with-reason instead of new work.
Every finding carries evidence, authority, expected outcome, and a
verification plan. NOTHING is executed (proposals only).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional

from .schema import fp, utcnow
from .scope import ensure_scoped

SCHEMA = """
CREATE TABLE IF NOT EXISTS reconciler_findings (
    finding_id TEXT PRIMARY KEY,
    project TEXT NOT NULL,
    rule TEXT NOT NULL,
    subject TEXT NOT NULL,
    severity TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'open',
    hits INTEGER NOT NULL DEFAULT 1,
    first_seen REAL NOT NULL,
    last_seen REAL NOT NULL,
    cooldown_until REAL NOT NULL DEFAULT 0,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reconciler_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    finding_id TEXT NOT NULL,
    note TEXT NOT NULL
);
"""

COOLDOWN_S = 6 * 3600.0

# §6: the director must know when NOT to act. Every suppressed/repeated
# finding records one of these reasons — never silence, never spam.
NO_ACTION_REASONS = (
    "existing-owner-repairing", "evidence-stale", "handoff-pending",
    "work-productive", "outside-delegated-authority", "insufficient-capacity",
    "duplicate-incident", "awaiting-verified-dependency", "no-safe-action")


@dataclass
class DesiredState:
    objectives: list = field(default_factory=list)      # [{id, text}]
    tasks: list = field(default_factory=list)           # task dicts
    verification_obligations: list = field(default_factory=list)
    critical_targets: dict = field(default_factory=dict)


@dataclass
class ObservedState:
    sessions: list = field(default_factory=list)
    ownership: list = field(default_factory=list)
    funnel: Optional[dict] = None
    incidents: list = field(default_factory=list)
    messages: list = field(default_factory=list)
    fleet: dict = field(default_factory=dict)
    now: float = field(default_factory=utcnow)


@dataclass
class Finding:
    rule: str
    subject: str
    severity: str
    evidence: list
    proposed_action: str
    authority: str
    verification_plan: str
    expected_outcome: str

    project: str = ""

    @property
    def finding_id(self) -> str:
        return fp("finding", self.project, self.rule, self.subject)[:16]


class Reconciler:
    def __init__(self, con, project: str = "default",
                 cooldown_s: float = COOLDOWN_S):
        self.con = con
        self.project = project
        self.cooldown_s = cooldown_s
        ensure_scoped(con)
        con.executescript(SCHEMA)

    # ---- deterministic discrepancy rules ----

    def _rules(self, d: DesiredState, o: ObservedState) -> list[Finding]:
        out: list[Finding] = []
        task_ids = {t.get("task_id") for t in d.tasks}
        states = {t.get("task_id"): t.get("state") for t in d.tasks}

        # approved objective with no task at all
        for obj in d.objectives:
            oid = obj.get("id", "")
            if not any(oid and (oid in (t.get("task_id") or "")
                                or t.get("objective_ref") == oid)
                       for t in d.tasks):
                out.append(Finding(
                    "objective-without-task", oid or obj.get("text", "")[:40],
                    "P2",
                    [f"objective {oid}: {obj.get('text', '')[:80]}"],
                    "propose creating an owned task for this objective",
                    "project-authority",
                    "task exists with an acknowledged owner",
                    "objective moves from approved to in-flight"))

        # task offered/unassigned with no progress path
        for t in d.tasks:
            tid = t.get("task_id", "")
            st = states.get(tid)
            if st in ("unassigned", "offered"):
                out.append(Finding(
                    "task-without-active-owner", tid, "P2",
                    [f"task {tid} state={st}"],
                    "propose assignment/offer to a lane owner",
                    "project-authority",
                    "owner acknowledges with attributable evidence",
                    "task reaches acknowledged/active"))
            if st in ("acknowledged", "active"):
                prog = t.get("last_verified_progress", "")
                updated = float(t.get("updated_at") or 0)
                if not prog and o.now - updated > 86400:
                    out.append(Finding(
                        "stalled-acknowledged", tid, "P1",
                        [f"task {tid} state={st}, no progress evidence "
                         f"for {(o.now - updated) / 86400:.1f}d"],
                        "propose status check to the owner",
                        "project-authority",
                        "fresh progress evidence or explicit blocker",
                        "work resumes or blocker is named"))

        # conflicting ownership
        for rec in o.ownership:
            if rec.get("state") == "conflicting":
                out.append(Finding(
                    "conflicting-claims", rec.get("task_id", ""), "P1",
                    [f"claimants {rec.get('claimants')} "
                     f"evidence {rec.get('evidence', [])[:3]}"],
                    "propose ownership reconciliation request",
                    "project-authority",
                    "one accountable owner confirmed by authority",
                    "single owner settles"))

        # upstream starvation (critical path) — only for projects that
        # DECLARE a critical path (shared observations never leak a
        # project's critical-path state into another project)
        if o.funnel and d.critical_targets:
            holdup = o.funnel.get("holdup")
            basis = o.funnel.get("starvation_basis")
            if holdup == "admission-gap" or basis in ("backlog-zero",
                                                      "measured"):
                out.append(Finding(
                    "starving-consumer", f"critical-path:{self.project}",
                    "P1",
                    [f"funnel holdup={holdup} starvation_basis={basis} "
                     f"queued={o.funnel.get('queued')}"],
                    "propose investigation of the admission/supply path",
                    "project-authority",
                    "measured ready-work coverage >= target",
                    "consumer has prioritized work again"))

        # repair reported but recovery unverified
        for inc in o.incidents:
            lc = inc.get("lifecycle_state", "")
            if lc in ("repair_in_progress", "recovery_observed"):
                out.append(Finding(
                    "repair-unverified", inc.get("correlation_id",
                                                 inc.get("incident_id", "")),
                    "P2",
                    [f"incident lifecycle={lc}"],
                    "propose requesting independent recovery evidence",
                    "project-authority",
                    "root-cause-fixed or verified-fresh-run evidence",
                    "incident reaches recovery_verified"))

        # owner host disappeared (migration/loss)
        for t in d.tasks:
            tid, host = t.get("task_id", ""), t.get("host", "")
            if host and states.get(tid) in ("acknowledged", "active"):
                mh = (o.fleet or {}).get(host, {})
                if mh.get("connectivity") == "offline":
                    out.append(Finding(
                        "owner-host-missing", tid, "P1",
                        [f"owner host {host} connectivity=offline"],
                        "propose migration check with the owner",
                        "project-authority",
                        "work continues on a reachable host or is parked",
                        "no silent loss of in-flight work"))

        # unacked sends
        for m in o.messages:
            if m.get("state") == "sent" and \
                    o.now - float(m.get("created_at") or 0) > 86400:
                out.append(Finding(
                    "message-unacked", m.get("message_id", ""), "P3",
                    [f"message {m.get('message_id')} sent, unacked >24h"],
                    "propose bounded re-ping or expiry",
                    "project-authority",
                    "ack arrives or message expires into escalation",
                    "no silent dropped handoffs"))
        return out

    # ---- idempotent reconcile ----

    def reconcile(self, desired: DesiredState,
                  observed: ObservedState) -> dict:
        findings = self._rules(desired, observed)
        for f in findings:
            f.project = self.project
        new, suppressed, open_ = [], [], []
        now = observed.now
        for f in findings:
            row = self.con.execute(
                "SELECT state, hits, cooldown_until FROM reconciler_findings "
                "WHERE finding_id=?", (f.finding_id,)).fetchone()
            payload = json.dumps({
                "severity": f.severity, "evidence": f.evidence[:5],
                "proposed_action": f.proposed_action,
                "authority": f.authority,
                "verification_plan": f.verification_plan,
                "expected_outcome": f.expected_outcome},
                sort_keys=True)
            if row is None:
                self.con.execute(
                    "INSERT INTO reconciler_findings(finding_id, project, "
                    "rule, subject, severity, first_seen, last_seen, "
                    "cooldown_until, payload) VALUES (?,?,?,?,?,?,?,?,?)",
                    (f.finding_id, self.project, f.rule, f.subject,
                     f.severity, now, now, now + self.cooldown_s, payload))
                new.append(f)
            else:
                state, hits, cd = row
                if cd > now:
                    self.con.execute(
                        "INSERT INTO reconciler_notes(ts, finding_id, note) "
                        "VALUES (?,?,?)",
                        (now, f.finding_id,
                         f"suppressed: cooldown until {cd} (hits={hits + 1}); "
                         f"reason=no-safe-action-or-pending "
                         f"(vocabulary: {','.join(NO_ACTION_REASONS)}); "
                         f"state unchanged — no new action"))
                    self.con.execute(
                        "UPDATE reconciler_findings SET hits=hits+1, "
                        "last_seen=? WHERE finding_id=?", (now, f.finding_id))
                    suppressed.append({"finding_id": f.finding_id,
                                       "why": "cooldown — same unresolved "
                                              "discrepancy, no new action"})
                else:
                    self.con.execute(
                        "UPDATE reconciler_findings SET hits=hits+1, "
                        "last_seen=?, cooldown_until=?, state='open' "
                        "WHERE finding_id=?",
                        (now, now + self.cooldown_s, f.finding_id))
                    open_.append(f)
        self.con.commit()
        return {"new": new, "suppressed": suppressed, "recurring": open_,
                "total_open": self.con.execute(
                    "SELECT COUNT(*) FROM reconciler_findings "
                    "WHERE state='open'").fetchone()[0]}

    def resolve(self, finding_id: str, evidence: str) -> None:
        self.con.execute(
            "UPDATE reconciler_findings SET state='resolved' "
            "WHERE finding_id=?", (finding_id,))
        self.con.execute(
            "INSERT INTO reconciler_notes(ts, finding_id, note) "
            "VALUES (?,?,?)", (utcnow(), finding_id,
                               f"resolved: {evidence[:160]}"))
        self.con.commit()
