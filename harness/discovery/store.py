"""Design state store: designs, atoms, and pinned results (designs.db).

Product rule: discovery is a READ-ONLY consumer of the catalog; the only
mutable state is the engineer's design (atoms and answers). Results are
stored whole and stamped with the corpus release they ran against, so a
prior result stays reproducible after the corpus moves (spec section 45).
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

DEFAULT_DB = "/Volumes/M5_4TB/extract-results/designs.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS designs (
    design_id TEXT PRIMARY KEY,
    aisle TEXT NOT NULL DEFAULT 'power',
    name TEXT,
    corpus_release TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS atoms (
    atom_id INTEGER PRIMARY KEY AUTOINCREMENT,
    design_id TEXT NOT NULL REFERENCES designs(design_id),
    axis TEXT NOT NULL,
    op TEXT NOT NULL,
    value_json TEXT NOT NULL,
    hard INTEGER NOT NULL DEFAULT 1,
    margin_pct REAL,
    required_condition_json TEXT,
    state TEXT NOT NULL DEFAULT 'active',
    -- active | not_sure | dropped (spec sections 31-32: the engineer can
    -- always override the knife)
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_atoms_design ON atoms(design_id);
CREATE TABLE IF NOT EXISTS results (
    design_id TEXT NOT NULL REFERENCES designs(design_id),
    created_at REAL NOT NULL,
    corpus_release TEXT NOT NULL,
    summary_json TEXT NOT NULL,
    PRIMARY KEY (design_id, created_at)
);
"""


def connect(db_path: str | None = None) -> sqlite3.Connection:
    path = db_path or DEFAULT_DB
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA)
    return con


def now() -> float:
    return time.time()


def create_design(con: sqlite3.Connection, aisle: str = "power",
                  name: str | None = None) -> str:
    design_id = f"dw-{int(now() * 1000):013d}-{aisle[:4]}"
    ts = now()
    con.execute(
        "INSERT INTO designs (design_id, aisle, name, created_at, updated_at)"
        " VALUES (?,?,?,?,?)", (design_id, aisle, name, ts, ts))
    con.commit()
    return design_id


def upsert_atom(con: sqlite3.Connection, design_id: str, atom: dict[str, Any],
                replace: bool = False) -> None:
    """Add one atom; replace=True swaps an existing atom on the same axis."""
    ts = now()
    if replace:
        con.execute(
            "UPDATE atoms SET state='dropped', created_at=? "
            "WHERE design_id=? AND axis=? AND state='active'",
            (ts, design_id, atom["axis"]))
    con.execute(
        "INSERT INTO atoms (design_id, axis, op, value_json, hard,"
        " margin_pct, required_condition_json, state, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (design_id, atom["axis"], atom["op"],
         json.dumps(atom.get("value")), 1 if atom.get("hard", True) else 0,
         atom.get("margin_pct"),
         json.dumps(atom.get("required_condition"))
         if atom.get("required_condition") is not None else None,
         atom.get("state", "active"), ts))
    con.execute("UPDATE designs SET updated_at=? WHERE design_id=?",
                (ts, design_id))
    con.commit()


def active_atoms(con: sqlite3.Connection, design_id: str) -> list[dict]:
    rows = con.execute(
        "SELECT * FROM atoms WHERE design_id=? AND state='active'"
        " ORDER BY atom_id", (design_id,)).fetchall()
    out = []
    for r in rows:
        out.append({
            "axis": r["axis"], "op": r["op"],
            "value": json.loads(r["value_json"]),
            "hard": bool(r["hard"]),
            "margin_pct": r["margin_pct"],
            "required_condition": json.loads(r["required_condition_json"])
            if r["required_condition_json"] else None,
            "state": r["state"],
        })
    return out


def record_result(con: sqlite3.Connection, design_id: str,
                  corpus_release: str, summary: dict[str, Any]) -> None:
    con.execute(
        "INSERT INTO results (design_id, created_at, corpus_release,"
        " summary_json) VALUES (?,?,?,?)",
        (design_id, now(), corpus_release,
         json.dumps(summary, ensure_ascii=False, default=str)))
    con.execute("UPDATE designs SET corpus_release=?, updated_at=?"
                " WHERE design_id=?", (corpus_release, now(), design_id))
    con.commit()


def latest_result(con: sqlite3.Connection,
                  design_id: str) -> dict[str, Any] | None:
    row = con.execute(
        "SELECT * FROM results WHERE design_id=?"
        " ORDER BY created_at DESC LIMIT 1", (design_id,)).fetchone()
    if not row:
        return None
    return {"corpus_release": row["corpus_release"],
            "created_at": row["created_at"],
            "summary": json.loads(row["summary_json"])}
