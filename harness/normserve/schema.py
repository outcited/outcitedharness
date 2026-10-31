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
  - round-3 (NEO review of f41c18eb): the gate is SEMANTIC — value is
    bound on the label's own line to the printed unit (9mV != 9V), the
    printed role (typ != max), range endpoints (min != upper bound),
    never after a page reference; OPN applicability must be evidenced
    (mentioned in quote/condition/source/evidence_ref, or a named rule
    WITH receipt); a raw applicability_rule verdict without receipt is
    self-assertion and is refused.
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
    errors.extend(_applicability_errors(row))
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


_ROLE_PRINTED = {"min": "min", "max": "max", "typ": "typ", "typ.": "typ",
                 "nom": "typ", "nom.": "typ"}
_NUM_ONLY_RE = re.compile(r"^-?\d+(?:\.\d+)?$")
# 9, 9mV, 1.2V, 100mA -> (number, normalized unit like 'mV')
_NUM_UNIT_RE = re.compile(
    r"^(-?\d+(?:\.\d+)?)\s*([kKmMuµnnpM]?)([VAWHzsF])$")
_UNIT_ONLY_RE = re.compile(r"^[kKmMuµnnpM]?[VAWHzsF]\.?$")
_PAGE_CUE_RE = re.compile(
    r"\b(?:see|refer(?:ence)?|pages?|pp\.?)\b", re.IGNORECASE)
_RANGE_MARKER_RE = re.compile(
    r"\d(?:\.\d+)?\s*(?:to|–|—|~|\.\.|…)\s*-?\d", re.IGNORECASE)


def _norm_unit(u):
    return (u or "").strip().rstrip(".").replace("µ", "u").replace("μ", "u")


def _unit_of(claimed):
    """'mV' -> 'mV'; 'V' -> 'V'; None -> None (normalized prefix+base)."""
    u = _norm_unit(claimed)
    return u or None


def _label_lines(verbatim, base):
    """Lines of the quote whose text carries the field's own label."""
    pat = re.compile(r"(?<![A-Za-z0-9])" + re.escape(base) +
                     r"(?![A-Za-z0-9])", re.IGNORECASE)
    return [ln.strip() for ln in verbatim.splitlines()
            if ln.strip() and pat.search(ln)]


def _scan_label_line(line, base):
    """Cell-level scan of one label line AFTER the label cell.

    Returns dict with:
      nums: [{v, unit, page_cue}] in print order (unit bound from an
            attached suffix or the following unit-only cell);
      roles: printed role words (min/max/typ);
      is_range: a 'a to b' style range marker is printed.
    """
    cells = [c.strip() for c in line.split("|")] if "|" in line else [line]
    pat = re.compile(r"(?<![A-Za-z0-9])" + re.escape(base) +
                     r"(?![A-Za-z0-9])", re.IGNORECASE)
    start = 0
    for i, cell in enumerate(cells):
        if pat.search(cell):
            start = i + 1
            break
    nums, roles = [], set()
    page_cue = False
    for cell in cells[start:]:
        if not cell:
            continue
        cell_nums = []
        cell_unit = None
        for tok in cell.split():
            t = tok.strip("(),;:")
            if _NUM_ONLY_RE.match(t):
                cell_nums.append({"v": float(t), "unit": None})
            else:
                m = _NUM_UNIT_RE.match(t)
                if m:
                    cell_nums.append({"v": float(m.group(1)),
                                      "unit": _norm_unit(m.group(2) +
                                                         m.group(3))})
                elif t.lower() in _ROLE_PRINTED:
                    roles.add(_ROLE_PRINTED[t.lower()])
                elif _UNIT_ONLY_RE.match(t):
                    cell_unit = _norm_unit(t)
        if _PAGE_CUE_RE.search(cell) and not cell_nums:
            page_cue = True
        if cell_nums:
            for n in cell_nums:
                n["page_cue"] = page_cue
                nums.append(n)
            if cell_unit and nums[-1]["unit"] is None:
                nums[-1]["unit"] = cell_unit
        elif cell_unit and nums and nums[-1]["unit"] is None:
            nums[-1]["unit"] = cell_unit
    return {"nums": nums, "roles": roles,
            "is_range": bool(_RANGE_MARKER_RE.search(line))}


def _bind_value_on_line(scan, name, v, claimed_role, claimed_unit):
    """Bind one claimed (value, role, unit) to the printed cells of one
    label line. Returns (True, '') or (False, reason)."""
    nums = scan["nums"]
    target = float(v)
    cands = [n for n in nums if n["v"] == target]
    if not cands:
        return False, (f"value {_fmt_num(v)} not printed after field "
                       "label in row")
    n = cands[0]
    if n["page_cue"]:
        return False, ("number follows a page reference (see/refer/page) "
                       "in the row — it is a page number, not a field "
                       "value")
    if n["unit"]:
        if claimed_unit:
            if _unit_of(claimed_unit) != n["unit"]:
                return False, (f"printed unit {n['unit']!r} does not bind "
                               f"claimed unit {claimed_unit!r}")
        else:
            return False, (f"row prints unit {n['unit']!r} but the claim "
                           "records no unit — unit unbound")
    allvals = sorted({m["v"] for m in nums})
    if len(allvals) > 1 or scan["is_range"]:
        if name == "value_min" or claimed_role == "min":
            if target != allvals[0]:
                return False, (f"{_fmt_num(v)} is not the lower bound of "
                               f"the stated range {allvals[0]}–"
                               f"{allvals[-1]}")
        if name == "value_max" or claimed_role == "max":
            if target != allvals[-1]:
                return False, (f"{_fmt_num(v)} is not the upper bound of "
                               f"the stated range {allvals[0]}–"
                               f"{allvals[-1]}")
    roles = scan["roles"]
    if claimed_role in ("typ", "nom"):
        if "typ" not in roles:
            return False, ("role typ claimed but no typ/nom role is "
                           "printed in the row")
    elif claimed_role in ("min", "max"):
        if roles and claimed_role not in roles:
            if not (scan["is_range"] or len(allvals) > 1):
                return False, (f"printed role {sorted(roles)} conflicts "
                               f"with claimed role {claimed_role!r}")
        elif not roles and not (scan["is_range"] or len(allvals) > 1):
            return False, (f"role {claimed_role!r} claimed but not "
                           "printed in the row")
    else:
        if roles:
            return False, (f"row prints role {sorted(roles)} but the "
                           f"claim records value_role {claimed_role!r} — "
                           "role unbound")
    return True, ""


def _num_printed_after_label(verbatim, v, field, claimed_role=None,
                             claimed_unit=None, name="value"):
    """Field-bound semantic entailment (NEO round-3): the value must be
    printed after the field's own label ON THE SAME ROW/LINE and bound
    to its printed unit, printed role, and range position. Numeric
    token presence alone is not entailment.
    Returns (True, '') or (False, reason).
    """
    base = _field_base_token(field)
    if not base:
        return False, f"field {field!r} has no base label token"
    lines = _label_lines(verbatim, base)
    if not lines:
        return False, (f"field label {base!r} not present in verbatim — "
                       f"row cannot entail field {field!r}")
    first_fail = None
    for line in lines:
        scan = _scan_label_line(line, base)
        ok, why = _bind_value_on_line(scan, name, v, claimed_role,
                                      claimed_unit)
        if ok:
            return True, ""
        if first_fail is None:
            first_fail = why
    return False, first_fail or "row does not entail value"


def _entailment_errors(row):
    """The verbatim span-quote must entail the claimed value(s), bound
    to the field's label AND the printed unit, role, and range position
    (NEO round-3 items 1–5). Over-claims are rejected here, never
    stored/served.
    """
    errors = []
    verbatim = row.get("verbatim") or ""
    if not verbatim.strip():
        return errors  # missing-verbatim already reported
    role = (row.get("value_role") or "").strip().lower() or None
    unit = (row.get("unit") or "").strip() or None
    # The field name itself implies a bound: vin_min_v is a MIN claim
    # even when the raw row delivers it on the generic `value` key
    # (NEO round-3 item 4). The printed-role claim only wins when it is
    # itself an explicit role (min/max/typ/nom).
    f = (row.get("field") or "").strip().lower()
    implied = None
    if f.endswith(("_min_v", "_min")):
        implied = "min"
    elif f.endswith(("_max_v", "_max")):
        implied = "max"
    checked = []
    if row.get("value") is not None:
        checked.append(("value", row["value"], role))
    if row.get("value_min") is not None:
        checked.append(("value_min", row["value_min"],
                        "min" if role not in ("typ", "nom") else role))
    if row.get("value_max") is not None:
        checked.append(("value_max", row["value_max"],
                        "max" if role not in ("typ", "nom") else role))
    for name, v, claimed_role in checked:
        if claimed_role not in ("min", "max", "typ", "nom") and implied:
            claimed_role = implied
        ok, why = _num_printed_after_label(verbatim, v, row.get("field"),
                                           claimed_role=claimed_role,
                                           claimed_unit=unit, name=name)
        if not ok:
            errors.append(
                f"verbatim does not entail {name}={_fmt_num(v)} "
                f"(field {row.get('field')!r}): {why} — over-claim rejected")
    return errors


# --- OPN applicability binding (NEO round-3 items 6–7) ---

def _applicability_errors(row):
    """The OPN must be bound to source evidence: it must be mentioned in
    the quote, its condition, the source URL, or an evidence_ref — or
    validated by a named applicability rule WITH a receipt. A raw
    applicability_rule verdict without rule receipt (evidence_ref +
    span) is self-assertion and is refused. Unrelated-OPN rows are
    held, never served.
    """
    errors = []
    opn = (row.get("opn") or "").strip().upper()
    if not opn:
        return errors  # missing-opn already reported
    evidence_blob = " ".join(
        str(row.get(k) or "") for k in
        ("verbatim", "condition_verbatim", "source_url", "evidence_ref")
    ).upper()
    mentioned = opn in evidence_blob
    vb = (row.get("verified_by") or "").strip()
    if vb == "applicability_rule":
        if not (row.get("evidence_ref") or "").strip() or \
                not (row.get("span") or "").strip():
            errors.append(
                "verified_by=applicability_rule requires an actual rule "
                "receipt (nonempty evidence_ref + span) — a raw rule "
                "verdict without receipt is self-assertion, refused")
        if not mentioned:
            errors.append(
                f"applicability_rule receipt does not name OPN {opn!r} "
                "in evidence_ref/span/verbatim — applicability unbound")
    elif not mentioned:
        errors.append(
            f"OPN applicability not evidenced: {opn!r} is not mentioned "
            "in verbatim/condition_verbatim/source_url/evidence_ref — "
            "unrelated-OPN claim held")
    return errors
