"""SQLite-backed pipeline store: jobs, results, adjudication ledger.

Replaces the JSON state file. One database, WAL mode, safe for the dispatcher
loop, workers (via the dispatcher), and the API to share on the M5.
"""

import json
import os
import sqlite3
import time
from pathlib import Path

DEFAULT_DB = "/Volumes/M5_4TB/extract-results/pipeline.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,               -- substrate | extract | adjudicate
    pillar TEXT,
    grain TEXT,
    schema_name TEXT,
    corpus_key TEXT NOT NULL,         -- sha256:<hash> for doc-grain, node:<id>, query:<...>
    source_path TEXT,
    budget_tier TEXT,
    payload TEXT,
    state TEXT NOT NULL DEFAULT 'pending',  -- pending|claimed|done|failed|dead
    claimed_by TEXT,
    claimed_at REAL,
    visibility_s REAL DEFAULT 3600,
    attempts INTEGER DEFAULT 0,
    max_attempts INTEGER DEFAULT 3,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_state ON jobs(state, kind);
CREATE INDEX IF NOT EXISTS idx_jobs_corpus ON jobs(corpus_key, kind);

CREATE TABLE IF NOT EXISTS results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL REFERENCES jobs(id),
    extractor TEXT NOT NULL,
    extractor_version TEXT NOT NULL,
    corpus_key TEXT NOT NULL,
    document_sha256 TEXT,
    page_label_map TEXT,
    kind_hint TEXT NOT NULL DEFAULT 'substrate',
    output TEXT NOT NULL,
    created_at REAL NOT NULL,
    UNIQUE (corpus_key, kind_hint, extractor_version)
);
CREATE INDEX IF NOT EXISTS idx_results_corpus ON results(corpus_key);

CREATE TABLE IF NOT EXISTS adjudication_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    result_id INTEGER REFERENCES results(id),
    judge TEXT NOT NULL,
    judge_version TEXT,
    claim_index INTEGER,
    severity TEXT NOT NULL,           -- P0|P1|P2|P3|P4
    verdict TEXT NOT NULL,            -- SUPPORTED|UNSUPPORTED|NOT_IN_DOC|AMBIGUOUS
    reason_code TEXT,
    evidence_quote TEXT,
    extractor TEXT,
    failure_class TEXT,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ledger_class ON adjudication_ledger(failure_class, created_at);

CREATE TABLE IF NOT EXISTS budget_spend (
    day TEXT NOT NULL,
    tier TEXT NOT NULL,
    jobs INTEGER DEFAULT 0,
    PRIMARY KEY (day, tier)
);
"""

TIER_DAILY_CAPS = {
    "frontier": 50,
    "hosted-judge": 5000,
    "local-vision": 20000,
    "local-student": 100000,
    "local-canon": 50000,
    "burn-cloud": 25000,
    "deterministic": 10**9,
}


def budget_check(con, tier, caps=None):
    caps = caps or TIER_DAILY_CAPS
    day = time.strftime("%Y-%m-%d")
    row = con.execute("SELECT jobs FROM budget_spend WHERE day=? AND tier=?", (day, tier)).fetchone()
    used = row["jobs"] if row else 0
    cap = caps.get(tier, 0)
    if used >= cap:
        return False, {"day": day, "tier": tier, "used": used, "cap": cap}
    con.execute(
        "INSERT INTO budget_spend (day, tier, jobs) VALUES (?,?,1)"
        " ON CONFLICT(day, tier) DO UPDATE SET jobs = jobs + 1",
        (day, tier),
    )
    con.commit()
    return True, {"day": day, "tier": tier, "used": used + 1, "cap": cap}


def connect(db_path: str = None):
    path = db_path or os.environ.get("PIPELINE_DB", DEFAULT_DB)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=30000")
    con.executescript(SCHEMA)
    return con


def now():
    return time.time()


def enqueue(con, kind, corpus_key, source_path=None, pillar=None, grain=None,
            schema_name=None, budget_tier=None, payload=None, max_attempts=3):
    con.execute(
        "INSERT INTO jobs (kind, pillar, grain, schema_name, corpus_key, source_path,"
        " budget_tier, payload, max_attempts, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (kind, pillar, grain, schema_name, corpus_key, source_path, budget_tier,
         json.dumps(payload) if payload else None, max_attempts, now(), now()),
    )
    con.commit()
    return con.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]


def claim(con, worker, kinds=("substrate",), limit=40):
    """Bounded FIFO claim (queue-correctness fix, 2026-10-08 review).

    - The attempts cap is enforced in BOTH the candidate SELECT and the
      claim UPDATE, so an expired lease can never be reclaimed past
      max_attempts even when the worker crashed without calling fail()
      (production evidence: jobs at 48-123 attempts on a dead volume).
    - Candidates are ordered by insertion id (rowid), NOT created_at:
      created_at is provenance and is known-corrupt for the legacy cohort
      (a single 2029-12-03 bulk stamp); id order is clock-independent FIFO
      and immune to that class of corruption.
    - Terminal handling: an expired lease at/over the attempt cap is a
      zombie; it is retired to dead with a ledger row. Active leases are
      never touched.
    """
    cutoff = now()
    placeholders = ",".join("?" * len(kinds))
    rows = con.execute(
        f"SELECT * FROM jobs WHERE state IN ('pending','claimed') AND kind IN ({placeholders})"
        " AND attempts < max_attempts"
        " AND (state='pending' OR claimed_at + visibility_s < ?)"
        " ORDER BY id ASC LIMIT ?",
        (*kinds, cutoff, limit),
    ).fetchall()
    claimed = []
    for row in rows:
        cur = con.execute(
            "UPDATE jobs SET state='claimed', claimed_by=?, claimed_at=?,"
            " attempts=attempts+1, updated_at=? WHERE id=? AND"
            " attempts < max_attempts"
            " AND (state='pending' OR claimed_at + visibility_s < ?)",
            (worker, cutoff, cutoff, row["id"], cutoff),
        )
        if cur.rowcount:
            claimed.append(dict(row))
    swept = con.execute(
        "UPDATE jobs SET state='dead', updated_at=? WHERE state='claimed'"
        " AND attempts >= max_attempts AND claimed_at + visibility_s < ?",
        (cutoff, cutoff)).rowcount
    if swept:
        con.execute(
            "INSERT INTO adjudication_ledger (judge, severity, verdict,"
            " reason_code, created_at) VALUES ('queue','P3','JOB_FAILED',"
            " 'lease expired at attempt cap; worker died without fail()', ?)",
            (cutoff,))
    con.commit()
    return claimed


def ack(con, job_id, extractor, extractor_version, output, document_sha256=None,
        page_label_map=None, kind_hint="substrate"):
    ts = now()
    cur = con.execute(
        "INSERT OR IGNORE INTO results (job_id, extractor, extractor_version, corpus_key,"
        " document_sha256, page_label_map, output, kind_hint, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (job_id, extractor, extractor_version, _corpus_of(con, job_id),
         document_sha256, json.dumps(page_label_map) if page_label_map else None,
         output, kind_hint, ts),
    )
    con.execute(
        "UPDATE jobs SET state='done', updated_at=? WHERE id=?", (ts, job_id)
    )
    con.commit()
    return cur.lastrowid


def fail(con, job_id, reason=""):
    row = con.execute("SELECT attempts, max_attempts FROM jobs WHERE id=?", (job_id,)).fetchone()
    if row is None:
        return
    dead = row["attempts"] >= row["max_attempts"]
    con.execute(
        "UPDATE jobs SET state=?, updated_at=? WHERE id=?",
        ("dead" if dead else "pending", now(), job_id),
    )
    con.execute(
        "INSERT INTO adjudication_ledger (judge, severity, verdict, reason_code, created_at)"
        " VALUES (?, ?, ?, ?, ?)",
        ("queue", "P3", "JOB_FAILED", str(reason)[:200], now()),
    )
    con.commit()


def record_verdicts(con, result_id, judge, judge_version, verdicts, extractor=None):
    ts = now()
    for v in verdicts:
        con.execute(
            "INSERT INTO adjudication_ledger (result_id, judge, judge_version, claim_index,"
            " severity, verdict, reason_code, evidence_quote, extractor, failure_class, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (result_id, judge, judge_version, v.get("claim_index"),
             v.get("severity", "P2"), v.get("verdict", "AMBIGUOUS"),
             v.get("reason_code"), v.get("evidence_quote"), extractor,
             v.get("failure_class"), ts),
        )
    con.commit()


def ledger_summary(con, since=None):
    q = ("SELECT failure_class, severity, COUNT(*) AS n FROM adjudication_ledger"
         " WHERE created_at > ? GROUP BY failure_class, severity ORDER BY n DESC")
    return [dict(r) for r in con.execute(q, (since or 0,)).fetchall()]


def _corpus_of(con, job_id):
    row = con.execute("SELECT corpus_key FROM jobs WHERE id=?", (job_id,)).fetchone()
    return row["corpus_key"] if row else "?"
