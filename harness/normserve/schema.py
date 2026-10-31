"""Typed versioned schema for normalized claims (NORM-SERVE-01).

One row per OPN+sha256+field+page. Named typed fields — NOT a JSONB dump.
Provenance columns: doc_sha, page, span, verbatim, verified_by,
rule_version, evidence_ref. doc_sha / page / verbatim / verified_by /
rule_version are REQUIRED — a row missing any is rejected by the loader
and never served (issue #64 negative tests).

v2 (hw-x9g, NEO round-2 review at head 61e95297):
  - SCHEMA_VERSION bumped to "norm-serve.v2" (extractor_method column +
    widened unique key); existing stores are fail-closed detected and
    REFUSED by the loader — live data is never auto-migrated.
  - unique key widened with `page`: distinct page-level observations are
    distinct rows, never silently deduped away.
  - verified_by restricted to an explicit evidence-backed vocabulary.
  - entailment gate is field-bound: a claimed value must be entailed in
    the quoted row AFTER the field's own label — numeric token presence
    alone is not entailment.
"""

import re

SCHEMA_VERSION = "norm-serve.v2"

REQUIRED_PROVENANCE = ("doc_sha", "page", "verbatim", "verified_by",
                       "rule_version")

# Evidence-backed verification vocabulary (NEO round-2 review). A value
# outside this set has no evidence semantics and is refused — verified_by
# is a receipt, not a free-text verdict dump.
VERIFIED_BY_VOCAB = frozenset({
    "vault_sha_recompute",   # sha256 recomputed against the vault blob
    "quote_pass",            # value entailed by the verbatim span-quote
    "applicability_rule",    # validated by a named applicability rule
})

# Raw extractor verdicts map onto the vocabulary here, in the adapter.
VERDICT_TO_VERIFIED_BY = {
    "printed_row": "quote_pass",
    "vault_sha_recompute": "vault_sha_recompute",
    "applicability_rule": "applicability_rule",
}

SCHEMA_V2 = """
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
-- Null-safe uniqueness (SQL UNIQUE treats NULLs as distinct; COALESCE
-- folds NULL to ''). `page` is part of the KEY in v2: observations on
-- different pages are distinct rows — a same-key different-page row is
-- NEW evidence, not a duplicate (NEO round-2: silent-drop defect).
CREATE UNIQUE INDEX IF NOT EXISTS uq_norm_claims_key
    ON norm_claims(opn, doc_sha, field, page,
                   COALESCE(value_role, ''),
                   COALESCE(condition_verbatim, ''));
"""

# Postgres dialect (PG on x — mfg_dosier). Applied by the loader when the
# target is a Postgres URL; never applied locally without authorization.
SCHEMA_V2_PG = """
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
    ON norm_claims(opn, doc_sha, field, page,
                   COALESCE(value_role, ''),
                   COALESCE(condition_verbatim, ''));
"""

# Columns compared when a key collides: identical content -> duplicate;
# ANY difference -> conflicting observation, refused loudly (never
# silently ignored / mislabeled as duplicate).
SUBSTANTIVE_COLS = ("value", "value_min", "value_max", "unit",
                    "unit_as_printed", "verbatim", "span", "verdict",
                    "verified_by", "rule_version", "extractor_method",
                    "evidence_ref", "source_url")


def validate_typed_row(row: dict):
    """Return list of contract violations; empty list means valid.

    Fail-closed: missing provenance never reaches storage.
    """
    errors = []
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
    vb = (row.get("verified_by") or "").strip()
    if not vb:
        errors.append("missing verified_by")
    elif vb not in VERIFIED_BY_VOCAB:
        errors.append(
            f"verified_by {vb!r} not in evidence-backed vocabulary "
            f"{sorted(VERIFIED_BY_VOCAB)}")
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


_FIELD_SUFFIXES = ("_min_v", "_max_v", "_v", "_min", "_max")


def _field_base_token(field):
    """vout_max_v -> 'vout'; the printed label for the field's row."""
    f = (field or "").strip().lower()
    changed = True
    while changed and f:
        changed = False
        for s in _FIELD_SUFFIXES:
            if f.endswith(s):
                f = f[:-len(s)]
                changed = True
    return f


def _num_printed_after_label(verbatim, v, field):
    """Field-bound entailment: value must appear as a printed numeric
    token in the row AFTER the field's own label.

    Token-level comparison kills the digit-boundary false accepts:
    '9' inside '0.9' / '9.5' / '-9' is a different token and does not
    match. If the field label is absent from the quote, nothing in that
    row can be evidence FOR the field (the original
    '…available on | 9' over-claim) — fail.
    Returns (True, '') or (False, reason).
    """
    base = _field_base_token(field)
    if not base:
        return False, f"field {field!r} has no base label token"
    label = re.search(
        r"(?<![A-Za-z0-9])" + re.escape(base) + r"(?![A-Za-z0-9])",
        verbatim, re.IGNORECASE)
    if not label:
        return False, (f"field label {base!r} not present in verbatim — "
                       f"row cannot entail field {field!r}")
    tail = verbatim[label.end():]
    target = float(v)
    for tok in re.split(r"\s*\|\s*|\s+", tail):
        tok = tok.strip("()[]{}:;,")
        if not tok:
            continue
        try:
            if float(tok) == target:
                return True, ""
        except ValueError:
            if tok == _fmt_num(v):
                return True, ""
    return False, (f"value {_fmt_num(v)} not printed after field label "
                   f"{base!r} in verbatim")


def _entailment_errors(row):
    """The verbatim span-quote must entail the claimed value(s), bound to
    the field's own label (NEO round-2: numeric token presence alone is
    not entailment). Over-claims are rejected here, never stored/served.
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
        ok, why = _num_printed_after_label(verbatim, v, row.get("field"))
        if not ok:
            errors.append(
                f"verbatim does not entail {name}={_fmt_num(v)} "
                f"(field {row.get('field')!r}): {why} — over-claim rejected")
    return errors
