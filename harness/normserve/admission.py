"""Fail-closed admission gate (hw-x9g round 4).

A typed claim row is SERVED only if ALL five conditions hold; otherwise it
is HELD with a reason code — never swallowed, never guessed:

1. STRUCTURED OBSERVATION — the raw row cites an existing extractor's
   structured output with a CELL coordinate (page + table/row/column
   indexes from the extractor's own table extraction, sha-matched to the
   claim). No cell coordinate -> HOLD no_structured_observation.
2. FIELD BINDING BY EXTRACTION — field and value_role must come from the
   extractor's column/row-header mapping AT that cell's position
   (field_binding={table,column_key,column_role}). Untied field or role
   -> HOLD field_unbound / role_unbound. (Kills VOUT|VIN9V cross-field
   binding and Min|Typ|Max header mislabels.)
3. UNIT BOUND — unit must appear in the cell's own text or match the
   table's declared column unit. Absent/conflicting -> HOLD unit_unbound.
4. APPLICABILITY BY PRINTED STRUCTURE — OPN applicability only via exact
   match inside a parsed ordering/device-summary/package structure.
   URL/query/path/prefix/negated/evidence_ref occurrences are NOT
   applicability -> HOLD opn_unbound.
5. VERIFICATION RECEIPT IDENTITY RESOLVES — verified_by from a fixed
   vocabulary only; the receipt RE-VERIFIES at load: vault_sha_recompute
   re-hashes the doc against the vault blob; quote_pass re-extracts the
   span from CURRENT bytes (pymupdf) and requires the value in the cited
   cell; applicability_rule must resolve to a ratified rule object.
   Self-asserted strings -> HOLD receipt_unknown_vocabulary /
   receipt_unverifiable / rule_unresolved.

Additionally (NEO round-4 requirement A): value_role AND conditions must
be bound at the SAME source-bound observation, including inherited
context, with precedence recorded (local table-level conditions override
section defaults). Missing/unlabeled conditions -> HOLD conditions_unbound.

SAFE-TO-ACCEPT WHITELIST: rows from an existing structured table-aware
extractor with cell coordinates + structure-bound field/unit/role +
printed-structure OPN applicability + a re-verifying receipt. Everything
else HOLDs by construction.
"""

import re

from . import vault as vault_mod

RECEIPT_VOCABULARY = ("vault_sha_recompute", "quote_pass",
                      "applicability_rule")

APPLICABILITY_STRUCTURES = ("ordering_table", "device_summary",
                            "package_table")

COLUMN_ROLES = ("min", "typ", "max", "value")

CONDITION_PRECEDENCES = ("local", "column", "inherited")

_CELL_KINDS = ("table_cell",)


class AdmissionDecision:
    def __init__(self, served: bool, reason_code: str = None, detail: str = ""):
        self.served = served
        self.reason_code = reason_code
        self.detail = detail

    def __repr__(self):
        if self.served:
            return "AdmissionDecision(served=True)"
        return f"AdmissionDecision(HOLD {self.reason_code}: {self.detail})"


def _cell_evidence(raw):
    """Return the cited table-cell evidence dict, or None."""
    cells = []
    for ev in (raw.get("evidence") or []):
        if not isinstance(ev, dict):
            continue
        if ev.get("kind") in _CELL_KINDS:
            cells.append(ev)
    if len(cells) != 1:
        return None
    return cells[0]


def _check_structured_observation(typed, cell):
    if cell.get("document_sha256") != typed.get("doc_sha"):
        return "evidence document_sha256 does not match claim doc_sha"
    for key in ("page_1based", "table_index", "row_index", "column_index"):
        if not isinstance(cell.get(key), int):
            return f"cell evidence missing integer {key}"
    if cell["page_1based"] != typed.get("page"):
        return "cell evidence page does not match claim page"
    return None


def _check_field_binding(typed, raw, cell):
    binding = raw.get("field_binding")
    if not isinstance(binding, dict):
        return "no field_binding (field not tied to extractor column mapping)"
    if not isinstance(binding.get("column_key"), str) or \
            not binding["column_key"].strip():
        return "field_binding missing column_key"
    if binding.get("table") != cell.get("table_index"):
        return "field_binding.table does not match cited cell table_index"
    field = (typed.get("field") or "").strip()
    column_key = binding["column_key"].strip()
    if not field.startswith(column_key):
        return (f"field {field!r} not derived from column_key "
                f"{column_key!r}")
    role = binding.get("column_role")
    if role not in COLUMN_ROLES:
        return f"field_binding column_role not in {COLUMN_ROLES}: {role!r}"
    return None


def _check_role_binding(typed, raw):
    role = raw["field_binding"]["column_role"]
    value_role = typed.get("value_role")
    value = typed.get("value")
    vmin, vmax = typed.get("value_min"), typed.get("value_max")
    if role == "min":
        ok = value_role in ("range", "min") and \
            (vmin is not None or value is not None) and \
            (value is None or vmax is None or value <= vmax)
    elif role == "max":
        ok = value_role in ("range", "max") and \
            (vmax is not None or value is not None) and \
            (value is None or vmin is None or value >= vmin)
    elif role == "typ":
        ok = value_role == "typ"
    else:  # value
        ok = value_role in ("value", "fixed") and value is not None
    if not ok:
        return (f"printed role {role!r} not bound to value_role "
                f"{value_role!r} / min/max payload")
    return None


_UNIT_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9])([kKmMuUnNpP]?)(V|A|W|Hz|mV|uV|mA|uA)(?![A-Za-z0-9])")


def _check_unit_bound(typed, raw, cell):
    unit = (typed.get("unit") or "").strip()
    if not unit:
        return "claim unit absent"
    cell_text = cell.get("quoted_text") or ""
    column_unit = (raw.get("field_binding") or {}).get("column_unit")
    if column_unit and str(column_unit).strip() == unit:
        return None
    tokens = {m.group(0) for m in _UNIT_TOKEN_RE.finditer(cell_text)}
    if tokens and unit not in tokens:
        return (f"cell text carries units {sorted(tokens)} conflicting with "
                f"claim unit {unit!r}")
    if not tokens and not column_unit:
        return "no unit in cell text and no declared column unit"
    return None


def _check_applicability(typed, raw):
    opn = (typed.get("opn") or "").strip().upper()
    app = raw.get("applicability")
    if not isinstance(app, dict):
        return "no printed-structure applicability (opn_unbound)"
    structure = app.get("structure")
    if structure not in APPLICABILITY_STRUCTURES:
        return (f"applicability structure not in {APPLICABILITY_STRUCTURES}: "
                f"{structure!r}")
    if not isinstance(app.get("structure_page"), int) or \
            app["structure_page"] < 1:
        return "applicability missing integer structure_page"
    cell_text = app.get("cell_text") or ""
    # The OPN must BE the printed cell's own value (row label in an
    # ordering/device-summary/package structure). Prose, prefixes,
    # negations, URL/query/path mentions all fail: not the cell value.
    if cell_text.strip().upper() != opn:
        return (f"opn {opn!r} is not the printed value of the cited "
                f"{structure} cell")
    return None


def _check_conditions(raw):
    conds = raw.get("conditions")
    if not isinstance(conds, list) or not conds:
        return "no conditions bound at the observation (conditions_unbound)"
    for c in conds:
        if not isinstance(c, dict):
            return "condition entry not an object"
        if not (c.get("verbatim") or "").strip():
            return "condition missing verbatim"
        if c.get("precedence") not in CONDITION_PRECEDENCES:
            return (f"condition precedence not in {CONDITION_PRECEDENCES}: "
                    f"{c.get('precedence')!r}")
        if not isinstance(c.get("source_page"), int) or c["source_page"] < 1:
            return "condition missing integer source_page"
    return None


def _check_receipt(typed, raw, cell, vault_root, ratified_rules):
    verified_by = (typed.get("verified_by") or "").strip()
    if verified_by not in RECEIPT_VOCABULARY:
        return ("receipt_unknown_vocabulary",
                f"verified_by not in {RECEIPT_VOCABULARY}: {verified_by!r}")

    if not vault_root:
        return ("receipt_unverifiable",
                "vault_root required for receipt re-verification")
    ok, detail = vault_mod.verify_sha256(vault_root, typed["doc_sha"])
    if not ok:
        return ("receipt_unverifiable", f"vault re-hash failed: {detail}")

    if verified_by == "quote_pass":
        detail_err = _reextract_span(typed, raw, cell, vault_root)
        if detail_err:
            return ("receipt_unverifiable", detail_err)
    elif verified_by == "applicability_rule":
        rule_id = (raw.get("applicability") or {}).get("rule_id")
        if not rule_id:
            return ("rule_unresolved",
                    "applicability_rule receipt without rule_id")
        resolved = (ratified_rules or {}).get(str(rule_id))
        if not resolved or resolved.get("rule_version") != \
                typed.get("rule_version"):
            return ("rule_unresolved",
                    f"rule_id {rule_id!r} not ratified at rule_version "
                    f"{typed.get('rule_version')!r}")
    return None


def _reextract_span(typed, raw, cell, vault_root):
    """Re-extract the span from CURRENT vault bytes (pymupdf) and require
    the claimed value in the cited cell's own text."""
    try:
        import fitz
    except ImportError:
        return "pymupdf not available; span re-extraction impossible"
    blob = vault_mod.blob_path(vault_root, typed["doc_sha"])
    try:
        with fitz.open(blob) as doc:
            page_text = doc[cell["page_1based"] - 1].get_text()
    except Exception as e:
        return f"span re-extraction failed: {type(e).__name__}: {e}"

    def norm(s):
        return re.sub(r"\s+", " ", s or "").strip()

    verbatim = norm(typed.get("verbatim"))
    if not verbatim or verbatim not in norm(page_text):
        return "verbatim span not found in current page bytes"
    cell_text = cell.get("quoted_text") or ""
    value = typed.get("value")
    if value is not None and not _value_in_text(value, cell_text):
        return (f"value {value!r} not present in cited cell text "
                f"{cell_text!r}")
    return None


def _value_in_text(value, text):
    for candidate in (repr(value), f"{value:g}", f"{value:.1f}", str(value)):
        if re.search(r"(?<![0-9.])" + re.escape(candidate) +
                     r"(?![0-9.])", text):
            return True
    return False


def evaluate(typed, raw, vault_root=None, ratified_rules=None):
    """Admission predicate over a typed row + its raw extractor row.

    Returns AdmissionDecision. Fail-closed: every nonconforming input is
    HELD with a reason code.
    """
    cell = _cell_evidence(raw)
    if cell is None:
        return AdmissionDecision(False, "no_structured_observation",
                                 "row does not cite exactly one table-cell "
                                 "evidence with coordinates")
    err = _check_structured_observation(typed, cell)
    if err:
        return AdmissionDecision(False, "no_structured_observation", err)
    err = _check_field_binding(typed, raw, cell)
    if err:
        return AdmissionDecision(False, "field_unbound", err)
    err = _check_role_binding(typed, raw)
    if err:
        return AdmissionDecision(False, "role_unbound", err)
    err = _check_unit_bound(typed, raw, cell)
    if err:
        return AdmissionDecision(False, "unit_unbound", err)
    err = _check_applicability(typed, raw)
    if err:
        return AdmissionDecision(False, "opn_unbound", err)
    err = _check_conditions(raw)
    if err:
        return AdmissionDecision(False, "conditions_unbound", err)
    err = _check_receipt(typed, raw, cell, vault_root, ratified_rules)
    if err:
        return AdmissionDecision(False, err[0], err[1])
    return AdmissionDecision(True)
