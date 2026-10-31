"""Typed versioned schema for normalized claims (NORM-SERVE-01).

One row per OPN+sha256+field. Named typed fields — NOT a JSONB dump.
Provenance columns: doc_sha, page, span, verbatim, verified_by,
rule_version, evidence_ref. doc_sha / page / verbatim / verified_by /
rule_version are REQUIRED — a row missing any is rejected by the loader
and never served (issue #64 negative tests).
"""

SCHEMA_VERSION = "norm-serve.v1"

REQUIRED_PROVENANCE = ("doc_sha", "page", "verbatim", "verified_by",
                       "rule_version")

SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS norm_claims (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    opn TEXT NOT NULL,
    doc_sha TEXT NOT NULL,
    field TEXT NOT NULL,
    value REAL,
    value_min REAL,
    value_max REAL,
    unit TEXT,
    unit_as_printed TEXT,
    value_role TEXT,
    condition_verbatim TEXT,
    verdict TEXT,
    table_kind TEXT,
    page INTEGER NOT NULL,
    verbatim TEXT NOT NULL,
    span TEXT,
    verified_by TEXT NOT NULL,
    rule_version TEXT NOT NULL,
    extractor_method TEXT,
    evidence_ref TEXT,
    source_url TEXT,
    schema TEXT,
    schema_version TEXT NOT NULL,
    loaded_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_norm_claims_opn ON norm_claims(opn);
CREATE INDEX IF NOT EXISTS idx_norm_claims_doc ON norm_claims(doc_sha);
-- Null-safe uniqueness: SQL UNIQUE treats NULLs as distinct, so a plain
-- UNIQUE over nullable value_role/condition_verbatim admits duplicate
-- rows on replay in PG. COALESCE folds NULL to '' for the key.
CREATE UNIQUE INDEX IF NOT EXISTS uq_norm_claims_key
    ON norm_claims(opn, doc_sha, field,
                   COALESCE(value_role, ''),
                   COALESCE(condition_verbatim, ''));
"""

# Postgres dialect (PG on x — mfg_dosier). Applied by the loader when the
# target is a Postgres URL; never applied locally without authorization.
SCHEMA_V1_PG = """
CREATE TABLE IF NOT EXISTS norm_claims (
    id BIGSERIAL PRIMARY KEY,
    opn TEXT NOT NULL,
    doc_sha TEXT NOT NULL,
    field TEXT NOT NULL,
    value DOUBLE PRECISION,
    value_min DOUBLE PRECISION,
    value_max DOUBLE PRECISION,
    unit TEXT,
    unit_as_printed TEXT,
    value_role TEXT,
    condition_verbatim TEXT,
    verdict TEXT,
    table_kind TEXT,
    page INTEGER NOT NULL,
    verbatim TEXT NOT NULL,
    span TEXT,
    verified_by TEXT NOT NULL,
    rule_version TEXT NOT NULL,
    extractor_method TEXT,
    evidence_ref TEXT,
    source_url TEXT,
    schema TEXT,
    schema_version TEXT NOT NULL,
    loaded_at DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_norm_claims_opn ON norm_claims(opn);
CREATE INDEX IF NOT EXISTS idx_norm_claims_doc ON norm_claims(doc_sha);
CREATE UNIQUE INDEX IF NOT EXISTS uq_norm_claims_key
    ON norm_claims(opn, doc_sha, field,
                   COALESCE(value_role, ''),
                   COALESCE(condition_verbatim, ''));
"""


def validate_typed_row(row: dict):
    """Return list of contract violations; empty list means valid.

    Fail-closed: missing provenance never reaches storage.
    """
    errors = []
    import re
    if not row.get("opn") or not str(row["opn"]).strip():
        errors.append("missing opn")
    sha = row.get("doc_sha") or ""
    if not re.fullmatch(r"[0-9a-f]{64}", sha):
        errors.append(f"doc_sha not 64-hex: {sha!r}")
    page = row.get("page")
    if not isinstance(page, int) or page < 1:
        errors.append(f"page not positive int: {page!r}")
    if not (row.get("verbatim") or "").strip():
        errors.append("missing verbatim")
    if not (row.get("verified_by") or "").strip():
        errors.append("missing verified_by")
    if not (row.get("rule_version") or "").strip():
        errors.append("missing rule_version")
    if not (row.get("field") or "").strip():
        errors.append("missing field")
    has_value = row.get("value") is not None
    has_range = row.get("value_min") is not None or \
        row.get("value_max") is not None
    if not (has_value or has_range):
        errors.append("no value (value or value_min/value_max required)")
    errors.extend(_entailment_errors(row))
    return errors


def _fmt_num(v):
    """9.0 -> '9', 2.3 -> '2.3' (how the value prints in a datasheet)."""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _num_in_verbatim(verbatim, v):
    """Digit-boundary substring match: claimed number must appear printed
    in the quote (9 matches '9 V' and '9.0' but not '19' or '310')."""
    import re
    return re.search(r"(?<!\d)" + re.escape(_fmt_num(v)) + r"(?!\d)",
                     verbatim) is not None


def _entailment_errors(row):
    """The verbatim span-quote must entail the claimed value(s).

    A value that appears nowhere in the quoted text is an over-claim
    (e.g. vout=9 from a versions-availability line) — rejected here,
    never stored or served.
    """
    errors = []
    verbatim = row.get("verbatim") or ""
    if not verbatim.strip():
        return errors  # missing-verbatim already reported
    checked = []
    if row.get("value") is not None:
        checked.append(("value", row["value"]))
    if row.get("value_min") is not None:
        checked.append(("value_min", row["value_min"]))
    if row.get("value_max") is not None:
        checked.append(("value_max", row["value_max"]))
    for name, v in checked:
        if not _num_in_verbatim(verbatim, v):
            errors.append(
                f"verbatim does not entail {name}={_fmt_num(v)} "
                f"(field {row.get('field')!r}): over-claim rejected")
    return errors
