"""Fail-closed admission gate with source-coordinate resolution
(hw-x9g round 5 — repairs the f3df0c2d rejections).

f3df0c2d was executed-reviewed and REJECTED (7 confirmed defects): the
gate trusted SELF-DECLARED structure metadata (quoted_text, header
bindings, applicability cells, condition verbatims, coordinates) and
verified only page-level verbatim presence. Round 5 rule: the gate
RE-READS the source PDF at the cited page and verifies CELL content,
field/header/unit/role binding, applicability, and conditions against
CURRENT BYTES. Anything that does not verify against the bytes is HELD
with a reason code — never trusted from stored or self-declared
metadata, never guessed.

Checks, in order (all but the receipt extras run for EVERY receipt
type — vault_sha_recompute gets no metadata-only bypass):

1. STRUCTURED OBSERVATION — exactly one table_cell evidence, sha- and
   page-matched to the claim, integer coordinates, all nonnegative,
   page within the document's actual page count.
     -> HOLD no_structured_observation
2. SOURCE RESOLUTION — the PDF is opened from the vault blob:
   a. the claim's verbatim span is present in the cited page's text
      -> HOLD span_not_in_source
   b. the cited cell's quoted_text is present in the cited page's text
      (invented cell text is impossible) -> HOLD cell_not_in_source
   c. the claimed value is printed inside the cited cell text
      -> HOLD value_not_in_cell
   d. the field_binding's header_verbatim is present in the cited
      page's text, and the column_role word (min/max/typ) is printed
      in that header -> HOLD header_not_in_source / role_not_in_header
   e. the applicability structure_page is in range and the OPN is
      printed as the cell's own value on THAT page, and the printed
      context is not a NEGATION ('Not applicable to ST1S15' never
      admits — every printed occurrence inside a negated sentence is
      refused) -> HOLD applicability_not_in_source /
      applicability_negated
   f. every condition's verbatim is present in the text of its
      source_page (which must be in range)
      -> HOLD condition_not_in_source
   g. COORDINATE RESOLUTION (round 6): the self-declared
      table_index/row_index/column_index resolve against the current
      bytes — via the evidence's table_headers_on_page anchors (printed,
      ascending, anchoring the cited header), the quoted row's line
      distance from the cited header (== row_index), and the claimed
      values printing at/after column_index in the row's own cells
      -> HOLD structure_not_in_source
3. FIELD BINDING BY EXTRACTION — field derived from the extractor's
   column_key at that cell; table matches. -> HOLD field_unbound
4. ROLE BINDING — printed column_role bound to value_role/min-max
   payload. -> HOLD role_unbound
5. UNIT BOUND, PRINTED-FIRST — if the cell text prints unit tokens,
   the claim unit must be among them; a declared column_unit can only
   fill ABSENCE, it never overrides a printed conflicting unit
   (counterexample: cell prints '9mV', declared column_unit 'V' — the
   printed mV wins). -> HOLD unit_unbound
6. APPLICABILITY BY PRINTED STRUCTURE — OPN is the exact printed value
   of a cited ordering/device-summary/package cell. URL/query/path/
   prefix/negated/evidence_ref mentions are not applicability.
   -> HOLD opn_unbound
7. CONDITIONS BOUND AT THE OBSERVATION — nonempty, each with verbatim,
   precedence in {local, column, inherited} (LOCAL table-level
   conditions OVERRIDE section defaults; inherited context recorded,
   never the reverse), integer source_page. -> HOLD conditions_unbound
8. RECEIPT IDENTITY — verified_by from the fixed vocabulary only;
   vault_sha_recompute re-hashes the blob; applicability_rule must
   resolve to a ratified rule at the claimed rule_version (a version
   string alone is not applicability proof — the rule receipt still
   needs evidence_ref + span naming the OPN at the schema layer).
   -> HOLD receipt_unknown_vocabulary / receipt_unverifiable /
      rule_unresolved
"""

import re

from . import vault as vault_mod

RECEIPT_VOCABULARY = ("vault_sha_recompute", "quote_pass",
                      "applicability_rule")

APPLICABILITY_STRUCTURES = ("ordering_table", "device_summary",
                            "package_table")

COLUMN_ROLES = ("min", "typ", "max", "value")

CONDITION_PRECEDENCES = ("local", "column", "inherited")

# Printed negation cues (round 6): an OPN printed ONLY inside a negated
# sentence is not applicable — positive printed evidence is required.
_NEGATION_RE = re.compile(
    r"not\s+applicable|not\s+supported|does\s+not\s+(?:apply|support)|"
    r"\bexcept\b|\bexcluding\b", re.IGNORECASE)

_CELL_KINDS = ("table_cell",)

# Unit token scan of printed cell/header text. The lookbehind blocks
# letters (so 'Rev' or 'Volt' never yield 'V') but ALLOWS digits before
# a prefixed unit (so '9mV' resolves to mV, not V — the f3df unit-
# conflict counterexample).
_UNIT_PRINT_RE = re.compile(
    r"(?<![A-Za-z])([kKmMuµnnp]?)(?:Hz|V|A|W|F|s)(?![A-Za-z])")



def _token_in_text(token, text):
    """Exact-token match (round 6 failure 4): 'ST1S15' is not printed in
    a line naming 'ST1S150' — a substring hit is not identity."""
    return re.search(r"(?<![A-Za-z0-9])" + re.escape(token) +
                     r"(?![A-Za-z0-9])", text, re.IGNORECASE) is not None


def _norm(s):
    return re.sub(r"\s+", " ", s or "").strip()


def _norm_unit(u):
    return (u or "").strip().rstrip(".").replace("µ", "u").replace("μ", "u")


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
        v = cell.get(key)
        if not isinstance(v, int) or isinstance(v, bool):
            return f"cell evidence missing integer {key}"
        minimum = 1 if key == "page_1based" else 0
        if v < minimum:
            return (f"cell evidence {key}={v!r} out of range — "
                    "self-declared coordinates are not evidence")
    if cell["page_1based"] != typed.get("page"):
        return "cell evidence page does not match claim page"
    return None


def _page_text(doc, page_1based):
    return doc[page_1based - 1].get_text()


def _resolve_source(typed, raw, cell, vault_root):
    """Re-read the source PDF from the vault and verify EVERY cited
    content element against CURRENT bytes. Runs for all receipt types.
    Returns (None, lines_by_page) when everything resolves — the
    normalized per-page line lists are returned for the downstream
    coordinate resolution — else ((reason_code, detail), None).
    """
    if not vault_root:
        return (("receipt_unverifiable",
                 "vault_root required — the gate must re-read the source "
                 "PDF; metadata-only admission is refused"), None)
    try:
        import fitz
    except ImportError:
        return (("receipt_unverifiable",
                 "pymupdf not available; source re-resolution impossible"),
                None)
    blob = vault_mod.blob_path(vault_root, typed["doc_sha"])
    page_texts, lines_by_page = {}, {}
    try:
        with fitz.open(blob) as doc:
            page_count = doc.page_count
            for p in range(1, page_count + 1):
                text = doc[p - 1].get_text()
                page_texts[p] = _norm(text)
                lines_by_page[p] = [_norm(l)
                                    for l in text.splitlines() if l.strip()]
    except Exception as e:
        return (("receipt_unverifiable",
                 f"source PDF could not be opened: {type(e).__name__}: {e}"),
                None)

    def text_of(p):
        return page_texts.get(p, "")

    page = cell["page_1based"]
    if page > page_count:
        return (("no_structured_observation",
                 f"cited page {page} out of range (document has "
                 f"{page_count} page(s)) — invented page refused"), None)

    page_text = text_of(page)

    verbatim = _norm(typed.get("verbatim"))
    if not verbatim or verbatim not in page_text:
        return (("span_not_in_source",
                 f"verbatim span not present in current bytes of page "
                 f"{page}"), None)

    cell_text = _norm(cell.get("quoted_text"))
    if not cell_text:
        return (("cell_not_in_source", "cited cell has no quoted_text"),
                None)
    if cell_text not in page_text:
        return (("cell_not_in_source",
                 f"cited cell text {cell_text!r} not present in current "
                 f"bytes of page {page} — invented cell refused"), None)
    value_err = _value_not_in_cell(typed, cell_text)
    if value_err:
        return (value_err, None)

    binding = raw.get("field_binding") or {}
    header = _norm(binding.get("header_verbatim"))
    if not header:
        return (("header_not_in_source",
                 "field_binding missing header_verbatim — the column "
                 "header must be quoted from the source"), None)
    if header not in page_text:
        return (("header_not_in_source",
                 f"column header {header!r} not present in current bytes "
                 f"of page {page}"), None)
    if header not in lines_by_page.get(page, []):
        return (("header_not_in_source",
                 f"column header {header!r} is not a printed line of page "
                 f"{page} — a substring of a printed header line is not "
                 "cell resolution (round 6 failure 2)"), None)
    role = binding.get("column_role")
    if role in ("min", "typ", "max"):
        if not re.search(r"(?<![A-Za-z])" + role + r"(?![A-Za-z])",
                         header, re.IGNORECASE):
            return (("role_not_in_header",
                     f"column_role {role!r} is not printed in the "
                     f"cited header {header!r}"), None)

    app_err = _resolve_applicability_source(raw, text_of, lines_by_page,
                                            page_count)
    if app_err:
        return (app_err, None)

    cond_err = _resolve_conditions_source(raw, text_of, page_count)
    if cond_err:
        return (cond_err, None)
    return (None, lines_by_page)


def _value_not_in_cell(typed, cell_text):
    for key in ("value", "value_min", "value_max"):
        v = typed.get(key)
        if v is None:
            continue
        if not _value_in_text(v, cell_text):
            return ("value_not_in_cell",
                    f"{key}={v!r} not printed in the cited cell text "
                    f"{cell_text!r}")
    return None


def _value_in_text(value, text):
    for candidate in (repr(value), f"{value:g}", f"{value:.1f}",
                      str(value)):
        if re.search(r"(?<![0-9.])" + re.escape(candidate) +
                     r"(?![0-9.])", text):
            return True
    return False


def _resolve_applicability_source(raw, text_of, lines_by_page, page_count):
    """The applicability cell's OPN text must be printed on the cited
    structure page of the CURRENT bytes — as an EXACT TOKEN (round 6
    failure 4: a source that names only ST1S150 does not admit ST1S15;
    substring presence is not identity) — and never inside a NEGATED
    sentence (round 6 failure 3: a title-positive + body-negated page
    resolves NEGATED — any printed occurrence of the OPN in a negation
    sentence on the structure page refuses the applicability)."""
    app = raw.get("applicability")
    if not isinstance(app, dict):
        return None  # shape checked later (opn_unbound)
    spage = app.get("structure_page")
    if not isinstance(spage, int) or isinstance(spage, bool) or \
            spage < 1 or spage > page_count:
        return ("applicability_not_in_source",
                f"applicability structure_page {spage!r} out of range "
                f"(document has {page_count} page(s))")
    cell_text = _norm(app.get("cell_text"))
    if cell_text and cell_text not in text_of(spage):
        return ("applicability_not_in_source",
                f"applicability cell {cell_text!r} not present in "
                f"current bytes of structure page {spage}")
    if cell_text:
        lines = lines_by_page.get(spage, [])
        occurrences = [ln for ln in lines
                       if _token_in_text(cell_text, ln)]
        if not occurrences:
            return ("applicability_not_in_source",
                    f"applicability cell {cell_text!r} is not printed as "
                    f"an exact token on structure page {spage} — a "
                    "substring of another OPN's printed identity is not "
                    "applicability")
        negated = [ln for ln in occurrences if _NEGATION_RE.search(ln)]
        if negated:
            return ("applicability_negated",
                    f"{cell_text!r} is printed inside a negated sentence "
                    f"on structure page {spage} (e.g. 'Not applicable "
                    "to ...') — negation is not positive printed "
                    "evidence; the NEGATED outcome resolves")
    return None


def _resolve_conditions_source(raw, text_of, page_count):
    """Every condition's verbatim must be present in the current bytes
    of its source_page."""
    conds = raw.get("conditions")
    if not isinstance(conds, list):
        return None  # shape checked later (conditions_unbound)
    for c in conds:
        if not isinstance(c, dict):
            continue
        spage = c.get("source_page")
        if not isinstance(spage, int) or isinstance(spage, bool) or \
                spage < 1 or spage > page_count:
            return ("condition_not_in_source",
                    f"condition source_page {spage!r} out of range "
                    f"(document has {page_count} page(s))")
        verbatim = _norm(c.get("verbatim"))
        if verbatim and verbatim not in text_of(spage):
            return ("condition_not_in_source",
                    f"condition verbatim {verbatim!r} not present in "
                    f"current bytes of source page {spage} — invented "
                    "condition refused")
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


def _printed_units(text):
    return {_norm_unit(m.group(0)) for m in _UNIT_PRINT_RE.finditer(text or "")}


def _check_unit_bound(typed, raw, cell):
    unit = _norm_unit(typed.get("unit"))
    if not unit:
        return "claim unit absent"
    cell_text = cell.get("quoted_text") or ""
    printed = _printed_units(cell_text)
    source = "cell text"
    if not printed:
        # PRINTED-FIRST (round 6 failure 2): if the cell prints no unit
        # of its own, the printed COLUMN HEADER is the unit evidence —
        # a declared column_unit never overrides a unit printed in the
        # header (counterexample: cell prints '9', header prints
        # 'Unit mV', column_unit declared 'V' -> the printed mV wins;
        # admitting 9 as 9V is over-claim).
        printed = _printed_units(
            (raw.get("field_binding") or {}).get("header_verbatim"))
        source = "cited header"
    if not printed:
        # The claim's own verbatim row line is source-verified (check
        # 2a); a unit PRINTED in that row's unit column is source
        # evidence too (e.g. '... | 2.3 | 5.5 | V' citing the bare
        # '2.3' cell).
        printed = _printed_units(typed.get("verbatim"))
        source = "cited row"
    if not printed:
        # Round 6 failure 5: with NO unit printed anywhere in the cited
        # cell, header, or row line there is no source-resolved unit
        # evidence — the declared column_unit is extractor metadata,
        # not the source; the metadata-unit fallback path is retired
        # (a row citing a bare printed value and a metadata unit HELDs).
        column_unit = _norm_unit(
            (raw.get("field_binding") or {}).get("column_unit"))
        return (f"no unit printed in the cited cell, header, or row — "
                f"declared column_unit {column_unit!r} is metadata, "
                "not printed evidence")
    if unit not in printed:
        return (f"{source} prints units {sorted(printed)} "
                f"conflicting with claim unit {unit!r} — printed "
                "unit wins over declared column_unit")
    return None


def _resolve_coordinates(typed, raw, cell, lines_by_page):
    """Source-resolve the STRUCTURE coordinates (round 6 failure 1):
    table_index / row_index / column_index are self-declared integers
    that previously admitted 999s. Resolution contract, all against the
    current bytes of the cited page:

    - table: the cell evidence carries `table_headers_on_page` — the
      ordered list of table-header verbatims on the page. Every anchor
      must be printed, anchors must be in ascending line order, the
      cited table_index must fall inside the anchor list, and
      anchors[table_index] must BE the cited header.
    - row: the quoted cell content must be the row_index-th line after
      the cited header line in reading order (rows print one line each
      under this extractor lane's model; rows that do not resolve are
      HELD, never guessed).
    - column: the claimed values must be printed at/after the cited
      column_index in the cited row's own verbatim cell list.
    """
    page_lines = lines_by_page.get(cell["page_1based"], [])
    binding = raw.get("field_binding") or {}
    header = _norm(binding.get("header_verbatim"))
    quoted = _norm(cell.get("quoted_text"))

    anchors = cell.get("table_headers_on_page")
    if not isinstance(anchors, list) or not anchors or \
            not all(isinstance(a, str) and a.strip() for a in anchors):
        return ("structure_not_in_source",
                "cell evidence missing table_headers_on_page anchors — "
                "self-declared table_index is not evidence")
    table_index = cell["table_index"]
    if table_index >= len(anchors):
        return ("structure_not_in_source",
                f"table_index {table_index} outside the "
                f"{len(anchors)} printed table anchor(s) on the cited "
                "page — invented table refused")
    anchor_lines = []
    for a in anchors:
        na = _norm(a)
        idx = [i for i, ln in enumerate(page_lines) if na in ln]
        if not idx:
            return ("structure_not_in_source",
                    f"table anchor {na!r} not present in current bytes "
                    f"of page {cell['page_1based']} — invented table "
                    "refused")
        anchor_lines.append(idx[0])
    if any(b <= a for a, b in zip(anchor_lines, anchor_lines[1:])):
        return ("structure_not_in_source",
                "table_headers_on_page anchors are not in ascending "
                "line order on the cited page")
    if _norm(anchors[table_index]) != header:
        return ("structure_not_in_source",
                f"cited table_index {table_index} anchors to "
                f"{_norm(anchors[table_index])!r}, not the cited header "
                f"{header!r}")

    header_lines = [i for i, ln in enumerate(page_lines)
                    if header and header in ln]
    quoted_lines = [i for i, ln in enumerate(page_lines)
                    if quoted and quoted in ln]
    row = cell["row_index"]
    if not any(q - h == row for h in header_lines
               for q in quoted_lines if q > h):
        return ("structure_not_in_source",
                f"row_index {row} does not resolve: the quoted row is "
                f"not {row} line(s) after the cited header in the "
                "current bytes")

    cells = [c.strip() for c in (typed.get("verbatim") or "").split("|")]
    col = cell["column_index"]
    if col >= len(cells):
        return ("structure_not_in_source",
                f"column_index {col} outside the {len(cells)} printed "
                "cell(s) of the cited row — invented column refused")
    # Round 6 failure 1: the check must resolve the CITED cell, not the
    # row tail — a numeric value living in column 2 is not admitted by
    # citing column 0 or the description column 1. The cited cell
    # resolves when the claimed value prints IN the cell at
    # column_index, or when the quoted cell span provably starts at
    # column_index (the quote is the tail of the row's cells from the
    # cited column, or the whole row).
    cited_cell = cells[col]
    tail = " | ".join(cells[col:])
    whole = " | ".join(cells)
    for key in ("value", "value_min", "value_max"):
        v = typed.get(key)
        if v is None:
            continue
        value_at_col = _value_in_text(v, cited_cell)
        span_anchored = (_norm(tail) == _norm(cell.get("quoted_text") or "")
                         or _norm(whole) ==
                         _norm(cell.get("quoted_text") or ""))
        if not value_at_col and not span_anchored:
            return ("structure_not_in_source",
                    f"{key}={v!r} does not resolve in the cell cited at "
                    f"column_index {col} ({cited_cell!r}) and the quoted "
                    "cell span does not start at the cited column — the "
                    "cited cell must be resolved, not the row tail")
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
    cell_text = (app.get("cell_text") or "").strip().upper()
    # The OPN must BE the printed cell's own value (row label in an
    # ordering/device-summary/package structure). Prose, prefixes,
    # negations, URL/query/path mentions all fail: not the cell value.
    if cell_text != opn:
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
        if not isinstance(c.get("source_page"), int) or \
                isinstance(c.get("source_page"), bool) or \
                c["source_page"] < 1:
            return "condition missing integer source_page"
    return None


def _check_receipt(typed, raw, vault_root, ratified_rules):
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
    if verified_by == "applicability_rule":
        rule_id = (raw.get("applicability") or {}).get("rule_id")
        if not rule_id:
            return ("rule_unresolved",
                    "applicability_rule receipt without rule_id")
        resolved = (ratified_rules or {}).get(str(rule_id))
        if not resolved or resolved.get("rule_version") != \
                typed.get("rule_version"):
            return ("rule_unresolved",
                    f"rule_id {rule_id!r} not ratified at rule_version "
                    f"{typed.get('rule_version')!r} — a version string "
                    "alone is not applicability proof")
    return None


def evaluate(typed, raw, vault_root=None, ratified_rules=None):
    """Admission predicate over a typed row + its raw extractor row.

    Returns AdmissionDecision. Fail-closed: every nonconforming input is
    HELD with a reason code. Source re-resolution (check 2) runs for
    EVERY receipt type — no metadata-only path exists.
    """
    cell = _cell_evidence(raw)
    if cell is None:
        return AdmissionDecision(False, "no_structured_observation",
                                 "row does not cite exactly one table-cell "
                                 "evidence with coordinates")
    err = _check_structured_observation(typed, cell)
    if err:
        return AdmissionDecision(False, "no_structured_observation", err)
    err, lines_by_page = _resolve_source(typed, raw, cell, vault_root)
    if err:
        return AdmissionDecision(False, err[0], err[1])
    err = _check_field_binding(typed, raw, cell)
    if err:
        return AdmissionDecision(False, "field_unbound", err)
    err = _check_role_binding(typed, raw)
    if err:
        return AdmissionDecision(False, "role_unbound", err)
    err = _check_unit_bound(typed, raw, cell)
    if err:
        return AdmissionDecision(False, "unit_unbound", err)
    err = _resolve_coordinates(typed, raw, cell, lines_by_page)
    if err:
        return AdmissionDecision(False, err[0], err[1])
    err = _check_applicability(typed, raw)
    if err:
        return AdmissionDecision(False, "opn_unbound", err)
    err = _check_conditions(raw)
    if err:
        return AdmissionDecision(False, "conditions_unbound", err)
    err = _check_receipt(typed, raw, vault_root, ratified_rules)
    if err:
        return AdmissionDecision(False, err[0], err[1])
    return AdmissionDecision(True)
