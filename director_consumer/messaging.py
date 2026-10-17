"""Controlled agentmail delivery + acknowledgment (§3).

Laws:
  - delivery is NEVER acknowledgment; acknowledgment is NEVER completion
  - production delivery is gated: `delivery.enabled` must be explicitly
    true AND an authorization_ref present; default is disabled (observe)
  - message ids are idempotent; filenames derive from message_id so
    duplicate sends rewrite the same artifact (no dup mailboxes)
  - bounded retries, expiry -> escalation draft, mailbox reconciliation
    after network failure, full audit trail (message_events)

Mailbox layout (existing grammar, isolated in tests):
  <outbox_root>/<recipient>/<ts>_<from>_<to>_<type>_<subject>.md
Inbound acks are discovered as files with an `ack_of: <message_id>` header.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .schema import fp, utcnow
from .scope import ensure_scoped
from .acceptance import scoped_receipt, validate_acceptance
from .authorization import AuthRequest, AuthorizationStore

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    message_id TEXT NOT NULL,
    project TEXT NOT NULL DEFAULT 'default',
    correlation_id TEXT NOT NULL DEFAULT '',
    recipient TEXT NOT NULL,
    msg_type TEXT NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'proposed',
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    sent_at REAL,
    acked_at REAL,
    expires_at REAL,
    authorization_ref TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (project, message_id)
);
CREATE TABLE IF NOT EXISTS message_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT NOT NULL,
    project TEXT NOT NULL DEFAULT 'default',
    ts REAL NOT NULL,
    event TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_msg_state ON messages(state);
CREATE TABLE IF NOT EXISTS action_envelopes (
    action_id TEXT NOT NULL,
    project TEXT NOT NULL,
    envelope_json TEXT NOT NULL,
    body_digest TEXT NOT NULL,
    auth_id TEXT NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (project, action_id)
);
CREATE TABLE IF NOT EXISTS invalid_receipts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT NOT NULL,
    project TEXT NOT NULL,
    reason TEXT NOT NULL,
    payload TEXT NOT NULL,
    ts REAL NOT NULL
);
"""


def _auth_request(project: str, m: dict, body_digest: str,
                  message_id: str, sender: str, transport: str,
                  assignment_epoch: int):
    from .authorization import AuthRequest
    return AuthRequest(project=project, action="deliver",
                       recipient=m["recipient"],
                       task_ref=m.get("correlation_id", ""),
                       nonce=f"deliver:{message_id}",
                       payload_digest=body_digest,
                       action_id=message_id,
                       msg_type=m.get("msg_type", ""),
                       subject=m.get("subject", ""),
                       transport=transport,
                       assignment_epoch=assignment_epoch,
                       sender=sender)


class OpenCodeSessionTransport:
    """PRODUCTION adapter for OpenCode session origin.

    Assurance level (honest): origin is resolved from an OPERATOR-PROTECTED
    sessions binding (artifact -> session token registered at session
    start) plus existence of the session in the local OpenCode metadata
    store. The installed opencode.db schema carries NO signed session
    tokens (inspected 2026-10), so cryptographic origin binding is a
    SOFTWARE GAP — this adapter is the strongest available until OpenCode
    exposes authenticated session receipts. It is NOT the test double
    (SessionTokenTransport) and must not be confused with it.
    """

    def __init__(self, bindings_file=None, opencode_db=None):
        self.bindings_file = bindings_file
        self.opencode_db = opencode_db

    def verify_origin(self, artifact_path):
        if not self.bindings_file:
            return None
        try:
            data = json.loads(Path(self.bindings_file).read_text())
        except (OSError, json.JSONDecodeError):
            return None
        session = data.get(str(artifact_path))
        if not session:
            return None
        return session   # existence in bindings is the interim proof


class SessionTokenTransport:
    """Trusted adapter: establishes ORIGIN from a session token embedded
    in the artifact and registered with the transport at session start.
    Caller-declared session text is never origin proof (05C §3)."""

    def __init__(self, tokens: dict | None = None):
        self.tokens = tokens or {}   # artifact path -> true origin session

    def verify_origin(self, artifact_path):
        return self.tokens.get(str(artifact_path))

MAX_ATTEMPTS = 5
DEFAULT_TTL_S = 7 * 86400


class DeliveryDisabled(Exception):
    pass


@dataclass
class DeliveryPolicy:
    enabled: bool = False
    authorization_ref: str = ""
    outbox_root: Optional[Path] = None
    # NAS/production transports must PRE-EXIST: a missing mount must never
    # be replaced by a locally-created impostor inbox. Isolated test
    # destinations are legitimately fresh.
    require_existing_root: bool = False

    @classmethod
    def load(cls, path: Optional[Path], outbox_root: Optional[Path]):
        """delivery: {enabled: bool, authorization_ref: str} from project
        policy. Without BOTH, delivery refuses."""
        enabled, ref, req = False, "", False
        if path and Path(path).exists():
            import yaml
            data = yaml.safe_load(Path(path).read_text()) or {}
            d = data.get("delivery", {})
            enabled = bool(d.get("enabled", False))
            ref = str(d.get("authorization_ref", ""))
            req = bool(d.get("require_existing_root", False))
        return cls(enabled=enabled, authorization_ref=ref,
                   outbox_root=outbox_root, require_existing_root=req)


class MailboxDelivery:
    def __init__(self, con, policy: DeliveryPolicy,
                 sender: str = "engineering-director",
                 project: str = "default",
                 authorizations: AuthorizationStore | None = None,
                 transport=None,
                 mode: str = "observe",
                 transport_name: str = "isolated-test-transport"):
        self.con = con
        self.policy = policy
        self.sender = sender
        self.project = project
        self.authorizations = authorizations
        if authorizations is not None and \
                getattr(authorizations, "con", None) is not con:
            raise ValueError(
                "single-transaction law: AuthorizationStore and "
                "MailboxDelivery must share one sqlite connection")
        self.transport = transport
        self.mode = mode            # observe | test-delivery
        self.transport_name = transport_name
        ensure_scoped(con)
        con.executescript(SCHEMA)

    def _event(self, mid: str, event: str, detail: str = "") -> None:
        self.con.execute(
            "INSERT INTO message_events(message_id, project, ts, event, "
            "detail) VALUES (?,?,?,?,?)",
            (mid, self.project, utcnow(), event, detail))

    def propose(self, recipient: str, msg_type: str, subject: str, body: str,
                correlation_id: str = "", ttl_s: float = DEFAULT_TTL_S,
                message_id: Optional[str] = None,
                assignment_epoch: int = 0) -> str:
        # 05C §2: identity covers the whole action — project/type/
        # recipient/subject/body/task/epoch — so no field can piggyback
        # an old id or an old grant
        mid = message_id or fp("msg", self.project, recipient, msg_type,
                               subject, body, correlation_id,
                               assignment_epoch)[:16]
        self.con.execute(
            "INSERT OR IGNORE INTO messages(message_id, project, "
            "correlation_id, recipient, msg_type, subject, body, state, "
            "created_at, expires_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (mid, self.project, correlation_id, recipient, msg_type,
             subject, body, 'proposed', utcnow(), utcnow() + ttl_s))
        self._event(mid, "proposed")
        self.con.commit()
        return mid

    def _authorize(self, message_id: str, auth_id: str,
                   body: str, recipient: str) -> None:
        # task scope comes from the message's correlation_id (one-message
        # authorizations bind project/task/recipient/body digest)
        """One action boundary. With a store present, only scoped,
        operator-file-backed approvals pass. Crash-safe: nonce = the
        message id itself (one action id, never two consumptions)."""
        if self.authorizations is None:
            self._event(message_id, "delivery_refused",
                        "no authorization service attached (fail closed)")
            self.con.commit()
            raise DeliveryDisabled(
                "no authorization service — delivery refused (fail closed)")
        row = self.con.execute(
            "SELECT correlation_id FROM messages WHERE message_id=? "
            "AND project=?", (message_id, self.project)).fetchone()
        ok, why = self.authorizations.check(auth_id, AuthRequest(
            project=self.project, action="deliver", recipient=recipient,
            task_ref=(row[0] if row else ""), nonce=f"deliver:{message_id}",
            payload_digest=fp("msg-body", body)))
        if not ok:
            self._event(message_id, "delivery_refused", why)
            self.con.commit()
            raise DeliveryDisabled(f"authorization refused: {why}")

    def deliver(self, message_id: str, auth_id: str = "",
                assignment_epoch: int = 0) -> str:
        """05C §4: ONE transaction owns validate + reserve + envelope +
        intent + audit; the external write happens after commit against
        the PERSISTED envelope (never regenerated). Crash semantics:
        before commit = nothing consumed, retry allowed; after commit =
        resumable without re-approval."""
        if self.mode == "observe":
            self._event(message_id, "delivery_refused",
                        "observation mode prohibits external effects")
            self.con.commit()
            raise DeliveryDisabled(
                "observation mode: external effects prohibited "
                "(even with a valid approval)")
        row = self.con.execute(
            "SELECT * FROM messages WHERE message_id=? AND project=?",
            (message_id, self.project)).fetchone()
        if row is None:
            raise KeyError(message_id)
        cols = [d[0] for d in self.con.execute(
            "SELECT * FROM messages LIMIT 0").description]
        m = dict(zip(cols, row))
        if m["state"] in ("sent", "acked", "ack-unverified", "expired",
                          "escalated"):
            return m["state"]          # duplicate-send protection
        if m["attempts"] >= MAX_ATTEMPTS and m["state"] != "delivering":
            self.con.execute(
                "UPDATE messages SET state='escalated' WHERE message_id=? "
                "AND project=?", (message_id, self.project))
            self._event(message_id, "escalated",
                        f"attempts {m['attempts']} >= {MAX_ATTEMPTS}")
            self.con.commit()
            return "escalated"
        body_digest = fp("msg-body", m["body"])
        resuming = (m["state"] == "delivering")
        # ---- single transaction boundary ----
        self.con.execute("BEGIN IMMEDIATE")
        try:
            if not resuming:
                if self.authorizations is None:
                    raise DeliveryDisabled(
                        "no authorization service — fail closed")
                ok, why = self.authorizations.reserve(self.con, auth_id,
                    _auth_request(self.project, m, body_digest, message_id,
                                  self.sender, self.transport_name,
                                  assignment_epoch))
                if not ok:
                    self.con.execute("ROLLBACK")  # nothing consumed
                    self._event(message_id, "delivery_refused", why)
                    self.con.commit()
                    raise DeliveryDisabled(f"authorization refused: {why}")
                # validated + reserved + envelope + intent commit together
                # immutable action envelope persisted with the intent
                dest_root = str(Path(self.policy.outbox_root)
                                / m["recipient"]) if self.policy.outbox_root \
                    else ""
                self.con.execute(
                    "INSERT OR REPLACE INTO action_envelopes("
                    "action_id, project, envelope_json, body_digest, "
                    "auth_id, created_at) VALUES (?,?,?,?,?,?)",
                    (message_id, self.project, json.dumps({
                        "envelope_version": "1", "project": self.project,
                        "action_id": message_id, "sender": self.sender,
                        "recipient": m["recipient"],
                        "task_ref": m.get("correlation_id", ""),
                        "msg_type": m["msg_type"], "subject": m["subject"],
                        "body_digest": body_digest,
                        "body": m["body"],
                        "destination": dest_root,
                        "transport": self.transport_name,
                        "assignment_epoch": assignment_epoch,
                        "max_actions": 1,
                        "approval_id": auth_id}), body_digest, auth_id,
                     utcnow()))
            # CAS transition: only one racer wins the intent
            cur = self.con.execute(
                "UPDATE messages SET state='delivering', "
                "attempts=attempts+1 WHERE message_id=? AND project=? "
                "AND state IN ('proposed','delivering')",
                (message_id, self.project))
            self._event(message_id, "intent", f"auth={auth_id or 'resume'}")
            self.con.execute("COMMIT")
        except DeliveryDisabled:
            raise          # refusal branch already rolled back + audited
        except Exception:
            try:
                self.con.execute("ROLLBACK")
            except sqlite3.OperationalError:
                pass
            raise
        if self.mode != "test-delivery":
            raise DeliveryDisabled(
                "external effects require test-delivery or approved mode")
        # ---- external effect driven by the PERSISTED approved envelope ----
        env = self._envelope(message_id)
        if not env:
            raise DeliveryDisabled("no persisted envelope — refuse to act")
        # closure §5: revocation/expiry/artifact re-checked immediately
        # before any external effect
        if self.authorizations is not None and env.get("approval_id"):
            elig, why = self.authorizations.still_eligible(env["approval_id"])
            if not elig:
                self.con.execute(
                    "UPDATE messages SET state='revoked-before-send' "
                    "WHERE message_id=? AND project=?",
                    (message_id, self.project))
                self._event(message_id, "revoked-before-send", why)
                self.con.commit()
                raise DeliveryDisabled(f"stopped before effect: {why}")
        ts = time.strftime("%Y%m%dT%H%M%SZ",
                           time.gmtime(float(m["created_at"])))
        fname = f"{ts}_{env['sender']}_{env['recipient']}_" \
                f"{env['msg_type']}_{env['action_id']}.md"
        dest_dir = Path(env.get("destination") or
                        (Path(self.policy.outbox_root) / env["recipient"]))
        root = dest_dir.parent
        if self.policy.require_existing_root and not root.exists():
            # 05C: a missing destination (e.g. unmounted NAS) must never
            # become a local impostor inbox
            self._event(message_id, "delivery_refused",
                        f"destination root missing: {root} — refusing to "
                        f"create an impostor inbox")
            self.con.commit()
            raise DeliveryDisabled(f"destination unavailable: {root}")
        dest = dest_dir / fname
        dest.parent.mkdir(parents=True, exist_ok=True)
        approved_body = env.get("body", m["body"])
        if dest.exists():
            prev = dest.read_text(errors="replace")
            # identical content: reconciled, nothing rewritten; ANY
            # difference is an integrity conflict and is never overwritten
            if fp("msg-body", prev) == env["body_digest"]:
                self.con.execute(
                    "UPDATE messages SET state='sent', sent_at=? "
                    "WHERE message_id=? AND project=? AND state='delivering'",
                    (utcnow(), message_id, self.project))
                self._event(message_id, "reconciled-existing",
                            f"artifact matches envelope digest: {dest}")
                self.con.commit()
                return "sent"
            self._event(message_id, "integrity_conflict",
                        f"existing artifact differs: {dest}")
            self.con.commit()
            return "conflict"
        tmp = dest.with_suffix(".md.tmp")
        tmp.write_text(approved_body)   # persisted action, not the row
        tmp.replace(dest)
        if os.environ.get("DIRECTOR_FAILPOINT") == "after-write-before-sent":
            os._exit(9)   # real crash boundary for failure tests
        self.con.execute(
            "UPDATE messages SET state='sent', sent_at=? "
            "WHERE message_id=? AND project=? AND state='delivering'",
            (utcnow(), message_id, self.project))
        self._event(message_id, "sent", str(dest))
        self.con.commit()
        return "sent"

    def _envelope(self, message_id: str) -> dict:
        row = self.con.execute(
            "SELECT envelope_json FROM action_envelopes WHERE action_id=? "
            "AND project=?", (message_id, self.project)).fetchone()
        return json.loads(row[0]) if row else {}

    def record_ack(self, message_id: str, ack_body: str = "",
                   from_agent: str = "", verified_sender: bool = False,
                   transport=None, artifact_path: Optional[Path] = None,
                   envelope: Optional[dict] = None,
                   session_bindings: Optional[dict] = None) -> str:
        """05C §5: acceptance = the validated ACK ENVELOPE plus
        attributable transport evidence. The envelope must name the exact
        message, project, task, CURRENT assignment epoch, and registered
        session binding. A From: header, a matching file UID alone, or a
        caller-declared verified flag is never sufficient. Invalid
        receipts are quarantined: no acked_at, no state change, no
        ownership effects, and retries/deadlines keep running."""
        row = self.con.execute(
            "SELECT recipient FROM messages WHERE message_id=? AND "
            "project=?", (message_id, self.project)).fetchone()
        intended = row[0] if row else ""
        env = envelope or {}
        row_full = self.con.execute(
            "SELECT * FROM messages WHERE message_id=? AND project=?",
            (message_id, self.project)).fetchone()
        if row_full is None:
            persisted = {}
        else:
            cols = [d[0] for d in self.con.execute(
                "SELECT * FROM messages LIMIT 0").description]
            persisted = dict(zip(cols, row_full))
        persisted["message_id"] = message_id
        persisted["project"] = self.project
        persisted.setdefault("task_ref", persisted.get("correlation_id", ""))
        # origin is ESTABLISHED by the trusted transport/adapter only
        origin = None
        if transport is not None and artifact_path is not None:
            try:
                origin = transport.verify_origin(artifact_path)
            except Exception:  # noqa: BLE001
                origin = None
        ok_acc, problems = validate_acceptance(
            persisted, env, origin, session_bindings,
            persisted.get("state", ""))
        if from_agent not in (intended, f"agent:{intended}"):
            problems.append("sender does not match the intended recipient")
        accepted = ok_acc and not problems
        if not accepted:
            self.con.execute(
                "INSERT INTO invalid_receipts(message_id, project, reason, "
                "payload, ts) VALUES (?,?,?,?,?)",
                (message_id, self.project, "; ".join(problems) or
                 "unattributed", json.dumps({"envelope": env,
                                             "from_agent": from_agent}),
                 utcnow()))
            self.con.commit()
            return "ack-unverified"
        state = "acked" if accepted else "ack-unverified"
        self.con.execute(
            "UPDATE messages SET state=?, acked_at=? "
            "WHERE message_id=? AND project=?",
            (state, utcnow(), message_id, self.project))
        self._event(message_id, state,
                    f"from={from_agent or 'unattributed'} "
                    f"verified={verified_sender} {ack_body[:60]}")
        self.con.commit()
        return state

    def expire_due(self) -> list[str]:
        """Expiry -> escalation draft is produced by the caller from these."""
        rows = self.con.execute(
            "SELECT message_id FROM messages WHERE state IN "
            "('proposed','sent') AND expires_at < ? AND project=?",
            (utcnow(), self.project)).fetchall()
        out = []
        for (mid,) in rows:
            self.con.execute(
                "UPDATE messages SET state='expired' WHERE message_id=? "
                "AND project=?", (mid, self.project))
            self._event(mid, "expired")
            out.append(mid)
        self.con.commit()
        return out

    def reconcile_mailbox(self) -> list[str]:
        """After network failure: scan the outbox for delivered artifacts
        whose state lags (file exists but state != sent/acked) and for ack
        files that arrived out-of-band. Idempotent."""
        recovered = []
        if self.policy.outbox_root is None:
            return recovered
        root = Path(self.policy.outbox_root)
        for f in root.rglob("*.md"):
            mid = f.stem.split("_")[-1]
            row = self.con.execute(
                "SELECT state FROM messages WHERE message_id=? "
                "AND project=?", (mid, self.project)).fetchone()
            if row is None:
                continue
            if row[0] in ("proposed", "delivering"):
                self.con.execute(
                    "UPDATE messages SET state='sent', sent_at=? "
                    "WHERE message_id=? AND project=?",
                    (utcnow(), mid, self.project))
                self._event(mid, "recovered", str(f))
                recovered.append(mid)
            head = f.read_text(errors="replace")[:2000]
            if "ack_of:" in head and row[0] not in ("acked",):
                # closure §3: mailbox recovery uses the SAME validator;
                # the file must carry a scoped envelope or it settles
                # nothing
                sender = ""
                env = {}
                for line in head.splitlines():
                    if line.lower().startswith("from:"):
                        sender = line.split(":", 1)[1].strip()
                    if line.lower().startswith("envelope:"):
                        try:
                            env = json.loads(line.split(":", 1)[1].strip())
                        except json.JSONDecodeError:
                            env = {}
                self.record_ack(mid, ack_body="recovered from mailbox",
                                from_agent=sender,
                                transport=self.transport,
                                artifact_path=f, envelope=env,
                                session_bindings=None)
                recovered.append(mid)
        self.con.commit()
        return recovered

    def reconcile_with_receiver(self, receiver,
                                session_bindings: Optional[dict] = None
                                ) -> list[str]:
        """05C closure §3: the receiver must return SCOPED acceptance
        receipts validated by the same contract as record_ack. A bare
        message id is not proof of task acceptance and settles nothing."""
        settled = []
        try:
            receipts = list(receiver.receipts())
        except Exception:  # noqa: BLE001
            return settled
        for rec in receipts:
            if not isinstance(rec, dict):
                self._event("?", "receipt_rejected",
                            f"bare id {rec!r} is not an acceptance receipt")
                continue
            mid = rec.get("ack_of", "")
            row = self.con.execute(
                "SELECT * FROM messages WHERE message_id=? AND project=?",
                (mid, rec.get("project", self.project))).fetchone()
            if row is None:
                continue
            cols = [d[0] for d in self.con.execute(
                "SELECT * FROM messages LIMIT 0").description]
            persisted = dict(zip(cols, row))
            persisted["message_id"] = mid
            persisted["task_ref"] = persisted.get("correlation_id", "")
            # the receiver ledger IS the trusted side once it carries a
            # scoped receipt; origin comes from the receipt's session,
            # which must match the registered binding
            ok, problems = validate_acceptance(
                persisted, rec, rec.get("session_id"), session_bindings,
                persisted.get("state", "sent"))
            if not ok:
                self._event(mid, "receipt_rejected", "; ".join(problems))
                continue
            self.con.execute(
                "UPDATE messages SET state='acked', acked_at=? "
                "WHERE message_id=? AND project=? AND state!='acked'",
                (utcnow(), mid, persisted.get("project", self.project)))
            self._event(mid, "acked",
                        "settled from scoped receiver receipt")
            settled.append(mid)
        self.con.commit()
        return settled

    def snapshot(self) -> list[dict]:
        rows = self.con.execute(
            "SELECT message_id, recipient, state, attempts, created_at, "
            "acked_at FROM messages WHERE project=? "
            "ORDER BY created_at DESC LIMIT 50", (self.project,))
        return [dict(zip(("message_id", "recipient", "state", "attempts",
                          "created_at", "acked_at"), r)) for r in rows]

    @staticmethod
    def _ts() -> str:
        return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
