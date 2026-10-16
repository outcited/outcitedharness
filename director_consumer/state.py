"""Durable state: cursors, dedup, proposals, and the append-only journal.

All consumer writes land in ONE sqlite database inside the director's own
workspace. Every ingest batch is a single transaction (journal entries +
proposals + seen fingerprints + cursor advance), so a crash mid-batch rolls
back to the previous consistent position: replay re-polls the same events
and dedup/proposal keys make the outcome identical (effectively-once).

The journal is hash-chained: tampering with any row breaks verification.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Optional

from .schema import Assessment, Proposal, StateMarker, utcnow

SCHEMA = """
CREATE TABLE IF NOT EXISTS cursors (
    feed TEXT PRIMARY KEY,
    cursor TEXT NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS seen_events (
    fingerprint TEXT PRIMARY KEY,
    first_seen REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS proposals (
    trigger_key TEXT PRIMARY KEY,
    ptype TEXT NOT NULL,
    filename TEXT NOT NULL,
    authority TEXT NOT NULL,
    created_at REAL NOT NULL,
    body TEXT NOT NULL,
    written INTEGER NOT NULL DEFAULT 0,
    written_at REAL
);
CREATE TABLE IF NOT EXISTS journal (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    kind TEXT NOT NULL,             -- marker|assessment|llm|run
    payload TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    entry_hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_journal_kind ON journal(kind);
CREATE INDEX IF NOT EXISTS idx_proposals_unwritten ON proposals(written);
CREATE TABLE IF NOT EXISTS model_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    endpoint TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    purpose TEXT,
    project TEXT,
    ok INTEGER NOT NULL,
    latency_ms REAL,
    note TEXT
);
"""


def _migrate(con: sqlite3.Connection) -> None:
    cols = {r[1] for r in con.execute("PRAGMA table_info(proposals)")}
    if "written" not in cols:
        con.execute("ALTER TABLE proposals ADD COLUMN written INTEGER NOT NULL DEFAULT 0")
    if "written_at" not in cols:
        con.execute("ALTER TABLE proposals ADD COLUMN written_at REAL")
    con.commit()


class DirectorState:
    def __init__(self, workspace: Path):
        self.workspace = Path(workspace)
        self.workspace.mkdir(parents=True, exist_ok=True)
        (self.workspace / "outbox").mkdir(exist_ok=True)
        (self.workspace / "escalations").mkdir(exist_ok=True)
        self.db = self.workspace / "director.db"
        self.con = sqlite3.connect(self.db, timeout=30)
        self.con.executescript(SCHEMA)
        self.con.commit()
        _migrate(self.con)
        self._head: Optional[str] = None

    # ---------- journal (hash-chained, append-only) ----------
    #
    # HEAD-CACHE LAW (01A #1): the in-memory head is only updated inside a
    # transaction that COMMITS. A rollback invalidates the cache so the next
    # append re-reads the true head from the database — a rolled-back batch
    # can never leave the chain pointing at ghost entries.

    def _head_hash(self) -> str:
        if self._head is None:
            row = self.con.execute(
                "SELECT entry_hash FROM journal ORDER BY id DESC LIMIT 1"
            ).fetchone()
            self._head = row[0] if row else "GENESIS"
        return self._head

    def _append(self, cur: sqlite3.Cursor, kind: str, payload: dict) -> int:
        prev = self._head_hash()
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        ts = utcnow()
        entry_hash = hashlib.sha256(
            f"{prev}|{ts}|{kind}|{blob}".encode()).hexdigest()
        c = cur.execute(
            "INSERT INTO journal(ts, kind, payload, prev_hash, entry_hash) "
            "VALUES (?,?,?,?,?)", (ts, kind, blob, prev, entry_hash))
        self._head = entry_hash
        return c.lastrowid

    def _journal_standalone(self, kind: str, payload: dict) -> None:
        try:
            with self.con:
                self._append(self.con, kind, payload)
        except Exception:
            self._head = None  # rolled back — cache must not keep ghost head
            raise

    def journal_marker(self, m: StateMarker) -> None:
        self._journal_standalone("marker", {
            "kind": m.kind, "feed": m.feed, "detail": m.detail,
            "observed_ts": m.observed_ts})

    def journal_run(self, summary: dict) -> None:
        self._journal_standalone("run", summary)

    def journal_llm(self, payload: dict) -> None:
        self._journal_standalone("llm", payload)

    # ---------- ingestion batch (atomic) ----------

    def ingest_batch(self, feed: str, cursor: str, fingerprints: Iterable[str],
                     assessments: list[Assessment],
                     proposals: list[Proposal],
                     markers: list[StateMarker]) -> list[Proposal]:
        """One transaction: seen + proposals + journal + cursor.

        Returns the proposals that were NEW in this batch (already-existing
        trigger_keys are skipped — replay produces no duplicates).
        """
        new_proposals: list[Proposal] = []
        fps = list(fingerprints)
        cur = self.con
        head_before = self._head  # snapshot for rollback restore
        cur.execute("BEGIN IMMEDIATE")
        try:
            for p in proposals:
                existing = cur.execute(
                    "SELECT 1 FROM proposals WHERE trigger_key=?",
                    (p.trigger_key,)).fetchone()
                if existing:
                    continue
                cur.execute(
                    "INSERT INTO proposals(trigger_key, ptype, filename, "
                    "authority, created_at, body, written) "
                    "VALUES (?,?,?,?,?,?,0)",
                    (p.trigger_key, p.ptype, p.filename, p.authority,
                     utcnow(), p.body))
                new_proposals.append(p)
            for a in assessments:
                self._append(cur, "assessment", a.to_json())
            for m in markers:
                self._append(cur, "marker", {
                    "kind": m.kind, "feed": m.feed, "detail": m.detail,
                    "observed_ts": m.observed_ts})
            for f in fps:
                cur.execute(
                    "INSERT OR IGNORE INTO seen_events(fingerprint, first_seen) "
                    "VALUES (?,?)", (f, utcnow()))
            cur.execute(
                "INSERT INTO cursors(feed, cursor, updated_at) VALUES (?,?,?) "
                "ON CONFLICT(feed) DO UPDATE SET cursor=excluded.cursor, "
                "updated_at=excluded.updated_at", (feed, cursor, utcnow()))
            cur.execute("COMMIT")
        except Exception:
            cur.execute("ROLLBACK")
            # 01A #1: rolled-back journal entries must not poison the chain
            self._head = None
            raise
        return new_proposals

    # ---------- transactional outbox (01A #2) ----------
    #
    # Proposal rows commit with written=0. flush_pending_writes() writes the
    # draft files atomically (tmp + os.replace) and marks written=1. A crash
    # after commit but before the file write is healed on the next run — no
    # draft can be permanently lost, and files are never half-written.

    def flush_pending_writes(self, writer) -> int:
        """writer(proposal_row_dict) -> final Path. Returns rows healed."""
        rows = self.con.execute(
            "SELECT trigger_key, ptype, filename, authority, created_at, body "
            "FROM proposals WHERE written=0 ORDER BY created_at").fetchall()
        healed = 0
        for tk, ptype, filename, authority, created, body in rows:
            try:
                target = writer({"trigger_key": tk, "ptype": ptype,
                                 "filename": filename, "authority": authority,
                                 "body": body})
                with self.con:
                    self.con.execute(
                        "UPDATE proposals SET written=1, written_at=?, "
                        "filename=? WHERE trigger_key=? AND written=0",
                        (utcnow(), str(target), tk))
                healed += 1
            except Exception:
                # leave written=0; retried on the next flush (never lost)
                continue
        return healed

    def unwritten_count(self) -> int:
        return self.con.execute(
            "SELECT COUNT(*) FROM proposals WHERE written=0").fetchone()[0]

    def record_model_usage(self, row: dict) -> None:
        """Model governance ledger (§6): every router call, allowed or
        refused, is attributable."""
        with self.con:
            self.con.execute(
                "INSERT INTO model_usage(ts, endpoint, provider, model, "
                "purpose, project, ok, latency_ms, note) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (utcnow(), row.get("endpoint", "?"), row.get("provider", "?"),
                 row.get("model", ""), row.get("purpose", ""),
                 row.get("project", ""), 1 if row.get("ok") else 0,
                 row.get("latency_ms"), row.get("note", "")))

    # ---------- queries ----------

    def get_cursor(self, feed: str) -> Optional[str]:
        row = self.con.execute(
            "SELECT cursor FROM cursors WHERE feed=?", (feed,)).fetchone()
        return row[0] if row else None

    def seen(self, fingerprints: list[str]) -> set[str]:
        if not fingerprints:
            return set()
        q = ",".join("?" * len(fingerprints))
        return {r[0] for r in self.con.execute(
            f"SELECT fingerprint FROM seen_events WHERE fingerprint IN ({q})",
            fingerprints)}

    def prune_seen(self, keep: int = 200000) -> None:
        with self.con:
            self.con.execute(
                "DELETE FROM seen_events WHERE fingerprint NOT IN "
                "(SELECT fingerprint FROM seen_events "
                " ORDER BY first_seen DESC LIMIT ?)", (keep,))

    def verify_chain(self) -> tuple[bool, Optional[int]]:
        """Returns (ok, broken_at_id)."""
        prev = "GENESIS"
        for rid, ts, kind, payload, prev_hash, entry_hash in self.con.execute(
                "SELECT id, ts, kind, payload, prev_hash, entry_hash "
                "FROM journal ORDER BY id"):
            if prev_hash != prev:
                return False, rid
            blob = payload
            expect = hashlib.sha256(
                f"{prev}|{ts}|{kind}|{blob}".encode()).hexdigest()
            if expect != entry_hash:
                return False, rid
            prev = entry_hash
        return True, None

    def close(self) -> None:
        self.con.close()
