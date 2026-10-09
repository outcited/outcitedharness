"""Canonical candidate identity store (PRD-FACET-03 R1).

facet_identity.db — experimental, derived, rebuildable. Identities
(category/subcategory/functional_class/manufacturer/family/series/opn) and
their relationships, each with an explicit authority, source hash, locator,
normalization rule, and status.

The fundamental invariant, enforced in code: a family relationship is an
engineering identity claim requiring evidence — not a string-matching
convenience.

- a relationship without authority + source sha + locator cannot exist;
- `unhashed:` placeholders are rejected as source hashes (no fabricated
  provenance);
- norm_rule comes from a closed set of evidence rules; there is no
  name-similarity rule;
- status is one-way: this store writes `proposed`/`ambiguous`/`unknown`
  only; `verified` requires promote() with an explicit approval reference
  (owner admission), and silent promotion is impossible;
- conflicting evidence is recorded as conflicting and never merged.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path

IDENTITY_SCHEMA = "harness.search-identity.v1"
DEFAULT_DB = "/Volumes/M5_4TB/extract-results/facet_identity.db"

KINDS = ("category", "subcategory", "functional_class", "manufacturer",
         "family", "series", "opn")
STATUSES = ("verified", "proposed", "ambiguous", "unknown")
REL_TYPES = ("contains", "member_of", "variant_of", "resolves_to")
# Closed set. A string-similarity rule does not exist and must not be added
# without owner review (PRD-FACET-03 R1).
NORM_RULES = ("front_matter_vocab_v1", "pairs_manifest", "vault_documents",
              "vault_mpns", "catalog_parts")

SCHEMA = """
CREATE TABLE IF NOT EXISTS identities (
    identity_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    label TEXT NOT NULL,
    manufacturer_id TEXT,
    authority TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'proposed',
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS relationships (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_id TEXT NOT NULL,
    child_id TEXT NOT NULL,
    rel_type TEXT NOT NULL,
    authority TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'proposed',
    source_sha256 TEXT NOT NULL,
    source_locator TEXT NOT NULL,
    norm_rule TEXT NOT NULL,
    confidence REAL,
    conflict_status TEXT,
    created_at REAL NOT NULL,
    UNIQUE (parent_id, child_id, rel_type)
);
CREATE INDEX IF NOT EXISTS idx_rel_child ON relationships(child_id);
CREATE INDEX IF NOT EXISTS idx_rel_parent ON relationships(parent_id);
CREATE TABLE IF NOT EXISTS provenance_map (
    stem TEXT PRIMARY KEY,
    resolved_sha256 TEXT,
    resolution TEXT,
    bytes_verified INTEGER NOT NULL DEFAULT 0,
    bytes_match INTEGER,
    quote_verified_claims INTEGER NOT NULL DEFAULT 0,
    quote_total_claims INTEGER NOT NULL DEFAULT 0,
    resolved_at REAL
);
CREATE TABLE IF NOT EXISTS provenance_conflicts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stem TEXT NOT NULL,
    sha_a TEXT NOT NULL, source_a TEXT NOT NULL,
    sha_b TEXT NOT NULL, source_b TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS quote_locator (
    stem TEXT NOT NULL,
    quote TEXT NOT NULL,
    page INTEGER,
    PRIMARY KEY (stem, quote)
);
CREATE TABLE IF NOT EXISTS releases (
    snapshot_id TEXT PRIMARY KEY,
    built_at REAL NOT NULL,
    counts_json TEXT NOT NULL,
    fingerprint TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
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


def _norm_label(label: str) -> str:
    return "-".join(str(label).strip().lower().split())


def identity_id_for(kind: str, label: str,
                    manufacturer_id: str | None = None) -> str:
    if kind not in KINDS:
        raise ValueError(f"unknown identity kind: {kind!r}")
    base = _norm_label(label)
    if kind in ("family", "series") and manufacturer_id:
        return f"{kind}:{manufacturer_id}:{base}"
    return f"{kind}:{base}"


def ensure_identity(con: sqlite3.Connection, kind: str, label: str, *,
                    manufacturer_id: str | None = None,
                    authority: str, status: str = "proposed") -> str:
    """Create-or-get an identity. This store never writes `verified` here —
    only promote() can, with an approval reference."""
    if status not in STATUSES:
        raise ValueError(f"unknown status: {status!r}")
    if status == "verified":
        raise ValueError(
            "identities are created proposed/ambiguous/unknown; verified "
            "requires promote() with an owner approval reference")
    if not (authority or "").strip():
        raise ValueError("authority is required (identity claims need a source)")
    iid = identity_id_for(kind, label, manufacturer_id)
    con.execute(
        "INSERT INTO identities (identity_id, kind, label, manufacturer_id,"
        " authority, status, created_at) VALUES (?,?,?,?,?,?,?)"
        " ON CONFLICT(identity_id) DO UPDATE SET"
        " authority=excluded.authority",
        (iid, kind, str(label).strip(), manufacturer_id, authority, status,
         now()))
    con.commit()
    return iid


def promote(con: sqlite3.Connection, identity_id: str, *,
            approval: str) -> None:
    """The ONLY path to `verified`. Requires an explicit owner approval
    reference; silent promotion is structurally impossible."""
    if not (approval or "").strip():
        raise ValueError("promotion requires an approval reference")
    cur = con.execute(
        "UPDATE identities SET status='verified' WHERE identity_id=?",
        (identity_id,))
    if not cur.rowcount:
        raise KeyError(identity_id)
    con.execute(
        "INSERT INTO meta (key, value) VALUES (?,?)"
        " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (f"approval:{identity_id}", approval))
    con.commit()


def add_relationship(con: sqlite3.Connection, parent_id: str, child_id: str,
                     rel_type: str, *, authority: str, source_sha256: str,
                     source_locator: dict, norm_rule: str,
                     status: str = "proposed",
                     confidence: float | None = None,
                     conflict_status: str | None = None) -> None:
    if rel_type not in REL_TYPES:
        raise ValueError(f"unknown rel_type: {rel_type!r}")
    if status not in STATUSES or status == "verified":
        raise ValueError("relationships are recorded proposed/ambiguous; "
                         "verified requires promote()")
    if norm_rule not in NORM_RULES:
        raise ValueError(
            f"norm_rule {norm_rule!r} not in the closed evidence set "
            f"{NORM_RULES} — name-similarity rules do not exist")
    if not (authority or "").strip():
        raise ValueError("authority is required")
    if not source_sha256 or source_sha256.startswith("unhashed:"):
        raise ValueError(
            "relationship source requires a real artifact sha256 — "
            "placeholder identities are not evidence")
    if isinstance(source_locator, dict):
        source_locator = json.dumps(source_locator, sort_keys=True,
                                    ensure_ascii=False)
    con.execute(
        "INSERT INTO relationships (parent_id, child_id, rel_type, authority,"
        " status, source_sha256, source_locator, norm_rule, confidence,"
        " conflict_status, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?)"
        " ON CONFLICT(parent_id, child_id, rel_type) DO UPDATE SET"
        " status=excluded.status, conflict_status=excluded.conflict_status,"
        " confidence=excluded.confidence, source_sha256=excluded.source_sha256,"
        " source_locator=excluded.source_locator",
        (parent_id, child_id, rel_type, authority, status, source_sha256,
         source_locator, norm_rule, confidence, conflict_status, now()))
    con.commit()


def mark_conflict(con: sqlite3.Connection, parent_a: str, parent_b: str,
                  child_id: str, rel_type: str) -> None:
    """Two authorities claim the same child: both rows stay, both are marked
    conflicting, neither wins (detectable, never silently merged)."""
    for parent in (parent_a, parent_b):
        con.execute(
            "UPDATE relationships SET conflict_status='conflicting',"
            " status='ambiguous' WHERE parent_id=? AND child_id=?"
            " AND rel_type=?", (parent, child_id, rel_type))
    con.commit()


def families_for_opn(con: sqlite3.Connection, opn: str,
                     include_conflicting: bool = False) -> list[dict]:
    rows = con.execute(
        "SELECT r.*, i.label, i.manufacturer_id, i.status AS id_status"
        " FROM relationships r JOIN identities i ON i.identity_id=r.parent_id"
        " WHERE r.child_id=? AND r.rel_type='contains' AND i.kind='family'"
        + ("" if include_conflicting
           else " AND (r.conflict_status IS NULL)"),
        (f"opn:{opn}",)).fetchall()
    return [dict(r) for r in rows]


def set_provenance(con: sqlite3.Connection, stem: str, *,
                   resolved_sha256: str | None, resolution: str | None,
                   bytes_verified: bool, bytes_match: bool | None,
                   quote_verified: int, quote_total: int) -> None:
    con.execute(
        "INSERT INTO provenance_map (stem, resolved_sha256, resolution,"
        " bytes_verified, bytes_match, quote_verified_claims,"
        " quote_total_claims, resolved_at)"
        " VALUES (?,?,?,?,?,?,?,?)"
        " ON CONFLICT(stem) DO UPDATE SET"
        " resolved_sha256=excluded.resolved_sha256,"
        " resolution=excluded.resolution,"
        " bytes_verified=excluded.bytes_verified,"
        " bytes_match=excluded.bytes_match,"
        " quote_verified_claims=excluded.quote_verified_claims,"
        " quote_total_claims=excluded.quote_total_claims,"
        " resolved_at=excluded.resolved_at",
        (stem, resolved_sha256, resolution, 1 if bytes_verified else 0,
         None if bytes_match is None else (1 if bytes_match else 0),
         quote_verified, quote_total, now()))
    con.commit()


def record_provenance_conflict(con: sqlite3.Connection, stem: str,
                               sha_a: str, source_a: str,
                               sha_b: str, source_b: str) -> None:
    """Distinct source revisions/artifacts for one stem stay
    distinguishable — never collapsed to whichever rung ran first."""
    existing = con.execute(
        "SELECT 1 FROM provenance_conflicts WHERE stem=? AND"
        " ((sha_a=? AND sha_b=?) OR (sha_a=? AND sha_b=?))",
        (stem, sha_a, sha_b, sha_b, sha_a)).fetchone()
    if existing:
        return
    con.execute(
        "INSERT INTO provenance_conflicts (stem, sha_a, source_a, sha_b,"
        " source_b, created_at) VALUES (?,?,?,?,?,?)",
        (stem, sha_a, source_a, sha_b, source_b, now()))
    con.commit()


def snapshot(con: sqlite3.Connection, payload: dict) -> dict:
    """Immutable, reproducible identity snapshot (R5). snapshot_id is the
    content hash of the canonical payload — rebuilding identical content
    returns the identical id."""
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                           default=str)
    fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    snapshot_id = f"identity-snapshot-v1-{fingerprint[:16]}"
    counts = {
        "identities": len(payload.get("candidates", [])),
        "families": len({p["identity_id"] for c in payload.get("candidates", [])
                         for p in c.get("parents", [])}),
    }
    con.execute(
        "INSERT INTO releases (snapshot_id, built_at, counts_json,"
        " fingerprint) VALUES (?,?,?,?)"
        " ON CONFLICT(snapshot_id) DO NOTHING",
        (snapshot_id, now(), json.dumps(counts), fingerprint))
    con.commit()
    return {"snapshot_id": snapshot_id, "schema": IDENTITY_SCHEMA,
            "counts": counts, "payload": payload}


def build_identity_snapshot(catalog_con: sqlite3.Connection,
                            identity_con: sqlite3.Connection, *,
                            category: str,
                            aisle_map: dict[str, str] | None = None,
                            category_of: dict[str, str] | None = None
                            ) -> dict:
    """The M4 candidate-identity contract (R5): canonical ids, grain,
    parents with authority + membership evidence, manufacturer identity,
    coverage/unknown flags. M4 never re-parses names — it consumes this.

    Deterministic: candidates sorted by canonical_id; evidence sorted; no
    timestamps inside the payload (they live on the snapshot row), so
    rebuilds of unchanged content hash identically.
    """
    from harness.search.vendors import resolve as resolve_vendor

    aisle_map = aisle_map or {}
    category_of = category_of or {}
    memberships: dict[str, list[sqlite3.Row]] = {}
    for row in identity_con.execute(
            "SELECT r.child_id, r.status, r.conflict_status, r.authority,"
            " r.source_sha256, r.source_locator, r.norm_rule,"
            " i.identity_id, i.label, i.kind"
            " FROM relationships r JOIN identities i"
            " ON i.identity_id = r.parent_id"
            " WHERE r.rel_type='contains' AND i.kind='family'"
            " ORDER BY r.child_id, i.identity_id"):
        memberships.setdefault(row["child_id"], []).append(row)

    candidates = []
    for opn, vendor_raw in catalog_con.execute(
            "SELECT opn, vendor FROM parts ORDER BY opn"):
        if category_of and category_of.get(opn.upper()) != category:
            continue
        vendor = resolve_vendor(vendor_raw)
        rows = memberships.get(f"opn:{opn}", [])
        parents = []
        for m in rows:
            locator = json.loads(m["source_locator"])
            parents.append({
                "identity_id": m["identity_id"], "kind": m["kind"],
                "label": m["label"], "status": m["status"],
                "conflict_status": m["conflict_status"],
                "authority": m["authority"], "norm_rule": m["norm_rule"],
                "evidence": [{"source_sha256": m["source_sha256"],
                              "page": locator.get("page"),
                              "quote": locator.get("quote")}]})
        clean = [p for p in parents if not p["conflict_status"]]
        if len(clean) == 1:
            family_flag = clean[0]["status"]      # proposed (never silent)
        elif len(clean) > 1:
            family_flag = "ambiguous"
        elif parents:
            family_flag = "conflicting"
        else:
            family_flag = "unknown"
        candidates.append({
            "canonical_id": f"opn:{opn}",
            "grain": "opn",
            "label": opn,
            "manufacturer_id": (f"mfr:{vendor['canonical']}"
                                if vendor["resolved"] else None),
            "manufacturer_raw": vendor_raw,
            "manufacturer_resolution": vendor["rule"],
            "parents": parents,
            "flags": {"family": family_flag}})
    return {
        "schema": "harness.search-identity-snapshot.v1",
        "category": category,
        "grain": "opn",
        "authority_note": ("relationships are printed-evidence proposals "
                           "until owner admission; verified==0 by design; "
                           "unknown is a first-class value"),
        "candidates": candidates}
