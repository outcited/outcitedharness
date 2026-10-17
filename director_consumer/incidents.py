"""Incident correlation — generic (§4).

Symptoms become one incident ONLY through deterministic evidence
relationships: same correlation group + same identity key + overlapping
window. Temporal closeness alone NEVER merges. Suspected cause is always
explicitly non-verified; verified cause requires resolution evidence.

Tables (director.db):
  incidents(incident_id PK, project, correlation_id, identity_key,
            first_seen, last_seen, state, severity, suspected_cause,
            verified_cause NULL, owner, related_rules, evidence,
            proposed_action, resolution, reopen_count)
  incident_events(incident_id, trigger_key, ts)  -- links assessments
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Optional

from .schema import Assessment, fp, utcnow
from .scope import ensure_scoped

SCHEMA = """
CREATE TABLE IF NOT EXISTS incidents (
    incident_id TEXT NOT NULL,
    project TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    identity_key TEXT NOT NULL,
    first_seen REAL NOT NULL,
    last_seen REAL NOT NULL,
    state TEXT NOT NULL,              -- open|resolved|superseded
    severity TEXT NOT NULL,
    suspected_cause TEXT NOT NULL,
    verified_cause TEXT,
    owner TEXT,
    related_rules TEXT NOT NULL,
    evidence TEXT NOT NULL,
    proposed_action TEXT,
    resolution TEXT,
    reopen_count INTEGER NOT NULL DEFAULT 0,
    lifecycle_state TEXT NOT NULL DEFAULT 'detected',
    occurrence INTEGER NOT NULL DEFAULT 1,
    verified_cause_policy TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (project, incident_id)
);
CREATE TABLE IF NOT EXISTS incident_events (
    incident_id TEXT NOT NULL,
    project TEXT NOT NULL DEFAULT 'default',
    trigger_key TEXT NOT NULL,
    ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS incident_recovery (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id TEXT NOT NULL,
    project TEXT NOT NULL DEFAULT 'default',
    occurrence INTEGER NOT NULL DEFAULT 1,
    ts REAL NOT NULL,
    kind TEXT NOT NULL,        -- symptom-clear | root-cause-fixed |
                               -- fix-deployed | verified-fresh-run
    detail TEXT NOT NULL,
    receipt TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_inc_events ON incident_events(incident_id);
CREATE INDEX IF NOT EXISTS idx_inc_open ON incidents(state);
"""


@dataclass
class CorrelationRule:
    cid: str
    group_rules: list[str]
    group_key: str          # assessment attr or "feed" fallback via event kind
    window_h: float
    suspected_cause: str
    owner_hint: str = ""


SEV_RANK = {"P0": 0, "P1": 1, "P2": 2, "P3": 3, "P4": 4}


def _worst(a: str, b: str) -> str:
    return a if SEV_RANK.get(a, 9) <= SEV_RANK.get(b, 9) else b


# ---- recovery lifecycle (§4): evidence-gated transitions ----
RECOVERY_LEGAL = {
    "detected": {"investigating", "escalated", "blocked"},
    "investigating": {"mitigation_proposed", "repair_in_progress",
                      "escalated", "blocked"},
    "mitigation_proposed": {"repair_in_progress", "investigating",
                            "escalated", "blocked"},
    "repair_in_progress": {"recovery_observed", "investigating",
                           "escalated", "blocked"},
    "recovery_observed": {"recovery_verified", "repair_in_progress",
                          "detected"},   # symptom back -> relapse
    "recovery_verified": {"closed", "detected"},
    "closed": {"detected"},              # recurrence
    "blocked": {"investigating", "escalated"},
    "escalated": {"investigating", "blocked"},
}


class RecoveryError(Exception):
    pass


def load_correlations(deps: dict) -> list[CorrelationRule]:
    out = []
    for r in deps.get("correlations", []):
        out.append(CorrelationRule(
            cid=r["id"], group_rules=r["group_rules"],
            group_key=r.get("group_key", "feed"),
            window_h=float(r.get("window_h", 24)),
            suspected_cause=r.get("suspected_cause", "unknown cause"),
            owner_hint=r.get("owner_hint", "")))
    return out


class IncidentStore:
    def __init__(self, con, correlations: list[CorrelationRule],
                 project: str = "default"):
        self.con = con
        self.rules = correlations
        self.project = project
        ensure_scoped(con)
        con.executescript(SCHEMA)
        cols = {r[1] for r in con.execute("PRAGMA table_info(incidents)")}
        if "lifecycle_state" not in cols:
            con.execute("ALTER TABLE incidents ADD COLUMN lifecycle_state "
                        "TEXT NOT NULL DEFAULT 'detected'")
            con.commit()
        self.by_rule = {rid: r for r in correlations for rid in r.group_rules}

    # ---- recovery lifecycle (§4) ----

    def mark_recovery_evidence(self, incident_id: str, kind: str,
                               detail: str,
                               receipt: Optional[dict] = None) -> None:
        if kind not in ("symptom-clear", "root-cause-fixed",
                        "fix-deployed", "verified-fresh-run"):
            raise RecoveryError(f"unknown evidence kind {kind!r}")
        row = self.con.execute(
            "SELECT occurrence FROM incidents WHERE incident_id=? "
            "AND project=?", (incident_id, self.project)).fetchone()
        occ = row[0] if row else 1
        self.con.execute(
            "INSERT INTO incident_recovery(incident_id, project, "
            "occurrence, ts, kind, detail, receipt) VALUES (?,?,?,?,?,?,?)",
            (incident_id, self.project, occ, utcnow(), kind, detail,
             json.dumps(receipt) if receipt else ""))
        self.con.commit()

    def lifecycle_transition(self, incident_id: str, to_state: str,
                             actor: str, evidence: str) -> str:
        """Evidence-gated lifecycle move. recovery_verified REQUIRES
        root-cause or fresh-run evidence — a vanished symptom is not
        recovery, and an LLM opinion is never evidence."""
        if not evidence:
            raise RecoveryError("evidence is mandatory for transitions")
        row = self.con.execute(
            "SELECT lifecycle_state FROM incidents WHERE incident_id=? "
            "AND project=?", (incident_id, self.project)).fetchone()
        if row is None:
            raise RecoveryError(f"unknown incident {incident_id}")
        cur = row[0]
        if to_state not in RECOVERY_LEGAL.get(cur, set()):
            raise RecoveryError(f"{cur} -> {to_state} is not a legal "
                                f"lifecycle transition")
        if to_state == "recovery_verified":
            occ_row = self.con.execute(
                "SELECT occurrence FROM incidents WHERE incident_id=? "
                "AND project=?", (incident_id, self.project)).fetchone()
            occ = occ_row[0] if occ_row else 1
            rows = self.con.execute(
                "SELECT kind, receipt FROM incident_recovery "
                "WHERE incident_id=? AND project=? AND occurrence=?",
                (incident_id, self.project, occ)).fetchall()
            # evidence from an OLDER occurrence never closes a recurrence
            kinds = {r[0] for r in rows}
            if not (kinds & {"root-cause-fixed", "verified-fresh-run"}):
                raise RecoveryError(
                    "recovery_verified requires root-cause-fixed or "
                    "verified-fresh-run evidence from the CURRENT "
                    "occurrence — a cleared symptom or stale evidence is "
                    "not a verified recovery")
        if to_state == "closed" and cur != "recovery_verified":
            raise RecoveryError("closed only from recovery_verified")
        if to_state == "detected" and cur in ("closed", "recovery_verified"):
            # recurrence: new occurrence freezes older evidence
            self.con.execute(
                "UPDATE incidents SET occurrence=occurrence+1 "
                "WHERE incident_id=? AND project=?",
                (incident_id, self.project))
        self.con.execute(
            "UPDATE incidents SET lifecycle_state=? WHERE incident_id=? "
            "AND project=?", (to_state, incident_id, self.project))
        self.con.execute(
            "INSERT INTO incident_events(incident_id, project, trigger_key, "
            "ts) VALUES (?,?,?,?)",
            (incident_id, self.project,
             f"lifecycle:{cur}->{to_state}:{actor}", utcnow()))
        self.con.commit()
        return to_state

    def lifecycle(self, incident_id: str) -> dict:
        row = self.con.execute(
            "SELECT lifecycle_state, reopen_count FROM incidents "
            "WHERE incident_id=? AND project=?",
            (incident_id, self.project)).fetchone()
        kinds = [dict(zip(("ts", "kind", "detail"), r)) for r in
                 self.con.execute("SELECT ts, kind, detail FROM "
                                  "incident_recovery WHERE incident_id=? "
                                  "ORDER BY id", (incident_id,))]
        return {"lifecycle_state": row[0] if row else None,
                "reopen_count": row[1] if row else 0,
                "recovery_evidence": kinds}

    def _identity(self, a: Assessment, r: CorrelationRule) -> str:
        # identity key: explicit attrs first (box/feed), then event kind
        key = None
        for ref in a.evidence:
            key = key or ref.feed
        return f"{r.group_key}:{key or a.event_kind}"

    def observe(self, a: Assessment) -> Optional[str]:
        """Feed one assessment into correlation. Returns incident_id or
        None when the assessment belongs to no correlation group."""
        r = self.by_rule.get(a.rule_id)
        if r is None:
            return None
        now = utcnow()
        bucket = int(now // (r.window_h * 3600))
        ident = self._identity(a, r)
        incident_id = fp("incident", self.project, r.cid, ident,
                 bucket)[:16]
        row = self.con.execute(
            "SELECT incident_id, state, severity, first_seen FROM incidents "
            "WHERE incident_id=? AND project=?",
            (incident_id, self.project)).fetchone()
        if row is None:
            self.con.execute(
                "INSERT INTO incidents(incident_id, project, correlation_id, "
                "identity_key, first_seen, last_seen, state, severity, "
                "suspected_cause, verified_cause, owner, related_rules, "
                "evidence, proposed_action, resolution, reopen_count) "
                "VALUES (?,?,?,?,?,?,?,?,?,NULL,?,?,'[]',NULL,NULL,0)",
                (incident_id, self.project, r.cid, ident, now, now, "open",
                 a.severity, r.suspected_cause,
                 a.authority if a.authority.startswith("agent:") else "",
                 json.dumps([a.rule_id])))
        else:
            reopened = row[1] == "resolved"
            new_sev = _worst(row[2], a.severity)
            self.con.execute(
                "UPDATE incidents SET last_seen=?, severity=?, "
                "state='open', reopen_count=reopen_count+?, "
                "occurrence=occurrence+? WHERE incident_id=?",
                (now, new_sev, 1 if reopened else 0,
                 1 if reopened else 0, incident_id))
            rules = set(json.loads(
                self.con.execute("SELECT related_rules FROM incidents "
                                 "WHERE incident_id=? AND project=?",
                                 (incident_id, self.project)).fetchone()[0]))
            rules.add(a.rule_id)
            self.con.execute(
                "UPDATE incidents SET related_rules=? WHERE incident_id=? "
                "AND project=?",
                (json.dumps(sorted(rules)), incident_id, self.project))
        self.con.execute(
            "INSERT INTO incident_events(incident_id, project, trigger_key, "
            "ts) VALUES (?,?,?,?)",
            (incident_id, self.project, a.trigger_key, now))
        # evidence accumulates (bounded)
        ev = json.loads(self.con.execute(
            "SELECT evidence FROM incidents WHERE incident_id=? "
            "AND project=?", (incident_id, self.project)).fetchone()[0])
        entry = {"rule": a.rule_id, "summary": a.event_summary[:160],
                 "ts": now,
                 "refs": [{"feed": x.feed, "source_id": x.source_id}
                          for x in a.evidence]}
        if entry not in ev:
            ev.append(entry)
        self.con.execute(
            "UPDATE incidents SET evidence=? WHERE incident_id=? "
            "AND project=?", (json.dumps(ev[-20:]), incident_id,
             self.project))
        self.con.execute(
            "UPDATE incidents SET proposed_action=? WHERE incident_id=? "
            "AND project=? AND proposed_action IS NULL",
            (a.action, incident_id, self.project))
        self.con.commit()  # never leave an implicit txn for shared writers
        return incident_id

    def resolve(self, incident_id: str, resolution: str,
                receipt: Optional[dict] = None) -> None:
        """05C §6: suspected_cause is NEVER copied into verified_cause.
        verified_cause is written only from an explicit, resolvable
        receipt (source, scope, observed_at, method, result). Service
        recovery may be verified while root cause stays unknown."""
        if receipt is not None:
            required = ("source", "scope", "observed_at", "method", "result")
            if not all(receipt.get(k) for k in required):
                raise RecoveryError(
                    "verification receipt incomplete — needs "
                    f"{required}")
        self.con.execute(
            "UPDATE incidents SET state='resolved', resolution=?, "
            "verified_cause=COALESCE(verified_cause, ?) "
            "WHERE incident_id=? AND project=?",
            (resolution,
             (receipt or {}).get("result") if receipt else None,
             incident_id, self.project))

    def open_incidents(self) -> list[dict]:
        rows = self.con.execute(
            "SELECT * FROM incidents WHERE state='open' AND project=? "
            "ORDER BY severity, last_seen DESC", (self.project,)).fetchall()
        cols = [d[0] for d in self.con.execute("SELECT * FROM incidents "
                                               "LIMIT 0").description]
        return [dict(zip(cols, r)) for r in rows]
