"""Scoped authorization (§4/§5B) — operator-file trust boundary.

TRUST MODEL (P0-2): the approval artifact is an operator-owned file whose
CONTENT defines the complete approved scope. The service identity (the
director's uid) must NOT be able to write it:

  - auth_id is a strict token (no path traversal) and the file is opened
    O_NOFOLLOW inside the approvals directory (no symlink escapes)
  - file uid must equal the configured OPERATOR uid and must differ from
    the SERVICE uid; same identities = no trust boundary = refuse
  - approvals dir and file must not be group/world writable
  - an empty {} file is NOT approval — the file must carry the full scope
    (project, action, recipient, task_ref, objective_ref, expires_at,
    spend_scope, max_spend_usd, issuer, payload_digest) and the file's
    values are what gets enforced (DB row is a validated cache)
  - revocation = deleting the file (or the revoked flag); re-checks
    re-read the file every time

NO INVENTED CRYPTOGRAPHY: unix ownership/permissions are the established
mechanism for a single-owner self-hosted host; the human operator keeps
the approvals directory write-protected from the service account.

NONCE LAW (P0-3): a nonce may be REUSED only to retry the identical
action (same payload digest); any other reuse is a replay and refused.
"""
from __future__ import annotations

import json
import os
import re
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
    payload_digest TEXT NOT NULL DEFAULT '',
    used_at REAL NOT NULL
);
"""

SAFE_ID = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")
REQUIRED_SCOPE = {
    "deliver": ("project", "recipient", "task_ref"),
    "reason": ("project",),
    "dispatch": ("project", "task_ref"),
    "spawn": ("project", "objective_ref"),
}
# the approval FILE must carry these fields — a partial/empty file is not
# authenticated human approval. Scope fields are action-scoped (a
# reason-only approval does not carry a recipient).
FILE_BASE_FIELDS = ("project", "action", "expires_at", "issuer",
                    "payload_digest")


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
    # full immutable action identity (05C §2): every field is checked;
    # nothing mutable may ride an old approval
    action_id: str = ""
    msg_type: str = ""
    subject: str = ""
    transport: str = ""
    assignment_epoch: int = 0
    sender: str = ""


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


@dataclass
class ActionEnvelope:
    """Canonical versioned action envelope (05C §3). One approval binds
    EVERY consequential field — body-only approval is not a thing."""
    envelope_version: str = "1"
    project: str = ""
    objective_ref: str = ""
    task_ref: str = ""
    incident_ref: str = ""
    assignment_epoch: int = 0
    action_id: str = ""
    sender: str = ""
    recipient: str = ""
    session_binding: str = ""
    msg_type: str = ""
    subject: str = ""
    body_digest: str = ""
    transport: str = ""
    expires_at: float = 0.0
    spend_scope: str = "none"
    max_actions: int = 1
    approval_id: str = ""
    issuer: str = ""


class AuthorizationStore:
    def __init__(self, con, approvals_dir: Optional[Path] = None,
                 operator_uid: Optional[int] = None,
                 service_uid: Optional[int] = None,
                 test_identities: bool = False):
        self.con = con
        self.approvals_dir = Path(approvals_dir) if approvals_dir else None
        # 05C §3: service identity comes from the ACTUAL process; the
        # operator identity comes from protected deployment config.
        # Caller-supplied uid values are honored ONLY when explicitly
        # labeled as test doubles — never as production proof.
        self.test_identities = test_identities
        if test_identities:
            self.service_uid = service_uid if service_uid is not None \
                else os.getuid()
            self.operator_uid = operator_uid if operator_uid is not None \
                else os.getuid()
        else:
            self.service_uid = os.getuid()     # the real running process
            self.operator_uid = operator_uid if operator_uid is not None \
                else -1                        # unset operator: fail closed
        con.executescript(SCHEMA)

    # ---- protected artifact access ----

    def _read_artifact(self, auth_id: str) -> tuple[Optional[dict], str]:
        if not SAFE_ID.match(auth_id or ""):
            return None, "invalid auth id (path traversal refused)"
        if self.approvals_dir is None:
            return None, "no approvals directory configured (fail closed)"
        if self.operator_uid == self.service_uid:
            return None, ("operator uid equals service uid — no trust "
                          "boundary (refuse)")
        base = os.path.realpath(self.approvals_dir)
        target = os.path.join(base, f"{auth_id}.json")
        if os.path.dirname(os.path.realpath(target)) != base:
            return None, "approval path escapes approvals directory"
        try:
            fd = os.open(target, os.O_RDONLY | os.O_NOFOLLOW)
        except OSError:
            return None, f"approval file missing or symlink: {auth_id}"
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                return None, "approval artifact is not a regular file"
            if st.st_uid != self.operator_uid:
                return None, (f"approval owner uid {st.st_uid} != operator "
                              f"{self.operator_uid}")
            if st.st_mode & 0o022:
                return None, "approval file is group/world writable"
            dmode = os.stat(base).st_mode
            if dmode & 0o022:
                return None, "approvals directory is group/world writable"
            raw = os.read(fd, 1 << 20)
        finally:
            os.close(fd)
        try:
            data = json.loads(raw.decode())
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None, "approval artifact is not valid JSON"
        if not isinstance(data, dict):
            return None, "approval artifact must be a JSON object"
        required = FILE_BASE_FIELDS + REQUIRED_SCOPE.get(
            data.get("action", ""), ())
        missing = [f for f in required if not data.get(f)]
        if missing:
            return None, (f"approval artifact incomplete — missing "
                          f"{missing} (an empty or partial file is NOT "
                          f"human approval)")
        return data, "operator approval artifact verified"

    # ---- issue / revoke ----

    def issue(self, record: AuthRecord) -> str:
        if record.issuer.startswith(("engineering-director", "director:")):
            raise AuthorizationError(
                "the director cannot authorize itself (P0-2)")
        data, why = self._read_artifact(record.auth_id)
        if data is None:
            raise AuthorizationError(f"issuance refused: {why}")
        # the FILE is authority: enforce its values, not caller claims.
        # Expiry is enforced at CHECK time — an old artifact simply cannot
        # authorize anything anymore.
        for f in ("project", "action", "recipient"):
            if data.get(f) != getattr(record, f):
                raise AuthorizationError(
                    f"record disagrees with approval artifact on {f!r}")
        self.con.execute(
            "INSERT OR REPLACE INTO authorizations(auth_id, project, "
            "objective_ref, task_ref, action, recipient, spend_scope, "
            "max_spend_usd, expires_at, issuer, issued_at, payload_digest, "
            "revoked) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (record.auth_id, data.get("project", record.project),
             data.get("objective_ref", record.objective_ref),
             data.get("task_ref", record.task_ref),
             data.get("action", record.action),
             data.get("recipient", record.recipient),
             data.get("spend_scope", record.spend_scope),
             float(data.get("max_spend_usd", record.max_spend_usd)),
             float(data.get("expires_at", record.expires_at)),
             data.get("issuer", record.issuer), utcnow(),
             data.get("payload_digest", record.payload_digest),
             1 if record.revoked else 0))
        self.con.commit()
        return record.auth_id

    def reserve(self, cur, auth_id: str, request: AuthRequest) -> tuple[bool, str]:
        """D2 fix: grant reservation INSIDE the caller's transaction.
        The nonce row and any state change commit (or roll back) together
        with the delivery intent — a crash cannot consume a grant without
        leaving a resumable action, and cannot leave intent without a
        validated grant."""
        ok, why = self.check(auth_id, request, consume=False)
        if not ok:
            return False, why
        if request.nonce:
            prev = cur.execute(
                "SELECT payload_digest FROM auth_nonces WHERE nonce=?",
                (request.nonce,)).fetchone()
            if prev is not None:
                if prev[0] == request.payload_digest:
                    # SAME action being retried or raced: idempotent
                    # resume, never a second action
                    return True, "already-reserved (same action)"
                return False, ("replay rejected (nonce already used for a "
                               "different action)")
            cur.execute(
                "INSERT INTO auth_nonces(nonce, project, auth_id, "
                "payload_digest, used_at) VALUES (?,?,?,?,?)",
                (request.nonce, request.project, auth_id,
                 request.payload_digest, utcnow()))
        return True, "reserved"

    def revoke(self, project: str, auth_id: str) -> None:
        self.con.execute(
            "UPDATE authorizations SET revoked=1 WHERE auth_id=? AND "
            "project=?", (auth_id, project))
        self.con.commit()

    # ---- enforcement ----

    def check(self, auth_id: str, request: AuthRequest,
              consume: bool = True) -> tuple[bool, str]:
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
        data, why = self._read_artifact(auth_id)   # re-read EVERY check
        if data is None:
            return False, f"approval proof failed: {why}"
        now = utcnow()
        if float(data.get("expires_at", 0)) < now or r["expires_at"] < now:
            return False, "authorization expired"
        if data.get("project") != request.project or \
                r["project"] != request.project:
            return False, "project mismatch"
        if data.get("action") != request.action or r["action"] != request.action:
            return False, "action mismatch"
        if data.get("recipient") != request.recipient:
            return False, "recipient mismatch"
        for fld in REQUIRED_SCOPE.get(request.action, ()):
            if not getattr(request, fld, "") or not (
                    r.get(fld) or data.get(fld)):
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
        if data.get("payload_digest") and \
                request.payload_digest != data["payload_digest"]:
            return False, "payload digest mismatch (approval bound to a " \
                          "different message)"
        # 05C §2: an approval binds the WHOLE action. Identical bodies
        # with a different subject/type/destination/task/epoch/id can
        # never ride the same grant.
        for fld, label in (("msg_type", "message type"),
                           ("subject", "subject"),
                           ("transport", "transport/destination"),
                           ("action_id", "action id"),
                           ("sender", "sender")):
            want = data.get(fld)
            if want not in (None, ""):
                got = getattr(request, fld, "")
                if got != want:
                    return False, f"{label} mismatch (approval bound to a " \
                                  f"different action)"
        if data.get("assignment_epoch") not in (None, "", 0):
            if int(data["assignment_epoch"]) != request.assignment_epoch:
                return False, "assignment epoch mismatch"
        if request.nonce and consume:
            prev = self.con.execute(
                "SELECT payload_digest FROM auth_nonces WHERE nonce=?",
                (request.nonce,)).fetchone()
            if prev is not None:
                return False, "replay rejected (nonce already used)"
            try:
                self.con.execute(
                    "INSERT INTO auth_nonces(nonce, project, auth_id, "
                    "payload_digest, used_at) VALUES (?,?,?,?,?)",
                    (request.nonce, request.project, auth_id,
                     request.payload_digest, now))
                self.con.commit()
            except sqlite3.IntegrityError:
                return False, "replay rejected (nonce already used)"
        return True, f"authorized by operator artifact ({data.get('issuer')})"
