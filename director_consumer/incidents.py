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
import os
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
    verified_outcome TEXT,
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
        if "verified_outcome" not in cols:
            con.execute("ALTER TABLE incidents ADD COLUMN verified_outcome "
                        "TEXT")
        if "occurrence" not in cols:
            con.execute("ALTER TABLE incidents ADD COLUMN occurrence "
                        "INTEGER NOT NULL DEFAULT 1")
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
            # a kind string alone is not proof — receipts must validate
            good = False
            for kind, rjson in rows:
                if kind not in ("root-cause-fixed", "verified-fresh-run"):
                    continue
                try:
                    rec = json.loads(rjson) if rjson else {}
                except json.JSONDecodeError:
                    rec = {}
                if rec and self.validate_receipt(incident_id, rec)[0]:
                    good = True
            if not good:
                raise RecoveryError(
                    "recovery_verified requires validated root-cause-fixed "
                    "or verified-fresh-run receipts from the CURRENT "
                    "occurrence — kinds, stale evidence and cleared "
                    "symptoms are not verified recovery")
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

    def repeat_status(self, incident_id: str) -> tuple[bool, str]:
        """(is_repeat, severity) from evidence, not clocks: observe() logs
        one incident_events row per occurrence, so count >= 2 means this
        occurrence is a repeat on the SAME identity+rule+window incident
        (doctrine §4: deterministic relationship, never temporal alone)."""
        row = self.con.execute(
            "SELECT severity FROM incidents WHERE incident_id=? "
            "AND project=?", (incident_id, self.project)).fetchone()
        if row is None:
            return False, ""
        n = self.con.execute(
            "SELECT count(*) FROM incident_events WHERE incident_id=? "
            "AND project=?", (incident_id, self.project)).fetchone()[0]
        return (int(n) >= 2), str(row[0])

    def validate_receipt(self, incident_id: str,
                         receipt: dict) -> tuple[bool, str]:
        """A receipt is a resolvable VERIFICATION, not five nonempty
        strings: scope must reference this incident, observed_at must
        parse and be fresh, result and method must be substantive."""
        required = ("source", "scope", "observed_at", "method", "result")
        missing = [k for k in required if not receipt.get(k)]
        if missing:
            return False, f"incomplete receipt: missing {missing}"
        if incident_id not in str(receipt.get("scope", "")):
            return False, "receipt scope does not reference this incident"
        try:
            from datetime import datetime, timezone
            ts = datetime.fromisoformat(
                str(receipt["observed_at"]).replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            age = utcnow() - ts.timestamp()
        except ValueError:
            return False, "receipt observed_at is not a valid timestamp"
        if age < -3600 or age > 86400 * 2:
            return False, (f"receipt observed_at is stale/future "
                           f"({age/3600:.1f}h) — not current evidence")
        if len(str(receipt["result"])) < 2 or len(str(receipt["method"])) < 2:
            return False, "receipt method/result are not substantive"
        # closure §4: format != outcome. A well-formed FAILURE result
        # never verifies recovery.
        res = str(receipt["result"]).lower()
        if any(bad in res for bad in ("failed", "still broken", "unhealthy",
                                      "not recovered", "regressed")):
            return False, (f"receipt result is a FAILURE "
                           f"({receipt['result']!r}) — outcomes must "
                           f"succeed to verify")
        # a path-like source must RESOLVE — an unresolvable source is not
        # proof of anything
        src = str(receipt.get("source", ""))
        looks_path = src.startswith(("/", "./", "../")) or any(
            src.endswith(ext) for ext in (".json", ".jsonl", ".db", ".md",
                                          ".txt", ".log"))
        if looks_path and not os.path.exists(src):
            return False, f"receipt source does not resolve: {src}"
        return True, "receipt valid"

    def resolve(self, incident_id: str, resolution: str,
                receipt: Optional[dict] = None) -> None:
        """closure §4: resolve records a verified OUTCOME only. A generic
        observation result is NEVER stored as a verified causal
        explanation; suspected_cause is never promoted."""
        if receipt is None:
            raise RecoveryError(
                "resolve() requires a verification receipt — an incident "
                "without evidence is never removed from the open view")
        ok, why = self.validate_receipt(incident_id, receipt)
        if not ok:
            raise RecoveryError(f"receipt rejected: {why}")
        self.con.execute(
            "UPDATE incidents SET state='resolved', resolution=?, "
            "verified_outcome=? WHERE incident_id=? AND project=?",
            (resolution, receipt.get("result"), incident_id, self.project))

    def establish_cause(self, incident_id: str, receipt: dict) -> None:
        """closure §4: a CAUSAL claim needs its own explicit receipt
        (kind: root-cause / causal: true + a causal method). Recovery may
        be verified while the root cause stays unknown — and a generic
        observation can never become a cause."""
        if not (receipt.get("causal") or receipt.get("kind") == "root-cause"):
            raise RecoveryError(
                "causal conclusion requires an explicit causal receipt "
                "(kind=root-cause) — observations are not causes")
        ok, why = self.validate_receipt(incident_id, receipt)
        if not ok:
            raise RecoveryError(f"receipt rejected: {why}")
        if len(str(receipt.get("result", ""))) < 8:
            raise RecoveryError("causal result is not substantive")
        self.con.execute(
            "UPDATE incidents SET verified_cause=? WHERE incident_id=? "
            "AND project=?",
            (receipt.get("result"), incident_id, self.project))
        self.con.commit()

    def open_incidents(self) -> list[dict]:
        rows = self.con.execute(
            "SELECT * FROM incidents WHERE state='open' AND project=? "
            "ORDER BY severity, last_seen DESC", (self.project,)).fetchall()
        cols = [d[0] for d in self.con.execute("SELECT * FROM incidents "
                                               "LIMIT 0").description]
        return [dict(zip(cols, r)) for r in rows]
