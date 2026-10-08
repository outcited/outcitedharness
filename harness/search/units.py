"""Evidence-unit store for engineering-evidence retrieval (PRD-SEARCH-01).

search_index.db — one database, WAL mode, safe to share between the indexer
(writer), the query service (reader), and the API. The units table is the
retrieval substrate the catalog and pipeline already imply but never built:
one row per retrievable piece of technical evidence at seven grains
(document, section, table, figure, claim, family, application), each with
full provenance in the EvidenceReference vocabulary (sha + page + locator).

Laws inherited from the platform (CONTEXT_HANDOFF.md):

- provenance is law: a unit without doc_sha256 + locator cannot exist;
- applicability is never widened: coverage_kind travels end-to-end, family
  evidence stays family evidence (surfaced, never converted to OPN);
- `verified` is M4's word alone: this store records adjudication state as
  reported upstream and never mints verdicts of its own;
- idempotent, version-keyed writes: the natural key is (doc_sha256, grain,
  locator, extraction_version) so a better extraction supersedes (new
  version), a re-run is a no-op, and a rejected claim flips
  verification_state without rewriting the printed text.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import struct
import time
from pathlib import Path
from typing import Any, Iterable, Sequence

SEARCH_SCHEMA = "harness.search-evidence-index.v1"
DEFAULT_DB = "/Volumes/M5_4TB/extract-results/search_index.db"

GRAINS = ("document", "section", "table", "figure", "claim", "family",
          "application")

DOC_CLASSES = ("datasheet", "reference_manual", "family", "errata", "unknown")

# Adjudication-facing verification states. `m4_verified` may only be set by
# copying M4 cell_verifier output (indexer annotation); `unverified` is the
# honest default for freshly extracted material.
VERIFICATION_STATES = ("unverified", "supported", "unsupported", "ambiguous",
                       "not_in_doc", "rejected", "m4_verified")

COVERAGE_KINDS = ("primary", "family", "mention", "ordering_table",
                  "burn-primary")

APPLICABILITY_SCOPES = ("opn", "family", "category")

SCHEMA = """
CREATE TABLE IF NOT EXISTS units (
    rid INTEGER PRIMARY KEY,
    unit_id TEXT NOT NULL UNIQUE,
    doc_sha256 TEXT NOT NULL,
    grain TEXT NOT NULL,
    page INTEGER,
    locator TEXT NOT NULL,
    text_repr TEXT NOT NULL,
    structured TEXT,
    ident TEXT,
    vendor TEXT,
    doc_class TEXT,
    category TEXT,
    family TEXT,
    applicability TEXT NOT NULL DEFAULT '[]',
    rev_code TEXT,
    rev_date TEXT,
    extraction_version TEXT NOT NULL,
    verification_state TEXT NOT NULL DEFAULT 'unverified',
    verification_source TEXT,
    retired INTEGER NOT NULL DEFAULT 0,
    retired_reason TEXT,
    created_at REAL NOT NULL,
    UNIQUE (doc_sha256, grain, locator, extraction_version)
);
CREATE INDEX IF NOT EXISTS idx_units_doc ON units(doc_sha256);
CREATE INDEX IF NOT EXISTS idx_units_grain ON units(grain);
CREATE INDEX IF NOT EXISTS idx_units_category ON units(category);
CREATE INDEX IF NOT EXISTS idx_units_vendor ON units(vendor, family);
CREATE INDEX IF NOT EXISTS idx_units_family ON units(family);
CREATE INDEX IF NOT EXISTS idx_units_state ON units(verification_state);

CREATE VIRTUAL TABLE IF NOT EXISTS units_fts USING fts5(
    text_norm, vendor, family, unit_id UNINDEXED
);
CREATE VIRTUAL TABLE IF NOT EXISTS ident_fts USING fts5(
    ident, unit_id UNINDEXED, tokenize='trigram'
);

CREATE TABLE IF NOT EXISTS vectors (
    unit_id TEXT PRIMARY KEY,
    model TEXT NOT NULL,
    dim INTEGER NOT NULL,
    vec BLOB NOT NULL
);

CREATE TABLE IF NOT EXISTS releases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    release TEXT NOT NULL,
    built_at REAL NOT NULL,
    unit_count INTEGER NOT NULL,
    fingerprint TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


# --- technical text normalization (R2: symbol and unit-aware search) ------
#
# Datasheets print "RDS(on),max", "100mA", "3.3V", "TA=25C". Engineers type
# "rdson", "100 ma", "3v3". Both sides pass through the same normalizer so
# FTS tokens meet regardless of glue: digit<->letter boundaries split,
# punctuation becomes space, known glued engineering terms fold.

_TERM_ALIASES = {
    "rdson": "rds on",
    "rds_on": "rds on",
    "vgsth": "vgs th",
    "vgs_th": "vgs th",
    "idss": "i dss",
    "3v3": "3 3 v",
    "5v5": "5 5 v",
}

_BOUNDARY = re.compile(r"(?<=[0-9])(?=[a-zA-Z])|(?<=[a-zA-Z])(?=[0-9])")
_NONALNUM = re.compile(r"[^a-z0-9]+")
_WS = re.compile(r"\s+")


def technical_normalize(text: str) -> str:
    if not text:
        return ""
    lowered = text.lower()
    # Glued engineering terms fold BEFORE boundary splitting, so "3v3"
    # and the printed "3.3 V" meet at "3 3 v".
    for alias, replacement in _TERM_ALIASES.items():
        lowered = re.sub(rf"\b{alias}\b", replacement, lowered)
    folded = _BOUNDARY.sub(" ", lowered)
    folded = _NONALNUM.sub(" ", folded)
    return _WS.sub(" ", " ".join(folded.split())).strip()


def pack_vector(values: Sequence[float]) -> bytes:
    rows = [float(v) for v in values]
    return struct.pack(f"{len(rows)}f", *rows)


def unpack_vector(blob: bytes) -> tuple[float, ...]:
    count = len(blob) // 4
    return struct.unpack(f"{count}f", blob)


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    an = sum(x * x for x in a)
    bn = sum(x * x for x in b)
    if an <= 0 or bn <= 0:
        return 0.0
    return dot / math.sqrt(an * bn)


# --- unit construction -----------------------------------------------------

def canonical_locator(locator: Any) -> str:
    """Locator JSON with sorted keys — the identity of a unit's position.

    Vocabulary mirrors EvidenceReference (harness/electronics/models.py):
    {bbox:[x0,y0,x1,y1], table_index, row_index, column_index, span:[c0,c1],
    caption_anchor, section, heading, kind}
    """
    if isinstance(locator, str):
        locator = json.loads(locator)
    if not isinstance(locator, dict) or not locator:
        raise ValueError("locator must be a non-empty dict")
    return json.dumps(locator, sort_keys=True, ensure_ascii=False)


def unit_id_for(doc_sha256: str, grain: str, locator_json: str,
                extraction_version: str) -> str:
    digest = hashlib.sha256()
    digest.update(doc_sha256.encode("ascii", errors="replace"))
    digest.update(b"|")
    digest.update(grain.encode("ascii", errors="replace"))
    digest.update(b"|")
    digest.update(locator_json.encode("utf-8"))
    digest.update(b"|")
    digest.update(extraction_version.encode("utf-8"))
    return digest.hexdigest()[:32]


def _validate_applicability(items: Any) -> list[dict]:
    if items is None:
        return []
    if isinstance(items, str):
        items = json.loads(items)
    out = []
    for item in items:
        if item.get("scope") not in APPLICABILITY_SCOPES:
            raise ValueError(f"bad applicability scope: {item.get('scope')!r}")
        if not item.get("value"):
            raise ValueError("applicability requires a value")
        kind = item.get("coverage_kind")
        if kind is not None and kind not in COVERAGE_KINDS:
            raise ValueError(f"bad coverage_kind: {kind!r}")
        out.append({"scope": item["scope"], "value": item["value"],
                    "coverage_kind": kind or "mention"})
    return out


def make_unit(*, doc_sha256: str, grain: str, locator: Any, text_repr: str,
              extraction_version: str, page: int | None = None,
              vendor: str | None = None, doc_class: str | None = None,
              category: str | None = None, family: str | None = None,
              ident: Iterable[str] | None = None,
              applicability: Any = None, rev_code: str | None = None,
              rev_date: str | None = None,
              verification_state: str = "unverified",
              verification_source: str | None = None,
              structured: dict | None = None) -> dict:
    """Validate and stamp one evidence unit. Raises on any law violation."""
    if grain not in GRAINS:
        raise ValueError(f"unknown grain: {grain!r}")
    if verification_state not in VERIFICATION_STATES:
        raise ValueError(f"unknown verification_state: {verification_state!r}")
    if verification_state == "m4_verified" and not verification_source:
        raise ValueError("m4_verified requires a verification_source")
    if doc_class is not None and doc_class not in DOC_CLASSES:
        raise ValueError(f"unknown doc_class: {doc_class!r}")
    if page is not None and page < 1:
        raise ValueError("page is 1-based")
    if not (doc_sha256 or "").strip():
        raise ValueError("doc_sha256 is required (provenance is law)")
    if not (text_repr or "").strip():
        raise ValueError("text_repr is required (no text -> no unit)")
    locator_json = canonical_locator(locator)
    if page is None:
        page_val = locator.get("page") if isinstance(locator, dict) else None
        if isinstance(page_val, int) and page_val >= 1:
            page = page_val
    ident_tokens = " ".join(
        t for t in (str(i).strip() for i in (ident or [])) if t)
    return {
        "unit_id": unit_id_for(doc_sha256, grain, locator_json,
                               extraction_version),
        "evidence_id": evidence_id_for(doc_sha256, grain, locator_json),
        "doc_sha256": doc_sha256,
        "grain": grain,
        "page": page,
        "locator": json.loads(locator_json),
        "locator_json": locator_json,
        "text_repr": text_repr,
        "structured": structured,
        "ident": ident_tokens or None,
        "vendor": vendor,
        "doc_class": doc_class,
        "category": category,
        "family": family,
        "applicability": _validate_applicability(applicability),
        "rev_code": rev_code,
        "rev_date": rev_date,
        "extraction_version": extraction_version,
        "verification_state": verification_state,
        "verification_source": verification_source,
    }


# --- store -----------------------------------------------------------------

def connect(db_path: str | None = None) -> sqlite3.Connection:
    path = db_path or DEFAULT_DB
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    # WAL + NORMAL: safe to lose the tail of a bulk load on power loss —
    # the index is a derived artifact, rebuilt from catalog + waves.
    con.execute("PRAGMA synchronous=NORMAL")
    con.executescript(SCHEMA)
    try:  # migration for pre-structured indexes (v0 pilot dbs)
        con.execute("ALTER TABLE units ADD COLUMN structured TEXT")
        con.commit()
    except sqlite3.OperationalError:
        pass
    return con


# --- provenance serving gate (P1 directive 2026-10-08) --------------------
#
# evidence_grade decides how a unit may be PRESENTED, never whether it is
# searchable. discovery_only material (placeholder doc identities such as
# ``unhashed:`` stems, or no reliable locator: neither page nor bbox) stays
# fully searchable but is visibly marked and can never be presented as
# verified evidence. evidence_grade material carries a real artifact hash
# and a precise locator.

UNHASHED_PREFIX = "unhashed:"


def evidence_grade(*, doc_sha256: str, page: int | None,
                   locator: dict | None) -> str:
    if not (doc_sha256 or "").startswith(UNHASHED_PREFIX) and \
            (page is not None or bool(locator and locator.get("bbox"))):
        return "evidence_grade"
    return "discovery_only"


def unit_evidence_grade(unit: dict) -> str:
    """Grade for a unit dict (make_unit output or row_to_unit row)."""
    locator = unit.get("locator")
    if isinstance(locator, str):
        locator = json.loads(locator)
    return evidence_grade(doc_sha256=unit.get("doc_sha256") or "",
                          page=unit.get("page"), locator=locator)


def evidence_id_for(doc_sha256: str, grain: str, locator_json: str) -> str:
    """Durable evidence identity: stable across re-indexing and extraction
    version bumps (unit_id changes when the extraction changes; evidence_id
    does not). This is the handle downstream consumers cite."""
    digest = hashlib.sha256()
    digest.update(doc_sha256.encode("ascii", errors="replace"))
    digest.update(b"|")
    digest.update(grain.encode("ascii", errors="replace"))
    digest.update(b"|")
    digest.update(locator_json.encode("utf-8"))
    return "ev-" + digest.hexdigest()[:24]


def now() -> float:
    return time.time()


def _delete_unit_rows(con: sqlite3.Connection, unit_id: str) -> None:
    # FTS5 rowid == units.rid, so FTS deletes are O(log n); unit_id itself
    # is UNINDEXED in the FTS tables and must never be deleted by value.
    row = con.execute("SELECT rid FROM units WHERE unit_id=?",
                      (unit_id,)).fetchone()
    con.execute("DELETE FROM vectors WHERE unit_id = ?", (unit_id,))
    con.execute("DELETE FROM units WHERE unit_id = ?", (unit_id,))
    if row is not None:
        rid = row[0]
        con.execute("DELETE FROM units_fts WHERE rowid = ?", (rid,))
        con.execute("DELETE FROM ident_fts WHERE rowid = ?", (rid,))


def _insert_unit(con: sqlite3.Connection, unit: dict, ts: float) -> None:
    text_norm = technical_normalize(unit["text_repr"])
    cur = con.execute(
        "INSERT INTO units (unit_id, doc_sha256, grain, page, locator,"
        " text_repr, structured, ident, vendor, doc_class, category,"
        " family, applicability, rev_code, rev_date, extraction_version,"
        " verification_state, verification_source, retired, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?)",
        (unit["unit_id"], unit["doc_sha256"], unit["grain"], unit["page"],
         unit["locator_json"], unit["text_repr"],
         json.dumps(unit.get("structured"), ensure_ascii=False)
         if unit.get("structured") is not None else None,
         unit["ident"], unit["vendor"],
         unit["doc_class"], unit["category"], unit["family"],
         json.dumps(unit["applicability"], ensure_ascii=False),
         unit["rev_code"], unit["rev_date"], unit["extraction_version"],
         unit["verification_state"], unit["verification_source"], ts))
    rid = cur.lastrowid
    con.execute(
        "INSERT INTO units_fts (rowid, text_norm, vendor, family, unit_id)"
        " VALUES (?,?,?,?,?)",
        (rid, text_norm, (unit["vendor"] or "").lower(),
         technical_normalize(unit["family"] or ""), unit["unit_id"]))
    if unit["ident"]:
        con.execute(
            "INSERT INTO ident_fts (rowid, ident, unit_id) VALUES (?,?,?)",
            (rid, technical_normalize(unit["ident"]), unit["unit_id"]))


def replace_document(con: sqlite3.Connection, units: list[dict],
                     *, doc_sha256: str | None = None,
                     extraction_version: str | None = None,
                     commit: bool = True) -> dict:
    """Idempotently write one document's units at one extraction version.

    Everything for (doc_sha256, extraction_version) that is not in the new
    set is removed (claim rejected / extraction changed), matching rows are
    replaced in place, and the release is marked dirty. Re-running with the
    same input is a no-op.
    """
    if not units:
        raise ValueError("refusing to replace a document with zero units")
    # Same natural key = same evidence: duplicates within one submission
    # collapse (last wins) rather than violating the natural-key constraint.
    by_id: dict[str, dict] = {}
    for u in units:
        by_id[u["unit_id"]] = u
    units = list(by_id.values())
    doc = doc_sha256 or units[0]["doc_sha256"]
    version = extraction_version or units[0]["extraction_version"]
    for u in units:
        if u["doc_sha256"] != doc or u["extraction_version"] != version:
            raise ValueError(
                "all units must share doc_sha256 and extraction_version")
    existing = {r[0] for r in con.execute(
        "SELECT unit_id FROM units WHERE doc_sha256=? AND extraction_version=?",
        (doc, version))}
    incoming = {u["unit_id"] for u in units}
    ts = now()
    for stale in sorted(existing - incoming):
        _delete_unit_rows(con, stale)
    for u in units:
        if u["unit_id"] in existing:
            _delete_unit_rows(con, u["unit_id"])
        _insert_unit(con, u, ts)
    con.execute(
        "INSERT INTO meta (key, value) VALUES ('release_dirty','1')"
        " ON CONFLICT(key) DO UPDATE SET value='1'")
    if commit:
        con.commit()
    return {"doc_sha256": doc, "extraction_version": version,
            "units": len(units), "replaced": len(existing & incoming),
            "removed": len(existing - incoming),
            "added": len(incoming - existing)}


def retire_document(con: sqlite3.Connection, doc_sha256: str,
                    reason: str) -> int:
    """Soft-retire every unit of a replaced/superseded document."""
    cur = con.execute(
        "UPDATE units SET retired=1, retired_reason=? WHERE doc_sha256=?"
        " AND retired=0", (reason, doc_sha256))
    if cur.rowcount:
        con.execute(
            "INSERT INTO meta (key, value) VALUES ('release_dirty','1')"
            " ON CONFLICT(key) DO UPDATE SET value='1'")
    con.commit()
    return cur.rowcount


def attach_vector(con: sqlite3.Connection, unit_id: str, model: str,
                  values: Sequence[float], commit: bool = True) -> None:
    con.execute(
        "INSERT INTO vectors (unit_id, model, dim, vec) VALUES (?,?,?,?)"
        " ON CONFLICT(unit_id) DO UPDATE SET model=excluded.model,"
        " dim=excluded.dim, vec=excluded.vec",
        (unit_id, model, len(values), pack_vector(values)))
    if commit:
        con.commit()


def set_model_fingerprint(con: sqlite3.Connection, fingerprint: str) -> None:
    con.execute(
        "INSERT INTO meta (key, value) VALUES ('embed_model_fingerprint',?)"
        " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (fingerprint,))
    con.commit()


def purge_vectors(con: sqlite3.Connection) -> int:
    """Drop every vector — required when served weights change identity
    (two weight spaces in one cosine index is invalid)."""
    n = con.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]
    con.execute("DELETE FROM vectors")
    con.execute(
        "INSERT INTO meta (key, value) VALUES ('release_dirty','1')"
        " ON CONFLICT(key) DO UPDATE SET value='1'")
    con.commit()
    return n


def vectors_available(con: sqlite3.Connection) -> bool:
    row = con.execute("SELECT 1 FROM vectors LIMIT 1").fetchone()
    return row is not None


def row_to_unit(row: sqlite3.Row) -> dict:
    unit = dict(row)
    unit.pop("rid", None)
    unit["applicability"] = json.loads(unit["applicability"])
    unit["locator"] = json.loads(unit["locator"])
    unit["retired"] = bool(unit["retired"])
    if unit.get("structured") is not None:
        unit["structured"] = json.loads(unit["structured"])
    unit["evidence_id"] = evidence_id_for(
        unit["doc_sha256"], unit["grain"], json.dumps(
            unit["locator"], sort_keys=True, ensure_ascii=False))
    unit["evidence_grade"] = unit_evidence_grade(unit)
    return unit


def get_unit(con: sqlite3.Connection, unit_id: str) -> dict | None:
    row = con.execute("SELECT * FROM units WHERE unit_id=?",
                      (unit_id,)).fetchone()
    return row_to_unit(row) if row else None


def unit_count(con: sqlite3.Connection, active_only: bool = True) -> int:
    sql = "SELECT COUNT(*) FROM units" + (" WHERE retired=0" if active_only
                                          else "")
    return con.execute(sql).fetchone()[0]


# --- release pinning (R4/R5: reproducible index release and rollback) -----

VECTOR_INDEX_VERSION = "brutefloat32-v1"

MODEL_PROBE_TEXT = "harness.search.model-probe.v1"


def model_fingerprint(embed) -> str:
    """Content fingerprint of the SERVED WEIGHTS, not the model name.

    Same name can carry different weights (observed 2026-10-08: e10b
    swapped checkpoints under bge-m3-cr-tapes-v1). A fixed probe text is
    embedded and the first 16 values are hashed — a weight change changes
    the fingerprint and therefore the retrieval release identity.
    """
    vector = list(embed([MODEL_PROBE_TEXT])[0])
    digest = hashlib.sha256()
    for value in vector[:16]:
        digest.update(f"{float(value):.6f}".encode("ascii"))
    return digest.hexdigest()[:12]


def vector_identity(con: sqlite3.Connection) -> str:
    """Which embedding models, their weight fingerprints, and counts."""
    rows = con.execute(
        "SELECT model, COUNT(*) AS n FROM vectors GROUP BY model"
        " ORDER BY model").fetchall()
    fp_row = con.execute(
        "SELECT value FROM meta WHERE key='embed_model_fingerprint'"
    ).fetchone()
    fp = fp_row[0] if fp_row else "unfingerprinted"
    if not rows:
        return f"{VECTOR_INDEX_VERSION};models=none;fp={fp}"
    return (f"{VECTOR_INDEX_VERSION};fp={fp};models=" + ",".join(
        f"{r['model']}:{r['n']}" for r in rows))


def _fingerprint(con: sqlite3.Connection, policy_identity: str = "") \
        -> tuple[str, int, int]:
    """Content-addressed: same release id <=> same index content AND the
    same retrieval policy. Text, verification state, ranking-policy
    identity, and vector identity are hashed, so neither a rewritten
    extraction nor a changed ranking system can hide behind a stable id."""
    digest = hashlib.sha256()
    count = 0
    active = 0
    for unit_id, text_repr, state, retired in con.execute(
            "SELECT unit_id, text_repr, verification_state, retired"
            " FROM units ORDER BY unit_id"):
        digest.update(unit_id.encode("ascii"))
        digest.update(b"\0")
        digest.update((text_repr or "").encode("utf-8", errors="replace"))
        digest.update(b"\0")
        digest.update((state or "").encode("ascii"))
        digest.update(b"|")
        digest.update(b"1" if retired else b"0")
        count += 1
        active += 0 if retired else 1
    digest.update(b"policy|" + policy_identity.encode("utf-8"))
    digest.update(b"vectors|" + vector_identity(con).encode("ascii"))
    return digest.hexdigest(), count, active


def index_release(con: sqlite3.Connection, force: bool = False,
                  policy_identity: str = "") -> dict:
    """Content fingerprint of the index; cached until the next write.

    A different policy_identity (ranking policy changed upstream) forces a
    recompute so the release id never lies about the retrieval system.
    Rollback = restore the previous database file (runbook); this
    fingerprint is what lets two restores be told apart.
    """
    stored_policy = con.execute(
        "SELECT value FROM meta WHERE key='policy_identity'").fetchone()
    policy_changed = policy_identity and \
        (stored_policy is None or stored_policy[0] != policy_identity)
    dirty_row = con.execute(
        "SELECT value FROM meta WHERE key='release_dirty'").fetchone()
    dirty = dirty_row is not None and dirty_row[0] == "1"
    cached = con.execute(
        "SELECT value FROM meta WHERE key='release'").fetchone()
    if cached and not dirty and not force and not policy_changed:
        count = int(con.execute(
            "SELECT value FROM meta WHERE key='release_unit_count'"
        ).fetchone()[0])
        active = int(con.execute(
            "SELECT value FROM meta WHERE key='release_active_count'"
        ).fetchone()[0])
        release = cached[0]
    else:
        fp, count, active = _fingerprint(con, policy_identity)
        release = f"search-release-v1-{fp[:12]}"
        ts = now()
        for key, value in (("release", release), ("release_dirty", "0"),
                           ("release_unit_count", str(count)),
                           ("release_active_count", str(active)),
                           ("policy_identity", policy_identity)):
            con.execute(
                "INSERT INTO meta (key, value) VALUES (?,?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value))
        con.execute("INSERT INTO releases (release, built_at, unit_count,"
                    " fingerprint) VALUES (?,?,?,?)", (release, ts, count,
                                                       fp))
        con.commit()
    return {"release": release, "schema": SEARCH_SCHEMA, "unit_count": count,
            "active_count": active,
            "policy_identity": policy_identity or
            (stored_policy[0] if stored_policy else ""),
            "vector_identity": vector_identity(con)}
