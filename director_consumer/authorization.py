"""Scoped authorization contract (§6) — default-deny at the action boundary.

A nonempty authorization_ref string is NEVER sufficient. An action passes
only when a durable approval record matches EVERY scoped dimension of the
actual request:

  project | objective_ref | task_ref | action | recipient | spend_scope
  | expiration | issuer

Replayed (nonce-used), expired, mismatched, or unknown records are
rejected. Enforcement lives in the call path (deliver/dispatch), not in
prompts.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional

from .schema import fp, utcnow

SCHEMA = """
CREATE TABLE IF NOT EXISTS authorizations (
    auth_id TEXT PRIMARY KEY,
    project TEXT NOT NULL,
    objective_ref TEXT NOT NULL DEFAULT '',
    task_ref TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL,
    recipient TEXT NOT NULL DEFAULT '',
    spend_scope TEXT NOT NULL DEFAULT 'none',
    max_spend_usd REAL NOT NULL DEFAULT 0,
    expires_at REAL NOT NULL,
    issuer TEXT NOT NULL,
    issued_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS auth_nonces (
    nonce TEXT PRIMARY KEY,
    auth_id TEXT NOT NULL,
    used_at REAL NOT NULL
);
"""


class AuthorizationError(Exception):
    pass


@dataclass
class AuthRequest:
    project: str
    action: str
    recipient: str = ""
    task_ref: str = ""
    objective_ref: str = ""
    spend_usd: float = 0.0
    nonce: str = ""
    provider: str = ""


@dataclass
class AuthRecord:
    auth_id: str
    project: str
    action: str
    recipient: str = ""
    task_ref: str = ""
    objective_ref: str = ""
    spend_scope: str = "none"
    max_spend_usd: float = 0.0
    expires_at: float = 0.0
    issuer: str = ""


class AuthorizationStore:
    def __init__(self, con):
        self.con = con
        con.executescript(SCHEMA)

    def issue(self, record: AuthRecord) -> str:
        """Issued by a HUMAN authority — the caller supplies issuer; the
        store never self-issues (director-issued rows are invalid)."""
        if record.issuer.startswith(("engineering-director", "director:")):
            raise AuthorizationError(
                "the director cannot authorize itself (§6/§9)")
        self.con.execute(
            "INSERT OR REPLACE INTO authorizations(auth_id, project, "
            "objective_ref, task_ref, action, recipient, spend_scope, "
            "max_spend_usd, expires_at, issuer, issued_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (record.auth_id, record.project, record.objective_ref,
             record.task_ref, record.action, record.recipient,
             record.spend_scope, record.max_spend_usd, record.expires_at,
             record.issuer, utcnow()))
        self.con.commit()
        return record.auth_id

    def check(self, auth_id: str, request: AuthRequest) -> tuple[bool, str]:
        """Default-deny. Validates the record against the ACTUAL request."""
        row = self.con.execute(
            "SELECT * FROM authorizations WHERE auth_id=?",
            (auth_id,)).fetchone()
        if row is None:
            return False, "no such authorization (default deny)"
        cols = [d[0] for d in self.con.execute(
            "SELECT * FROM authorizations LIMIT 0").description]
        r = dict(zip(cols, row))
        now = utcnow()
        if r["expires_at"] < now:
            return False, f"authorization expired at {r['expires_at']}"
        if r["project"] != request.project:
            return False, (f"project mismatch: authorized {r['project']} "
                           f"vs requested {request.project}")
        if r["action"] != request.action:
            return False, (f"action mismatch: authorized {r['action']} "
                           f"vs requested {request.action}")
        if r["recipient"] and r["recipient"] != request.recipient:
            return False, (f"recipient mismatch: authorized "
                           f"{r['recipient']} vs requested "
                           f"{request.recipient}")
        if r["task_ref"] and request.task_ref and \
                r["task_ref"] != request.task_ref:
            return False, "task_ref mismatch"
        if request.spend_usd > r["max_spend_usd"]:
            return False, (f"spend {request.spend_usd} exceeds scope "
                           f"{r['max_spend_usd']}")
        if request.provider and r["spend_scope"] == "none" and \
                request.provider.lower() in {"openrouter", "metered"}:
            return False, "METERED REFUSED: spend_scope=none"
        if request.nonce:
            used = self.con.execute(
                "SELECT 1 FROM auth_nonces WHERE nonce=?",
                (request.nonce,)).fetchone()
            if used:
                return False, "replay rejected (nonce already used)"
            self.con.execute(
                "INSERT INTO auth_nonces(nonce, auth_id, used_at) "
                "VALUES (?,?,?)", (request.nonce, auth_id, now))
            self.con.commit()
        return True, f"authorized by {r['issuer']}"

    @staticmethod
    def derive_id(record: dict) -> str:
        return fp("auth", record.get("project"), record.get("action"),
                  record.get("recipient"), record.get("task_ref"),
                  record.get("issuer"))[:16]
