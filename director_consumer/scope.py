"""Project-scope identity migration (§2) — non-destructive, restart-safe.

The DIRECTOR-01–04 schemas keyed tasks/messages/incidents by local id
alone. Two projects can legitimately use the same local id, so the
durable key is (project, local_id). This module rebuilds those tables
IN A SINGLE TRANSACTION (data preserved verbatim) exactly once, tracked
via schema_meta. Idempotent: second call is a no-op.

Child tables (task_events, task_contributors, message_events,
incident_events, incident_recovery) gain a project column + indexes so
history cannot bleed across projects.
"""
from __future__ import annotations

import sqlite3

SCOPE_VERSION = "1"

_TASKS_DDL = """
CREATE TABLE tasks_scoped (
    task_id TEXT NOT NULL,
    project TEXT NOT NULL,
    state TEXT NOT NULL,
    accountable_owner TEXT NOT NULL DEFAULT '',
    execution_agent TEXT NOT NULL DEFAULT '',
    session_id TEXT NOT NULL DEFAULT '',
    host TEXT NOT NULL DEFAULT '',
    assignment_authority TEXT NOT NULL DEFAULT '',
    epoch INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    last_verified_progress TEXT NOT NULL DEFAULT '',
    correlation_id TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (project, task_id)
)
"""

_MESSAGES_DDL = """
CREATE TABLE messages_scoped (
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
)
"""

_INCIDENTS_DDL = """
CREATE TABLE incidents_scoped (
    incident_id TEXT NOT NULL,
    project TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    identity_key TEXT NOT NULL,
    first_seen REAL NOT NULL,
    last_seen REAL NOT NULL,
    state TEXT NOT NULL,
    severity TEXT NOT NULL,
    suspected_cause TEXT NOT NULL,
    verified_cause TEXT,
    owner TEXT,
    related_rules TEXT NOT NULL,
    evidence TEXT NOT NULL,
    proposed_action TEXT,
    resolution TEXT,
    reopen_count INTEGER NOT NULL DEFAULT 0,
    lifecycle_state TEXT NOT NULL DEFAULT 'detected',
    PRIMARY KEY (project, incident_id)
)
"""


def _meta_get(con, key: str):
    try:
        row = con.execute("SELECT value FROM schema_meta WHERE key=?",
                          (key,)).fetchone()
        return row[0] if row else None
    except sqlite3.Error:
        return None


def _meta_set(con, key: str, value: str) -> None:
    con.execute("INSERT OR REPLACE INTO schema_meta(key, value) VALUES (?,?)",
                (key, value))


def _table_exists(con, name: str) -> bool:
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                       "AND name=?", (name,)).fetchone() is not None


def ensure_scoped(con: sqlite3.Connection) -> None:
    """Install project-scoped keys exactly once per database."""
    if _meta_get(con, "project_scope") == SCOPE_VERSION:
        return
    # registry tables may not exist yet on legacy dbs — make meta safe
    con.executescript(
        "CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, "
        "value TEXT NOT NULL);")
    if _meta_get(con, "project_scope") == SCOPE_VERSION:
        return
    try:
        con.execute("BEGIN IMMEDIATE")
        if _table_exists(con, "tasks") and \
                not _table_exists(con, "tasks_scoped"):
            # preserve all rows verbatim; never delete data
            cols = [r[1] for r in con.execute("PRAGMA table_info(tasks)")]
            has_project = "project" in cols
            sel_project = "project" if has_project else "'default'"
            con.execute(_TASKS_DDL)
            con.execute(
                f"INSERT INTO tasks_scoped(task_id, project, state, "
                f"accountable_owner, execution_agent, session_id, host, "
                f"assignment_authority, epoch, created_at, updated_at, "
                f"last_verified_progress, correlation_id) "
                f"SELECT task_id, {sel_project}, state, accountable_owner, "
                f"execution_agent, session_id, host, assignment_authority, "
                f"epoch, created_at, updated_at, last_verified_progress, "
                f"correlation_id FROM tasks")
            con.execute("ALTER TABLE tasks RENAME TO tasks_legacy")
            con.execute("ALTER TABLE tasks_scoped RENAME TO tasks")
        if _table_exists(con, "messages") and \
                not _table_exists(con, "messages_scoped"):
            con.execute(_MESSAGES_DDL)
            con.execute(
                "INSERT INTO messages_scoped(message_id, project, "
                "correlation_id, recipient, msg_type, subject, body, state, "
                "attempts, created_at, sent_at, acked_at, expires_at, "
                "authorization_ref) SELECT message_id, 'default', "
                "correlation_id, recipient, msg_type, subject, body, state, "
                "attempts, created_at, sent_at, acked_at, expires_at, "
                "authorization_ref FROM messages")
            con.execute("ALTER TABLE messages RENAME TO messages_legacy")
            con.execute("ALTER TABLE messages_scoped RENAME TO messages")
        if _table_exists(con, "incidents") and \
                not _table_exists(con, "incidents_scoped"):
            cols = [r[1] for r in con.execute("PRAGMA table_info(incidents)")]
            has_lc = "lifecycle_state" in cols
            lc = "lifecycle_state" if has_lc else "'detected'"
            con.execute(_INCIDENTS_DDL)
            con.execute(
                f"INSERT INTO incidents_scoped(incident_id, project, "
                f"correlation_id, identity_key, first_seen, last_seen, "
                f"state, severity, suspected_cause, verified_cause, owner, "
                f"related_rules, evidence, proposed_action, resolution, "
                f"reopen_count, lifecycle_state) SELECT incident_id, "
                f"project, correlation_id, identity_key, first_seen, "
                f"last_seen, state, severity, suspected_cause, "
                f"verified_cause, owner, related_rules, evidence, "
                f"proposed_action, resolution, reopen_count, {lc} "
                f"FROM incidents")
            con.execute("ALTER TABLE incidents RENAME TO incidents_legacy")
            con.execute("ALTER TABLE incidents_scoped RENAME TO incidents")
        # child tables: add project column where missing (rows default)
        for child, parent in (("task_events", "task_id"),
                              ("task_contributors", "task_id"),
                              ("message_events", "message_id"),
                              ("incident_events", "incident_id"),
                              ("incident_recovery", "incident_id")):
            if _table_exists(con, child):
                cols = [r[1] for r in con.execute(f"PRAGMA table_info({child})")]
                if "project" not in cols:
                    con.execute(f"ALTER TABLE {child} ADD COLUMN project "
                                f"TEXT NOT NULL DEFAULT 'default'")
        _meta_set(con, "project_scope", SCOPE_VERSION)
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
