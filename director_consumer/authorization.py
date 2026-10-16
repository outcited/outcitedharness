"""Scoped authorization (§4) — verified approvals at the action boundary.

Hardening over 05:
  - approvals are FILE-BACKED: an operator-owned, non-world-writable
    approval file is the trusted proof (unix permissions = the established
    mechanism for a single-owner self-hosted box; no invented crypto)
  - a declared issuer string is never proof; missing approval file or
    wrong ownership/mode => REFUSED (and revocation = removing the file
    or flipping `revoked`)
  - one-message authorizations bind to the message's immutable digest
  - missing required scope fields fail closed per action class
  - nonce consumption is crash-safe and one-shot (see messaging.deliver)
  - the director can never issue or enlarge its own authority

Grant/revoke lifecycle (operator): write `approvals/<auth_id>.json`
(chmod 0640, owned by the operator) to grant; delete the file to revoke.
Ownership/mode are verified on every check.
"""
from __future__ import annotations

import json
import os
import sqlite3
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .schema import fp, utcnow

SCHEMA = """
CREATE TABLE IF NOT EXISTS authorizations (
    auth_id TEXT NOT NULL,
    project TEXT NOT NULL,
    objective_ref TEXT NOT NULL DEFAULT '',
    task_ref TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL,
    recipient TEXT NOT NULL DEFAULT '',
    spend_scope TEXT NOT NULL DEFAULT 'none',
    max_spend_usd REAL NOT NULL DEFAULT 0,
    expires_at REAL NOT NULL,
    issuer TEXT NOT NULL,
    issued_at REAL NOT NULL,
    payload_digest TEXT NOT NULL DEFAULT '',
    revoked INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (project, auth_id)
);
CREATE TABLE IF NOT EXISTS auth_nonces (
    nonce TEXT PRIMARY KEY,
    project TEXT NOT NULL DEFAULT 'default',
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
    payload_digest: str = ""


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
    payload_digest: str = ""
    revoked: bool = False


# per-action minimum scope: missing fields fail closed
REQUIRED_SCOPE = {
    "deliver": ("project", "recipient", "task_ref"),
    "reason": ("project",),
    "dispatch": ("project", "task_ref"),
    "spawn": ("project", "objective_ref"),
}


class AuthorizationStore:
    def __init__(self, con, approvals_dir: Optional[Path] = None,
                 operator_uid: Optional[int] = None):
        self.con = con
        self.approvals_dir = Path(approvals_dir) if approvals_dir else None
        self.operator_uid = operator_uid if operator_uid is not None \
            else os.getuid()
        con.executescript(SCHEMA)

    # ---- operator proof ----

    def _approval_ok(self, auth_id: str) -> tuple[bool, str]:
        """File-backed proof: operator-owned, not group/world writable."""
        if self.approvals_dir is None:
            return False, ("no approvals directory configured — "
                           "file-backed proof unavailable (fail closed)")
        f = self.approvals_dir / f"{auth_id}.json"
        try:
            st = os.stat(f)
        except OSError:
            return False, f"approval file missing: {f.name} (revoked?)"
        if st.st_uid != self.operator_uid:
            return False, (f"approval file owner uid {st.st_uid} != "
                           f"operator uid {self.operator_uid}")
        if st.st_mode & 0o022:
            return False, "approval file is group/world writable"
        return True, "operator approval file verified"

    # ---- issue / revoke ----

    def issue(self, record: AuthRecord) -> str:
        if record.issuer.startswith(("engineering-director", "director:")):
            raise AuthorizationError(
                "the director cannot authorize itself (§4/§10)")
        ok, why = self._approval_ok(record.auth_id)
        if not ok:
            raise AuthorizationError(f"issuance refused: {why}")
        self.con.execute(
            "INSERT OR REPLACE INTO authorizations(auth_id, project, "
            "objective_ref, task_ref, action, recipient, spend_scope, "
            "max_spend_usd, expires_at, issuer, issued_at, payload_digest, "
            "revoked) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (record.auth_id, record.project, record.objective_ref,
             record.task_ref, record.action, record.recipient,
             record.spend_scope, record.max_spend_usd, record.expires_at,
             record.issuer, utcnow(), record.payload_digest,
             1 if record.revoked else 0))
        self.con.commit()
        return record.auth_id

    def revoke(self, project: str, auth_id: str) -> None:
        self.con.execute(
            "UPDATE authorizations SET revoked=1 WHERE auth_id=? AND "
            "project=?", (auth_id, project))
        self.con.commit()

    # ---- enforcement ----

    def check(self, auth_id: str, request: AuthRequest) -> tuple[bool, str]:
        row = self.con.execute(
            "SELECT * FROM authorizations WHERE auth_id=?",
            (auth_id,)).fetchone()
        if row is None:
            return False, "no such authorization (default deny)"
        cols = [d[0] for d in self.con.execute(
            "SELECT * FROM authorizations LIMIT 0").description]
        r = dict(zip(cols, row))
        if r["revoked"]:
            return False, "authorization revoked"
        ok, why = self._approval_ok(auth_id)
        if not ok:
            return False, f"approval proof failed: {why}"
        now = utcnow()
        if r["expires_at"] < now:
            return False, "authorization expired"
        if r["project"] != request.project:
            return False, "project mismatch"
        if r["action"] != request.action:
            return False, "action mismatch"
        if r["recipient"] and r["recipient"] != request.recipient:
            return False, "recipient mismatch"
        # fail-closed required scope for the action class
        for fld in REQUIRED_SCOPE.get(r["action"], ()):
            if not getattr(request, fld, "") or not r.get(fld, ""):
                return False, f"missing required scope field {fld!r}"
        if r["task_ref"] and request.task_ref and \
                r["task_ref"] != request.task_ref:
            return False, "task_ref mismatch"
        if r["objective_ref"] and request.objective_ref and \
                r["objective_ref"] != request.objective_ref:
            return False, "objective_ref mismatch"
        if request.spend_usd > r["max_spend_usd"]:
            return False, (f"spend {request.spend_usd} exceeds scope "
                           f"{r['max_spend_usd']}")
        if request.provider and r["spend_scope"] == "none" and \
                request.provider.lower() in {"openrouter", "metered"}:
            return False, "METERED REFUSED: spend_scope=none"
        if r["payload_digest"] and request.payload_digest != r["payload_digest"]:
            return False, "payload digest mismatch (approval is bound to " \
                          "a different message)"
        if request.nonce:
            try:
                self.con.execute(
                    "INSERT INTO auth_nonces(nonce, project, auth_id, "
                    "used_at) VALUES (?,?,?,?)",
                    (request.nonce, request.project, auth_id, now))
                self.con.commit()
            except sqlite3.IntegrityError:
                return False, "replay rejected (nonce already used)"
        return True, f"authorized by operator-approved record ({r['issuer']})"
