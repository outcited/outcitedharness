"""Loader: validate → vault-verify → store typed rows. Dry-run first.

Fail-closed per issue #64 negative tests:
  - a row missing ANY required provenance field (doc_sha/page/verbatim,
    plus rule_version + verified_by) is REJECTED, never stored;
  - a claim whose doc_sha doesn't re-verify against the vault is rejected;
  - every rejection is recorded, never swallowed.

Backends: SQLite locally (dry-run / proving); Postgres on x via URL
(never applied locally without authorization — loader ships as code,
DB access goes through the orchestrator/factory owner).
"""

import json
import sqlite3
import time
from pathlib import Path

from . import schema as schema_mod
from . import vault as vault_mod

try:
    import psycopg  # noqa: F401
    HAVE_PG = True
except ImportError:
    HAVE_PG = False


class Rejection(Exception):
    pass


class Store:
    def __init__(self, db_path: str):
        if db_path.startswith("postgres://") or db_path.startswith("postgresql://"):
            if not HAVE_PG:
                raise Rejection("psycopg not installed; PG target unavailable")
            raise Rejection(
                "PG writes require orchestrator/factory-owner authorization; "
                "use --dry-run or a SQLite path")
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(db_path, timeout=30, check_same_thread=False)
        self.con.row_factory = sqlite3.Row
        self.con.executescript(schema_mod.SCHEMA_V1)
        self.con.commit()

    def insert(self, row: dict):
        cols = ("opn", "doc_sha", "field", "value", "value_min", "value_max",
                "unit", "unit_as_printed", "value_role", "condition_verbatim",
                "verdict", "table_kind", "page", "verbatim", "span",
                "verified_by", "rule_version", "extractor_method",
                "evidence_ref", "source_url",
                "schema", "schema_version", "loaded_at")
        row = dict(row, loaded_at=time.time())
        placeholders = ",".join("?" for _ in cols)
        self.con.execute(
            f"INSERT OR IGNORE INTO norm_claims ({','.join(cols)}) "
            f"VALUES ({placeholders})",
            tuple(row.get(c) for c in cols))
        self.con.commit()

    def claims_for_opn(self, opn: str):
        return [dict(r) for r in self.con.execute(
            "SELECT * FROM norm_claims WHERE opn=? ORDER BY field, page",
            (opn.upper(),)).fetchall()]


class LoadReport:
    def __init__(self):
        self.loaded = 0
        self.rejections = []   # {index, reason, row_snippet}
        self.duplicates = 0

    def summary(self):
        return {"loaded": self.loaded, "rejected": len(self.rejections),
                "duplicates": self.duplicates,
                "rejections": self.rejections}


def load_rows(store, rows, vault_root=None, dry_run=False,
              adapter=None, opn_default=None):
    """Validate + store rows. Returns LoadReport. Dry-run validates and
    vault-verifies but writes nothing.

    Vault re-verification is MANDATORY on persist (NEO source review,
    merge 47cac640): a persist load without a vault root raises — the
    old `if vault_root:` silently skipped verification. Dry-run also
    verifies when a vault root is supplied.
    """
    if not dry_run and not vault_root:
        raise ValueError(
            "vault_root is mandatory for persist loads; verification "
            "cannot be skipped (fail-closed, merge 47cac640 review)")
    adapter = adapter or _default_adapter
    report = LoadReport()
    for i, raw in enumerate(rows):
        try:
            typed, reason = adapter(raw, opn_default=opn_default)
            if typed is None:
                raise Rejection(f"adapter: {reason}")
            errs = schema_mod.validate_typed_row(typed)
            if errs:
                raise Rejection("schema: " + "; ".join(errs))
            if vault_root:
                ok, detail = vault_mod.verify_sha256(vault_root, typed["doc_sha"])
                if not ok:
                    raise Rejection(f"vault: {detail}")
            if not dry_run:
                before = store.con.execute(
                    "SELECT COUNT(*) AS n FROM norm_claims").fetchone()["n"]
                store.insert(typed)
                after = store.con.execute(
                    "SELECT COUNT(*) AS n FROM norm_claims").fetchone()["n"]
                if after == before:
                    report.duplicates += 1
                else:
                    report.loaded += 1
            else:
                report.loaded += 1
        except Rejection as e:
            report.rejections.append({
                "index": i, "reason": str(e),
                "row_snippet": json.dumps(raw, default=str)[:200]})
        except Exception as e:  # unexpected — still recorded, never swallowed
            report.rejections.append({
                "index": i, "reason": f"unexpected: {type(e).__name__}: {e}",
                "row_snippet": json.dumps(raw, default=str)[:200]})
    return report


def _default_adapter(raw, opn_default=None):
    from .adapter import adapt_power_axes_row
    return adapt_power_axes_row(raw, opn_default=opn_default)


def load_jsonl(store, jsonl_path, vault_root=None, dry_run=False,
               opn_default=None):
    rows = []
    with open(jsonl_path) as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return load_rows(store, rows, vault_root=vault_root, dry_run=dry_run,
                     opn_default=opn_default)
