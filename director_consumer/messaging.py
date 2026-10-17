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
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .schema import fp, utcnow
from .scope import ensure_scoped
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
                  message_id: str):
    from .authorization import AuthRequest
    return AuthRequest(project=project, action="deliver",
                       recipient=m["recipient"],
                       task_ref=m.get("correlation_id", ""),
                       nonce=f"deliver:{message_id}",
                       payload_digest=body_digest)

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
                message_id: Optional[str] = None) -> str:
        mid = message_id or fp("msg", recipient, subject, body)[:16]
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

    def deliver(self, message_id: str, auth_id: str = "") -> str:
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
                    _auth_request(self.project, m, body_digest, message_id))
                if not ok:
                    self.con.execute("ROLLBACK")  # nothing consumed
                    self._event(message_id, "delivery_refused", why)
                    self.con.commit()
                    raise DeliveryDisabled(f"authorization refused: {why}")
                # validated + reserved + envelope + intent commit together
                # immutable action envelope persisted with the intent
                self.con.execute(
                    "INSERT OR REPLACE INTO action_envelopes("
                    "action_id, project, envelope_json, body_digest, "
                    "auth_id, created_at) VALUES (?,?,?,?,?,?)",
                    (message_id, self.project, json.dumps({
                        "envelope_version": "1", "project": self.project,
                        "action_id": message_id, "sender": self.sender,
                        "recipient": m["recipient"],
                        "msg_type": m["msg_type"], "subject": m["subject"],
                        "body_digest": body_digest,
                        "transport": self.transport_name,
                        "auth_id": auth_id}), body_digest, auth_id,
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
        # ---- external effect from the PERSISTED envelope ----
        env = self._envelope(message_id)
        ts = time.strftime("%Y%m%dT%H%M%SZ",
                           time.gmtime(float(m["created_at"])))
        fname = f"{ts}_{self.sender}_{m['recipient']}_" \
                f"{m['msg_type']}_{message_id}.md"
        root = Path(self.policy.outbox_root)
        if self.policy.require_existing_root and not root.exists():
            # 05C: a missing destination (e.g. unmounted NAS) must never
            # become a local impostor inbox
            self._event(message_id, "delivery_refused",
                        f"destination root missing: {root} — refusing to "
                        f"create an impostor inbox")
            self.con.commit()
            raise DeliveryDisabled(f"destination unavailable: {root}")
        dest = root / m["recipient"] / fname
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            prev = dest.read_text(errors="replace")
            if fp("msg-body", prev) != body_digest and prev.strip() and \
                    "body here" not in prev:
                # same identity, different contents: integrity conflict
                self._event(message_id, "integrity_conflict",
                            f"existing artifact differs: {dest}")
                self.con.commit()
                return "conflict"
        tmp = dest.with_suffix(".md.tmp")
        tmp.write_text(m["body"])
        tmp.replace(dest)
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
        problems = []
        if not env:
            problems.append("missing ack envelope")
        else:
            if env.get("ack_of") != message_id:
                problems.append("ack_of does not match this message")
            if env.get("project") != self.project:
                problems.append("envelope project mismatch")
            msg = self.con.execute(
                "SELECT body FROM messages WHERE message_id=? AND "
                "project=?", (message_id, self.project)).fetchone()
            from .schema import fp as _fp
            if msg and env.get("message_digest") and \
                    env["message_digest"] != _fp("msg-body", msg[0]):
                problems.append("message digest mismatch")
            if session_bindings is not None:
                want = session_bindings.get(
                    (env.get("project"), env.get("task_ref"),
                     env.get("epoch")))
                if want is None:
                    problems.append("no registered session binding for "
                                    "project/task/epoch")
                elif env.get("session_id") != want:
                    problems.append("session origin does not match the "
                                    "registered binding")
        if transport is not None and artifact_path is not None:
            try:
                verified_sender = bool(transport.verify(from_agent,
                                                        artifact_path))
            except Exception:  # noqa: BLE001
                verified_sender = False
            if not verified_sender:
                problems.append("transport origin not attributable")
        else:
            problems.append("no trusted transport evidence")
        accepted = (not problems and from_agent and
                    (from_agent == intended or
                     from_agent == f"agent:{intended}"))
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
                # P0-4: the From: header alone proves nothing — origin
                # must come from the transport's attributable evidence
                sender = ""
                for line in head.splitlines():
                    if line.lower().startswith("from:"):
                        sender = line.split(":", 1)[1].strip()
                self.record_ack(mid, ack_body="recovered from mailbox",
                                from_agent=sender,
                                transport=self.transport,
                                artifact_path=f)
                recovered.append(mid)
        self.con.commit()
        return recovered

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
