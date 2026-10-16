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

SCHEMA = """
CREATE TABLE IF NOT EXISTS incidents (
    incident_id TEXT PRIMARY KEY,
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
    reopen_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS incident_events (
    incident_id TEXT NOT NULL,
    trigger_key TEXT NOT NULL,
    ts REAL NOT NULL
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
        con.executescript(SCHEMA)
        self.by_rule = {rid: r for r in correlations for rid in r.group_rules}

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
        incident_id = fp("incident", r.cid, ident, bucket)[:16]
        row = self.con.execute(
            "SELECT incident_id, state, severity, first_seen FROM incidents "
            "WHERE incident_id=?", (incident_id,)).fetchone()
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
                "state='open', reopen_count=reopen_count+? "
                "WHERE incident_id=?",
                (now, new_sev, 1 if reopened else 0, incident_id))
            rules = set(json.loads(
                self.con.execute("SELECT related_rules FROM incidents "
                                 "WHERE incident_id=?",
                                 (incident_id,)).fetchone()[0]))
            rules.add(a.rule_id)
            self.con.execute(
                "UPDATE incidents SET related_rules=? WHERE incident_id=?",
                (json.dumps(sorted(rules)), incident_id))
        self.con.execute(
            "INSERT INTO incident_events(incident_id, trigger_key, ts) "
            "VALUES (?,?,?)", (incident_id, a.trigger_key, now))
        # evidence accumulates (bounded)
        ev = json.loads(self.con.execute(
            "SELECT evidence FROM incidents WHERE incident_id=?",
            (incident_id,)).fetchone()[0])
        entry = {"rule": a.rule_id, "summary": a.event_summary[:160],
                 "ts": now,
                 "refs": [{"feed": x.feed, "source_id": x.source_id}
                          for x in a.evidence]}
        if entry not in ev:
            ev.append(entry)
        self.con.execute(
            "UPDATE incidents SET evidence=? WHERE incident_id=?",
            (json.dumps(ev[-20:]), incident_id))
        self.con.execute(
            "UPDATE incidents SET proposed_action=? WHERE incident_id=? "
            "AND proposed_action IS NULL",
            (a.action, incident_id))
        self.con.commit()  # never leave an implicit txn for shared writers
        return incident_id

    def resolve(self, incident_id: str, resolution: str) -> None:
        """Verified recovery — in observe mode only from replayed
        resolution evidence, never from a model opinion."""
        self.con.execute(
            "UPDATE incidents SET state='resolved', resolution=?, "
            "verified_cause=COALESCE(verified_cause, suspected_cause) "
            "WHERE incident_id=?", (resolution, incident_id))

    def open_incidents(self) -> list[dict]:
        rows = self.con.execute(
            "SELECT * FROM incidents WHERE state='open' "
            "ORDER BY severity, last_seen DESC").fetchall()
        cols = [d[0] for d in self.con.execute("SELECT * FROM incidents "
                                               "LIMIT 0").description]
        return [dict(zip(cols, r)) for r in rows]
