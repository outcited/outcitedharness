"""Source adapter: harness.electronics-power-axes-v2.v1 rows → typed rows.

Raw extractor output varies by lane:
  - e3 batch rows carry `opn` and `pdf_sha`.
  - The ST1S15 proving sample carries the sha in `part_number` with an
    empty `pdf_sha` and no `opn`; the OPN is operator-supplied at load
    time (Sam-directed sample — never invented by this code).

No values are invented: verbatim, page, condition and verdict pass
through unchanged; unit_as_printed is preserved alongside the
canonicalized unit.

verified_by semantics (NEO source review at merge 47cac640):
`verified_by` records the EVIDENCE-BACKED VERDICT (e.g. "printed_row")
— what vouches for the claim. The extraction method is a separate
provenance field `extractor_method`; relabeling method as verification
over-claims.
"""

import re

SHA_RE = re.compile(r"^[0-9a-f]{64}$")


def adapt_power_axes_row(raw: dict, opn_default: str = None):
    """Map one raw power-axes-v2 row to a typed schema row.

    Returns (typed_row, None) or (None, reason) on unmappable input.
    """
    doc_sha = (raw.get("pdf_sha") or raw.get("doc_sha") or
               raw.get("part_number") or "").strip().lower()
    if not SHA_RE.match(doc_sha):
        return None, f"no 64-hex doc sha in row (pdf_sha/part_number): {doc_sha!r}"

    opn = (raw.get("opn") or opn_default or "").strip().upper()
    if not opn:
        return None, "no opn in row and no operator-supplied default"

    field = (raw.get("field") or "").strip()
    if not field:
        return None, "missing field"

    value = raw.get("value")
    # power-axes emits axis-suffixed min/max (e.g. vout_min_v / vout_max_v)
    vmin = raw.get(f"{field}_min_v", raw.get(f"{field}_min"))
    vmax = raw.get(f"{field}_max_v", raw.get(f"{field}_max"))
    if value is None and vmin is not None and vmin == vmax:
        value = vmin
        vmin = vmax = None
    if value is None and vmin is None and vmax is None:
        return None, f"no numeric value for field {field}"

    from . import schema as schema_mod
    verdict = (raw.get("verdict") or "").strip()
    verified_by = schema_mod.VERDICT_TO_VERIFIED_BY.get(verdict)
    if verified_by is None:
        return None, (
            f"verdict {verdict!r} does not map to an evidence-backed "
            f"verified_by vocabulary {sorted(schema_mod.VERIFIED_BY_VOCAB)} "
            "— arbitrary verdict strings are refused (hw-x9g)")
    return {
        "opn": opn,
        "doc_sha": doc_sha,
        "field": field,
        "value": value,
        "value_min": vmin,
        "value_max": vmax,
        "unit": raw.get("unit"),
        "unit_as_printed": raw.get("unit_as_printed"),
        "value_role": raw.get("value_role"),
        "condition_verbatim": raw.get("condition_verbatim"),
        "verdict": raw.get("verdict"),
        "table_kind": raw.get("table_kind"),
        "page": raw.get("page_1based"),
        "verbatim": raw.get("verbatim"),
        "span": raw.get("span"),
        "verified_by": verified_by,
        "rule_version": raw.get("schema"),
        "extractor_method": raw.get("method"),
        "evidence_ref": raw.get("evidence_ref"),
        "source_url": raw.get("source_url"),
        "schema": raw.get("schema"),
        "schema_version": schema_mod.SCHEMA_VERSION,
    }, None
