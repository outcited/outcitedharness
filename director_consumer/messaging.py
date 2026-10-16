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
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .schema import fp, utcnow
from .scope import ensure_scoped

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
"""

MAX_ATTEMPTS = 5
DEFAULT_TTL_S = 7 * 86400


class DeliveryDisabled(Exception):
    pass


@dataclass
class DeliveryPolicy:
    enabled: bool = False
    authorization_ref: str = ""
    outbox_root: Optional[Path] = None

    @classmethod
    def load(cls, path: Optional[Path], outbox_root: Optional[Path]):
        """delivery: {enabled: bool, authorization_ref: str} from project
        policy. Without BOTH, delivery refuses."""
        enabled, ref = False, ""
        if path and Path(path).exists():
            import yaml
            data = yaml.safe_load(Path(path).read_text()) or {}
            d = data.get("delivery", {})
            enabled = bool(d.get("enabled", False))
            ref = str(d.get("authorization_ref", ""))
        return cls(enabled=enabled, authorization_ref=ref,
                   outbox_root=outbox_root)


class MailboxDelivery:
    def __init__(self, con, policy: DeliveryPolicy,
                 sender: str = "engineering-director",
                 project: str = "default"):
        self.con = con
        self.policy = policy
        self.sender = sender
        self.project = project
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

    def deliver(self, message_id: str) -> str:
        """Attempt delivery. Gated: raises DeliveryDisabled when the
        authorization gate is closed. Idempotent by message_id."""
        row = self.con.execute(
            "SELECT * FROM messages WHERE message_id=? AND project=?",
            (message_id, self.project)).fetchone()
        if row is None:
            raise KeyError(message_id)
        cols = [d[0] for d in self.con.execute(
            "SELECT * FROM messages LIMIT 0").description]
        m = dict(zip(cols, row))
        if m["state"] in ("sent", "acked", "expired", "escalated"):
            return m["state"]          # duplicate-send protection
        if not (self.policy.enabled and self.policy.authorization_ref):
            self._event(message_id, "delivery_refused",
                        "delivery gate closed (needs enabled + "
                        "authorization_ref)")
            self.con.commit()
            raise DeliveryDisabled(
                "production delivery disabled — explicit authorization "
                "required (§3 hard boundary)")
        if self.policy.outbox_root is None:
            raise DeliveryDisabled("no outbox root configured")
        # bounded retries
        if m["attempts"] >= MAX_ATTEMPTS:
            self.con.execute(
                "UPDATE messages SET state='escalated' WHERE message_id=? "
                "AND project=?", (message_id, self.project))
            self._event(message_id, "escalated",
                        f"attempts {m['attempts']} >= {MAX_ATTEMPTS}")
            self.con.commit()
            return "escalated"
        fname = f"{self._ts()}_{self.sender}_{m['recipient']}_" \
                f"{m['msg_type']}_{message_id}.md"
        dest = Path(self.policy.outbox_root) / m["recipient"] / fname
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".md.tmp")
        tmp.write_text(m["body"])
        tmp.replace(dest)             # atomic; duplicate sends same file
        self.con.execute(
            "UPDATE messages SET state='sent', attempts=attempts+1, "
            "sent_at=? WHERE message_id=? AND project=?",
            (utcnow(), message_id, self.project))
        self._event(message_id, "sent", str(dest))
        self.con.commit()
        return "sent"

    def record_ack(self, message_id: str, ack_body: str = "",
                   from_agent: str = "") -> str:
        """Acknowledgment receipt. Delivery already happened; completion
        is a separate task-state concern (§3 laws)."""
        self.con.execute(
            "UPDATE messages SET state='acked', acked_at=? "
            "WHERE message_id=? AND project=?",
            (utcnow(), message_id, self.project))
        self._event(message_id, "acked", f"from={from_agent} {ack_body[:80]}")
        self.con.commit()
        return "acked"

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
            if row[0] == "proposed":
                self.con.execute(
                    "UPDATE messages SET state='sent', sent_at=? "
                    "WHERE message_id=? AND project=?",
                    (utcnow(), mid, self.project))
                self._event(mid, "recovered", str(f))
                recovered.append(mid)
            if "ack_of:" in f.read_text(errors="replace")[:2000]:
                if row[0] != "acked":
                    self.record_ack(mid, ack_body="recovered from mailbox",
                                    from_agent="mailbox-reconciler")
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
