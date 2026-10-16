"""Project-scope identity migration (§2/§3) — non-destructive, restart-safe.

Durable identity for project-specific state is (project, local_id).
Migrations are detected PER TABLE (presence of the `project` column), so
tables created after an earlier migration are still caught. Each rebuild
runs in one transaction and preserves every row; the original table is
renamed `*_legacy` as provenance. Never destructive, idempotent by
construction.

Shared-by-design state (explicitly NOT duplicated per project):
  - source cursors / seen_events: a physical feed event is fleet-global
  - model_usage, journal: director-global audit ledgers (project is a
    column for attribution, not part of identity)
"""
from __future__ import annotations

import sqlite3


def _cols(con, table: str) -> list[str]:
    return [r[1] for r in con.execute(f"PRAGMA table_info({table})")]


def _exists(con, table: str) -> bool:
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                       "AND name=?", (table,)).fetchone() is not None


def _pk(con, table: str) -> tuple:
    return tuple(sorted(r[1] for r in con.execute(
        f"PRAGMA table_info({table})") if r[5]))


def _needs_scope(con, table: str, want_pk: tuple) -> bool:
    """Scoped identity = the composite primary key. 04-era tables carry a
    project COLUMN but single-column PKs — detection must use the PK."""
    if not _exists(con, table):
        return False
    return _pk(con, table) != want_pk or "project" not in _cols(con, table)


def _rebuild(con, table: str, new_ddl: str, select_sql: str,
             legacy_keep: bool = True) -> None:
    tmp = f"{table}_scoped"
    con.execute(new_ddl)
    con.execute(f"INSERT INTO {tmp} {select_sql}")
    if legacy_keep:
        con.execute(f"ALTER TABLE {table} RENAME TO {table}_legacy")
    else:
        con.execute(f"DROP TABLE {table}")
    con.execute(f"ALTER TABLE {tmp} RENAME TO {table}")


_TASKS_DDL = """
CREATE TABLE tasks_scoped (
    task_id TEXT NOT NULL, project TEXT NOT NULL, state TEXT NOT NULL,
    accountable_owner TEXT NOT NULL DEFAULT '',
    execution_agent TEXT NOT NULL DEFAULT '', session_id TEXT NOT NULL DEFAULT '',
    host TEXT NOT NULL DEFAULT '', assignment_authority TEXT NOT NULL DEFAULT '',
    epoch INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    last_verified_progress TEXT NOT NULL DEFAULT '',
    correlation_id TEXT NOT NULL DEFAULT '', PRIMARY KEY (project, task_id))
"""

_MESSAGES_DDL = """
CREATE TABLE messages_scoped (
    message_id TEXT NOT NULL, project TEXT NOT NULL DEFAULT 'default',
    correlation_id TEXT NOT NULL DEFAULT '', recipient TEXT NOT NULL,
    msg_type TEXT NOT NULL, subject TEXT NOT NULL, body TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'proposed', attempts INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL, sent_at REAL, acked_at REAL, expires_at REAL,
    authorization_ref TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (project, message_id))
"""

_INCIDENTS_DDL = """
CREATE TABLE incidents_scoped (
    incident_id TEXT NOT NULL, project TEXT NOT NULL,
    correlation_id TEXT NOT NULL, identity_key TEXT NOT NULL,
    first_seen REAL NOT NULL, last_seen REAL NOT NULL, state TEXT NOT NULL,
    severity TEXT NOT NULL, suspected_cause TEXT NOT NULL, verified_cause TEXT,
    owner TEXT, related_rules TEXT NOT NULL, evidence TEXT NOT NULL,
    proposed_action TEXT, resolution TEXT,
    reopen_count INTEGER NOT NULL DEFAULT 0,
    lifecycle_state TEXT NOT NULL DEFAULT 'detected',
    PRIMARY KEY (project, incident_id))
"""

_PROPOSALS_DDL = """
CREATE TABLE proposals_scoped (
    trigger_key TEXT NOT NULL, project TEXT NOT NULL DEFAULT 'default',
    ptype TEXT NOT NULL, filename TEXT NOT NULL, authority TEXT NOT NULL,
    created_at REAL NOT NULL, body TEXT NOT NULL,
    written INTEGER NOT NULL DEFAULT 0, written_at REAL,
    PRIMARY KEY (project, trigger_key))
"""

_FINDINGS_DDL = """
CREATE TABLE reconciler_findings_scoped (
    finding_id TEXT NOT NULL, project TEXT NOT NULL, rule TEXT NOT NULL,
    subject TEXT NOT NULL, severity TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'open', hits INTEGER NOT NULL DEFAULT 1,
    first_seen REAL NOT NULL, last_seen REAL NOT NULL,
    cooldown_until REAL NOT NULL DEFAULT 0, payload TEXT NOT NULL,
    PRIMARY KEY (project, finding_id))
"""

_AUTHS_DDL = """
CREATE TABLE authorizations_scoped (
    auth_id TEXT NOT NULL, project TEXT NOT NULL,
    objective_ref TEXT NOT NULL DEFAULT '', task_ref TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL, recipient TEXT NOT NULL DEFAULT '',
    spend_scope TEXT NOT NULL DEFAULT 'none',
    max_spend_usd REAL NOT NULL DEFAULT 0, expires_at REAL NOT NULL,
    issuer TEXT NOT NULL, issued_at REAL NOT NULL,
    payload_digest TEXT NOT NULL DEFAULT '',
    revoked INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (project, auth_id))
"""


def ensure_scoped(con: sqlite3.Connection) -> None:
    con.executescript(
        "CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, "
        "value TEXT NOT NULL);")
    try:
        con.execute("BEGIN IMMEDIATE")
        if _needs_scope(con, "tasks", ("project", "task_id")):
            _rebuild(con, "tasks", _TASKS_DDL,
                     "(task_id, project, state, accountable_owner, "
                     "execution_agent, session_id, host, "
                     "assignment_authority, epoch, created_at, updated_at, "
                     "last_verified_progress, correlation_id) "
                     "SELECT task_id, COALESCE(project,'default'), state, "
                     "accountable_owner, execution_agent, session_id, host, "
                     "assignment_authority, epoch, created_at, updated_at, "
                     "last_verified_progress, correlation_id FROM tasks")
        if _needs_scope(con, "messages", ("message_id", "project")):
            _rebuild(con, "messages", _MESSAGES_DDL,
                     "(message_id, project, correlation_id, recipient, "
                     "msg_type, subject, body, state, attempts, created_at, "
                     "sent_at, acked_at, expires_at, authorization_ref) "
                     "SELECT message_id, 'default', correlation_id, "
                     "recipient, msg_type, subject, body, state, attempts, "
                     "created_at, sent_at, acked_at, expires_at, "
                     "authorization_ref FROM messages")
        if _needs_scope(con, "incidents", ("incident_id", "project")):
            lc = ("lifecycle_state" if "lifecycle_state" in _cols(con, "incidents")
                  else "'detected'")
            _rebuild(con, "incidents", _INCIDENTS_DDL,
                     "(incident_id, project, correlation_id, identity_key, "
                     "first_seen, last_seen, state, severity, "
                     "suspected_cause, verified_cause, owner, related_rules, "
                     "evidence, proposed_action, resolution, reopen_count, "
                     "lifecycle_state) SELECT incident_id, "
                     "COALESCE(project,'default'), correlation_id, "
                     "identity_key, first_seen, last_seen, state, severity, "
                     "suspected_cause, verified_cause, owner, related_rules, "
                     "evidence, proposed_action, resolution, reopen_count, "
                     f"{lc} FROM incidents")
        if _needs_scope(con, "proposals", ("project", "trigger_key")):
            _rebuild(con, "proposals", _PROPOSALS_DDL,
                     "(trigger_key, project, ptype, filename, authority, "
                     "created_at, body, written, written_at) SELECT "
                     "trigger_key, 'default', ptype, filename, authority, "
                     "created_at, body, COALESCE(written,0), written_at "
                     "FROM proposals")
        if _exists(con, "reconciler_findings") and \
                "project" not in _cols(con, "reconciler_findings"):
            _rebuild(con, "reconciler_findings", _FINDINGS_DDL,
                     "(finding_id, project, rule, subject, severity, state, "
                     "hits, first_seen, last_seen, cooldown_until, payload) "
                     "SELECT finding_id, COALESCE(project,'default'), rule, "
                     "subject, severity, state, hits, first_seen, last_seen, "
                     "cooldown_until, payload FROM reconciler_findings")
        if _exists(con, "authorizations") and \
                "project" not in _cols(con, "authorizations"):
            dig = ("payload_digest" if "payload_digest" in
                   _cols(con, "authorizations") else "''")
            rev = ("revoked" if "revoked" in _cols(con, "authorizations")
                   else "0")
            _rebuild(con, "authorizations", _AUTHS_DDL,
                     "(auth_id, project, objective_ref, task_ref, action, "
                     "recipient, spend_scope, max_spend_usd, expires_at, "
                     "issuer, issued_at, payload_digest, revoked) SELECT "
                     "auth_id, COALESCE(project,'default'), objective_ref, "
                     "task_ref, action, recipient, spend_scope, "
                     "max_spend_usd, expires_at, issuer, issued_at, "
                     f"{dig}, {rev} FROM authorizations")
        # child history tables gain project (append-only, ALTER only)
        for child in ("task_events", "task_contributors", "message_events",
                      "incident_events", "incident_recovery",
                      "reconciler_notes", "auth_nonces"):
            if _exists(con, child) and "project" not in _cols(con, child):
                con.execute(f"ALTER TABLE {child} ADD COLUMN project "
                            f"TEXT NOT NULL DEFAULT 'default'")
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
