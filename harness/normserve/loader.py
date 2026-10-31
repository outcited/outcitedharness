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
        self._fail_closed_preflight()
        self.con.executescript(schema_mod.SCHEMA_V3)
        self.con.commit()

    def _fail_closed_preflight(self):
        """NEO round-2 (hw-x9g): an existing store that predates
        norm-serve.v2 — or holds rows of another version, or already
        contains duplicate key groups — is REFUSED. Never auto-migrate
        live data; instruct instead."""
        table = self.con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='norm_claims'").fetchone()
        if not table:
            return
        cols = {r["name"] for r in
                self.con.execute("PRAGMA table_info(norm_claims)")}
        if "extractor_method" not in cols:
            raise Rejection(
                "existing store predates norm-serve.v2 (missing "
                "extractor_method column); refusing to open — export the "
                "rows and load into a new store; live data is never "
                "auto-migrated")
        if "conditions_sig" not in cols:
            raise Rejection(
                "existing store predates norm-serve.v3 (missing "
                "conditions_sig column); refusing to open — export the "
                "rows and load into a new store; live data is never "
                "auto-migrated")
        # NEO round-3 (8): an EMPTY store that still carries the OLD
        # no-page unique key would pass every row-level check but break
        # on the first second-page insert (IntegrityError). Detect the
        # old key definition and refuse before any load.
        auto_uq = self.con.execute(
            "SELECT name FROM pragma_index_list('norm_claims') "
            "WHERE origin='u' AND name LIKE 'sqlite_autoindex%'").fetchall()
        if auto_uq:
            raise Rejection(
                "existing store uses an inline pre-v2 UNIQUE key "
                f"({[r['name'] for r in auto_uq]}); refusing to open — "
                "export the rows and load into a new store; live data is "
                "never auto-migrated")
        uq = self.con.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' "
            "AND name='uq_norm_claims_key'").fetchone()
        if uq and uq["sql"]:
            ddl = uq["sql"].lower()
            if "page" not in ddl or "coalesce" not in ddl or                     "conditions_sig" not in ddl:
                raise Rejection(
                    "existing store has an OLD uq_norm_claims_key index "
                    "(no page or no conditions_sig in the key); "
                    "second-page or distinct-condition inserts would fail "
                    "or collapse — refusing to open; export the rows and "
                    "load into a new store; live data is never "
                    "auto-migrated")
        stored = {r[0] for r in self.con.execute(
            "SELECT DISTINCT schema_version FROM norm_claims")}
        stale = stored - {schema_mod.SCHEMA_VERSION}
        if stale:
            raise Rejection(
                f"existing store holds rows with schema_version {sorted(stale)} "
                f"(this code is {schema_mod.SCHEMA_VERSION}); refusing to open "
                "— export/reload explicitly; never auto-migrate live data")
        dups = self.con.execute(
            "SELECT COUNT(*) FROM (SELECT 1 FROM norm_claims "
            "GROUP BY opn, doc_sha, field, page, "
            "COALESCE(value_role,''), COALESCE(condition_verbatim,''), "
            "COALESCE(conditions_sig,'') "
            "HAVING COUNT(*) > 1)").fetchone()[0]
        if dups:
            raise Rejection(
                f"existing store has {dups} duplicate key group(s); "
                "refusing to create uq_norm_claims_key — dedupe "
                "explicitly (never silently); live data is never "
                "auto-migrated")

    def insert(self, row: dict):
        """Insert one typed row. Returns 'loaded' or 'duplicate'.

        A key collision with DIFFERENT content is a conflicting
        observation — raises Rejection (recorded, never silently
        ignored, never mislabeled as a duplicate)."""
        cols = ("opn", "doc_sha", "field", "value", "value_min", "value_max",
                "unit", "unit_as_printed", "value_role", "condition_verbatim",
                "conditions_sig",
                "verdict", "table_kind", "page", "verbatim", "span",
                "verified_by", "rule_version", "extractor_method",
                "evidence_ref", "source_url",
                "schema", "schema_version", "loaded_at")
        row = dict(row, loaded_at=time.time())
        placeholders = ",".join("?" for _ in cols)
        try:
            self.con.execute(
                f"INSERT INTO norm_claims ({','.join(cols)}) "
                f"VALUES ({placeholders})",
                tuple(row.get(c) for c in cols))
            self.con.commit()
            return "loaded"
        except sqlite3.IntegrityError:
            key = (row["opn"], row["doc_sha"], row["field"], row["page"],
                   row.get("value_role") or "",
                   row.get("condition_verbatim") or "",
                   row.get("conditions_sig") or "")
            existing = self.con.execute(
                "SELECT * FROM norm_claims WHERE opn=? AND doc_sha=? AND "
                "field=? AND page=? AND COALESCE(value_role,'')=? AND "
                "COALESCE(condition_verbatim,'')=? AND "
                "COALESCE(conditions_sig,'')=?", key).fetchone()
            self.con.rollback()
            if existing is None:
                raise
            diffs = [c for c in schema_mod.SUBSTANTIVE_COLS
                     if existing[c] != row.get(c)]
            if diffs:
                raise Rejection(
                    "conflicting observation on existing key "
                    f"(opn={key[0]!r} field={key[2]!r} page={key[3]}): "
                    f"differing columns {diffs} — refused loudly, never "
                    "silently ignored")
            return "duplicate"

    def claims_for_opn(self, opn: str):
        return [dict(r) for r in self.con.execute(
            "SELECT * FROM norm_claims WHERE opn=? ORDER BY field, page",
            (opn.upper(),)).fetchall()]


class LoadReport:
    def __init__(self):
        self.loaded = 0
        self.rejections = []   # {index, reason, row_snippet}
        self.holds = []        # {index, reason_code, detail, row_snippet}
        self.duplicates = 0

    def summary(self):
        return {"loaded": self.loaded, "rejected": len(self.rejections),
                "held": len(self.holds), "duplicates": self.duplicates,
                "rejections": self.rejections, "holds": self.holds}


def load_rows(store, rows, vault_root=None, dry_run=False,
              adapter=None, opn_default=None, ratified_rules=None):
    """Validate → vault-verify → ADMIT → store rows. Returns LoadReport.

    Admission gate (hw-x9g round 5) runs BEFORE store: rows that pass
    schema/vault checks but cannot be source-resolved — no structured
    observation, unbound field/role/unit, unprinted cell/header/OPN/
    condition, unverifiable receipt — are HELD with a reason code,
    recorded, never swallowed, never stored. Persist loads REQUIRE a
    vault root (mandatory re-verification + source re-resolution)."""
    from . import admission as admission_mod
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
            decision = admission_mod.evaluate(
                typed, raw, vault_root=vault_root,
                ratified_rules=ratified_rules)
            if not decision.served:
                report.holds.append({
                    "index": i, "reason_code": decision.reason_code,
                    "detail": decision.detail,
                    "row_snippet": json.dumps(raw, default=str)[:200]})
                continue
            if not dry_run:
                status = store.insert(typed)
                if status == "duplicate":
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
               opn_default=None, ratified_rules=None):
    rows = []
    with open(jsonl_path) as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return load_rows(store, rows, vault_root=vault_root, dry_run=dry_run,
                     opn_default=opn_default, ratified_rules=ratified_rules)
