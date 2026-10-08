"""Canonical part store: part-keyed truth over doc-keyed claims.

catalog.db — the database customers query. pipeline.db holds work; this
holds PRODUCT. Four tables (parts, part_sources, claims_canonical,
consensus) and the engine that reconciles multi-document disagreement
into single truths with trails.
"""

import json
import re
import sqlite3
import time
from pathlib import Path

DEFAULT_DB = "/Volumes/M5_4TB/extract-results/catalog.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS parts (
    opn TEXT PRIMARY KEY,
    mpn_base TEXT,
    vendor TEXT,
    family TEXT,
    package TEXT,
    first_seen REAL NOT NULL,
    source_doc_shas TEXT DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_parts_mpn ON parts(mpn_base);
CREATE INDEX IF NOT EXISTS idx_parts_vendor ON parts(vendor);

CREATE TABLE IF NOT EXISTS part_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    opn TEXT NOT NULL REFERENCES parts(opn),
    doc_sha256 TEXT NOT NULL,
    coverage_kind TEXT NOT NULL,      -- primary | family | mention | ordering_table
    evidence TEXT,                    -- how we know (quote/filename/enumeration)
    created_at REAL NOT NULL,
    UNIQUE (opn, doc_sha256)
);
CREATE INDEX IF NOT EXISTS idx_ps_doc ON part_sources(doc_sha256);

CREATE TABLE IF NOT EXISTS claims_canonical (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    opn TEXT NOT NULL REFERENCES parts(opn),
    symbol TEXT NOT NULL,
    qualifier TEXT,
    condition_norm TEXT,
    value REAL,
    unit TEXT,
    value_text TEXT,
    provenance TEXT NOT NULL,         -- {quote, sha, page, row_header, column_header}
    extractor TEXT,
    extractor_version TEXT,
    doc_sha256 TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cc_opn ON claims_canonical(opn, symbol);
CREATE INDEX IF NOT EXISTS idx_cc_symbol ON claims_canonical(symbol, condition_norm);

CREATE TABLE IF NOT EXISTS consensus (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    opn TEXT NOT NULL REFERENCES parts(opn),
    symbol TEXT NOT NULL,
    qualifier TEXT,
    condition_norm TEXT,
    resolved_value REAL,
    resolved_unit TEXT,
    confidence REAL NOT NULL,         -- 0..1
    source_count INTEGER NOT NULL,
    conflict_class TEXT,              -- null | revision_diff | extraction_error |
                                      --   vendor_inconsistency | unresolved
    resolution_trail TEXT,            -- ordered steps taken
    updated_at REAL NOT NULL,
    UNIQUE (opn, symbol, qualifier, condition_norm)
);

CREATE TABLE IF NOT EXISTS doc_revisions (
    doc_sha256 TEXT PRIMARY KEY,
    rev_code TEXT,                    -- printed (A, B, DS40002339E, v2.1)
    rev_date TEXT,                    -- printed
    doc_kind TEXT,                    -- datasheet | reference_manual | family | errata
    supersedes_sha TEXT
);
"""


def connect(db_path: str = None):
    path = db_path or DEFAULT_DB
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA)
    return con


def now():
    return time.time()


OPN_TOKEN_RE = re.compile(
    r"^[A-Z0-9][A-Z0-9\-_/]{3,20}[A-Z0-9]$")
STOP_NAMES = {"ABSOLUTE", "MAXIMUM", "RATINGS", "ELECTRICAL", "CHARACTERISTICS",
             "RECOMMENDED", "OPERATING", "CONDITIONS", "TABLE", "NOTE", "NOTES",
             "DESCRIPTION", "ORDERING", "INFORMATION", "PACKAGE", "THERMAL"}


def plausible_opn(token: str) -> bool:
    t = token.strip().upper()
    if not OPN_TOKEN_RE.match(t):
        return False
    if t in STOP_NAMES:
        return False
    digits = sum(c.isdigit() for c in t)
    letters = sum(c.isalpha() for c in t)
    return digits >= 2 and letters >= 2 and digits + letters == len(t)


def register_part(con, opn, vendor=None, family=None, package=None,
                  doc_sha=None, coverage=None, evidence=None):
    opn = opn.strip().upper()
    ts = now()
    con.execute(
        "INSERT INTO parts (opn, vendor, family, package, first_seen, source_doc_shas)"
        " VALUES (?,?,?,?,?,?) ON CONFLICT(opn) DO UPDATE SET"
        " vendor=COALESCE(excluded.vendor, vendor),"
        " family=COALESCE(excluded.family, family),"
        " package=COALESCE(excluded.package, package)",
        (opn, vendor, family, package, ts, json.dumps([doc_sha] if doc_sha else [])))
    if doc_sha:
        con.execute(
            "INSERT OR IGNORE INTO part_sources (opn, doc_sha256, coverage_kind,"
            " evidence, created_at) VALUES (?,?,?,?,?)",
            (opn, doc_sha, coverage or "mention", evidence or "", ts))
    con.commit()
    return opn


def attach_claim(con, opn, claim: dict, doc_sha: str, extractor="unknown",
                 version="?", provenance=None):
    con.execute(
        "INSERT INTO claims_canonical (opn, symbol, qualifier, condition_norm,"
        " value, unit, value_text, provenance, extractor, extractor_version,"
        " doc_sha256, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (opn, (claim.get("symbol") or "").upper(), claim.get("qualifier"),
         _norm_condition(claim.get("condition")),
         _to_float(claim.get("value")), claim.get("unit"),
         str(claim.get("value")),
         json.dumps(provenance or {k: claim.get(k) for k in
                                    ("quote", "row_header", "column_header")} |
                    {"sha": doc_sha, "page": claim.get("page")}),
         extractor, version, doc_sha, now()))


def _norm_condition(cond):
    if not cond:
        return ""
    return re.sub(r"\s+", " ", str(cond).lower().strip())


def _to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


TOLERANCE = 1e-4


def resolve_consensus(con, opn, symbol):
    """One symbol's consensus across all attached claims. Returns the row."""
    rows = con.execute(
        "SELECT * FROM claims_canonical WHERE opn=? AND symbol=?",
        (opn, symbol)).fetchall()
    if not rows:
        return None
    by_condition = {}
    for r in rows:
        by_condition.setdefault((r["qualifier"], r["condition_norm"]), []).append(r)
    results = []
    for (qual, cond), group in by_condition.items():
        values = [g["value"] for g in group if g["value"] is not None]
        distinct_docs = {g["doc_sha256"] for g in group}
        trail = []
        conflict = None
        if not values:
            resolved, confidence = None, 0.0
        else:
            span = max(values) - min(values)
            rel = span / max(abs(max(values)), 1e-9)
            if len(distinct_docs) == 1:
                conflict, resolved, confidence = None, values[0], 0.6
                trail.append("single_source")
            elif rel <= TOLERANCE:
                conflict, resolved = None, max(values)
                confidence = min(0.95, 0.5 + 0.15 * len(distinct_docs))
                trail.append(f"agreement:{len(distinct_docs)}_docs")
            else:
                revs = {d: con.execute(
                    "SELECT rev_date, rev_code FROM doc_revisions WHERE doc_sha256=?",
                    (d,)).fetchone() for d in distinct_docs}
                dated = [(revs[d]["rev_date"] or "", g) for d, g in
                         ((g["doc_sha256"], g) for g in group) if revs.get(d)]
                if dated:
                    dated.sort(key=lambda x: x[0])
                    resolved = dated[-1][1]["value"]
                    conflict = "revision_diff"
                    confidence = 0.7
                    trail.append("resolved_by_revision_ordering")
                else:
                    resolved, conflict, confidence = None, "unresolved", 0.0
                    trail.append("spread=" + str(round(rel, 4)))
        results.append({
            "opn": opn, "symbol": symbol, "qualifier": qual,
            "condition_norm": cond, "resolved_value": resolved,
            "resolved_unit": (group[0]["unit"] if group else None),
            "confidence": confidence, "source_count": len(distinct_docs),
            "conflict_class": conflict,
            "resolution_trail": json.dumps(trail), "updated_at": now(),
        })
    for r in results:
        con.execute(
            "INSERT INTO consensus (opn, symbol, qualifier, condition_norm,"
            " resolved_value, resolved_unit, confidence, source_count,"
            " conflict_class, resolution_trail, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(opn, symbol, qualifier, condition_norm) DO UPDATE SET"
            " resolved_value=excluded.resolved_value, confidence=excluded.confidence,"
            " source_count=excluded.source_count, conflict_class=excluded.conflict_class,"
            " resolution_trail=excluded.resolution_trail, updated_at=excluded.updated_at",
            tuple(r[k] for k in ("opn", "symbol", "qualifier", "condition_norm",
                                 "resolved_value", "resolved_unit", "confidence",
                                 "source_count", "conflict_class",
                                 "resolution_trail", "updated_at")))
    con.commit()
    return results
