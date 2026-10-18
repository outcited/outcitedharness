#!/usr/bin/env python3
"""vision_to_claims — adapter connecting vision-v2 evidence to the fold.

Reads existing vision_worker_v2 output (page_regions) and produces
burn-compatible claim_data rows. Read-only on inputs. Writes to an
explicitly isolated output root (never production defaults).

Supported table classes (unambiguous mapping):
  1. Parameter tables: symbol + min/typ/max + unit
  2. Pin tables: pin number + name + description
  3. Package dimensions: DIM + INCHES + MILLIMETERS

Unsupported tables are held as raw evidence with explicit reason.

Usage:
  python3 scripts/vision_to_claims.py \
    --input /Volumes/M5_4TB/extract-results/vision-v2 \
    --output /tmp/v2c-test/out \
    [--limit 20] [--fixtures] [--verbose]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# Coordinate transform: image-local pixels -> PDF page coordinates
# vision-v2 region_bbox is PDF points (from pymupdf)
# row.bbox is image-local pixels at 200 DPI
# PDF_coord = region_origin + pixel * (72 / DPI)
DPI = 200
PDF_PER_PIXEL = 72.0 / DPI  # 0.36


def transform_bbox_to_pdf(region_bbox: list, pixel_bbox: list) -> tuple:
    """Transform image-local pixel bbox to PDF-page coordinates.

    region_bbox: [x0, y0, x1, y1] in PDF points (from pymupdf)
    pixel_bbox:  [x0, y0, x1, y1] in image-local pixels (from model)
    Returns: (pdf_x0, pdf_y0, pdf_x1, pdf_y1) in PDF points
    """
    if not region_bbox or not pixel_bbox:
        return None
    rx0, ry0 = region_bbox[0], region_bbox[1]
    px0, py0, px1, py1 = pixel_bbox[0], pixel_bbox[1], pixel_bbox[2], pixel_bbox[3]
    return (
        rx0 + px0 * PDF_PER_PIXEL,
        ry0 + py0 * PDF_PER_PIXEL,
        rx0 + px1 * PDF_PER_PIXEL,
        ry0 + py1 * PDF_PER_PIXEL,
    )


def crop_verify_cell(pdf_path: str, page_1based: int, pdf_bbox: tuple,
                     expected_value: str) -> dict:
    """Real crop verification: OCR the PDF region and match against claim.

    Returns {verdict: SUPPORTED|UNSUPPORTED|REVIEW, ocr_text: str, ...}
    - SUPPORTED: expected value found in OCR
    - UNSUPPORTED: OCR found a DIFFERENT value (not just misread)
    - REVIEW: OCR mismatch or ambiguous (misread, missing, etc.)
    """
    if not pdf_path or not pdf_bbox:
        return {"verdict": "REVIEW", "reason": "no_source_or_bbox", "ocr_text": ""}

    try:
        import pymupdf
        import tempfile
        import subprocess

        doc = pymupdf.open(pdf_path)
        if page_1based > len(doc):
            doc.close()
            return {"verdict": "REVIEW", "reason": "page_out_of_range", "ocr_text": ""}

        page = doc[page_1based - 1]
        clip = pymupdf.Rect(*pdf_bbox)
        pix = page.get_pixmap(dpi=300, clip=clip)

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            pix.save(f.name)
            result = subprocess.run(
                ["tesseract", f.name, "stdout", "--psm", "6"],
                capture_output=True, text=True, timeout=60
            )
            ocr_text = result.stdout.strip()
        doc.close()

        if not ocr_text:
            return {"verdict": "REVIEW", "reason": "empty_ocr", "ocr_text": ""}

        # Match expected value against OCR
        exp = str(expected_value).strip()
        if not exp:
            return {"verdict": "REVIEW", "reason": "empty_expected", "ocr_text": ocr_text}

        # Exact match
        if exp in ocr_text:
            return {"verdict": "SUPPORTED", "reason": "exact_match",
                    "ocr_text": ocr_text, "matched": exp}

        # Case-insensitive
        if exp.upper() in ocr_text.upper():
            return {"verdict": "SUPPORTED", "reason": "case_insensitive_match",
                    "ocr_text": ocr_text, "matched": exp}

        # Numeric core match: extract numeric part and check in OCR
        # Handles cases like "45° (typ.)" where OCR garbles the suffix
        numeric_core = _extract_numeric_core(exp)
        if numeric_core and numeric_core in ocr_text:
            return {"verdict": "SUPPORTED", "reason": "numeric_core_match",
                    "ocr_text": ocr_text, "matched": numeric_core}

        # Normalized match (strip whitespace, handle common OCR substitutions)
        exp_norm = exp.strip().upper()
        ocr_norm = ocr_text.upper()
        if _fuzzy_match(exp_norm, ocr_norm):
            return {"verdict": "REVIEW", "reason": "fuzzy_match_possible_ocr_misread",
                    "ocr_text": ocr_text, "expected": exp}

        # No match — default to REVIEW (conservative)
        # UNSUPPORTED only when OCR clearly shows a different valid value
        ocr_words = [w for w in ocr_text.split() if any(c.isalnum() for c in w)]
        if len(ocr_words) == 0:
            return {"verdict": "REVIEW", "reason": "no_alphanumeric_in_ocr",
                    "ocr_text": ocr_text}

        # Check if ANY expected-like value appears in OCR
        for word in ocr_words:
            if _fuzzy_match(exp.upper(), word.upper()):
                return {"verdict": "REVIEW", "reason": "possible_match_different_position",
                        "ocr_text": ocr_text, "expected": exp}

        # Look for a clean numeric value that's different from expected
        exp_numeric = _extract_numeric_core(exp)
        for word in ocr_words:
            word_numeric = _extract_numeric_core(word)
            if word_numeric and exp_numeric and word_numeric != exp_numeric:
                # Found a different numeric value — could be UNSUPPORTED
                # But only if the OCR word looks clean (not garbled)
                if len(word) >= 2 and word.replace('.','').replace('-','').isdigit():
                    return {"verdict": "UNSUPPORTED", "reason": "different_numeric_value",
                            "ocr_text": ocr_text, "expected": exp,
                            "found": word}

        # Default: REVIEW (OCR may be garbled or value in different column)
        return {"verdict": "REVIEW", "reason": "no_confident_match",
                "ocr_text": ocr_text, "expected": exp}

    except Exception as e:
        return {"verdict": "REVIEW", "reason": f"verify_error:{str(e)[:60]}",
                "ocr_text": ""}


def _fuzzy_match(expected: str, ocr: str) -> bool:
    """Check if expected could be an OCR misread of text in ocr."""
    exp_len = len(expected)
    if exp_len < 2:
        return False
    for word in ocr.split():
        if abs(len(word) - exp_len) <= 1:
            common = sum(1 for a, b in zip(expected, word) if a == b)
            if common >= exp_len - 1:
                return True
    return False


def _extract_numeric_core(text: str) -> str | None:
    """Extract the numeric core from a value string.

    '45° (typ.)' → '45'
    '2.65' → '2.65'
    '60V' → '60'
    'GND' → None (not numeric)
    """
    m = re.match(r'^([<>±~]?\s*-?\d+\.?\d*)', text.strip())
    return m.group(1).strip() if m else None

# ── value/unit parsing ──────────────────────────────────────────────────
_UNIT_RE = re.compile(
    r"^([<>±~]?\s*-?\d+\.?\d*(?:[eE][+-]?\d+)?)\s*(.*)$"
)
_RANGE_RE = re.compile(
    r"^(-?\d+\.?\d*)\s*(?:to|–|—|-|~)\s*(-?\d+\.?\d*)\s*(.*)$"
)

# Min/Typ/Max detection from column headers
_ROLE_PATTERNS = {
    "min": re.compile(r"\bmin(?:imum)?\b", re.I),
    "typ": re.compile(r"\btyp(?:ical)?\b", re.I),
    "max": re.compile(r"\bmax(?:imum)?\b", re.I),
}

# Absolute max vs recommended operating
_ABS_MAX_RE = re.compile(
    r"absolute\s*max|stress\s*rating|beyond.*damage|not.*recommended",
    re.I,
)
_OP_RE = re.compile(r"recommended\s*operating|operating\s*conditions", re.I)

# Pin table detection
_PIN_COL_RE = re.compile(r"pin\s*(?:name|number|no|#|symbol)", re.I)
_PIN_DESC_RE = re.compile(r"description|function|alternate", re.I)

# Package dimension detection
_DIM_COL_RE = re.compile(r"\bdim\b|dimension|symbol", re.I)
_INCH_COL_RE = re.compile(r"inch", re.I)
_MM_COL_RE = re.compile(r"millimeter|mm\b", re.I)

# Parameter table: column headers that name a parameter
_PARAM_COL_RE = re.compile(
    r"parameter|symbol|characteristic|property|rating", re.I
)


def parse_value_unit(text: str) -> tuple[str | None, str | None, str | None]:
    """Parse a cell string into (numeric_value, unit, qualifier).

    Returns (None, None, raw_text) when no numeric value is extractable.
    Never invents — raw_text preserves the original for the quote.
    """
    if not text or not text.strip():
        return None, None, text
    t = text.strip()

    # Range: "1.35 to 1.65" or "-55 to 150"
    m = _RANGE_RE.match(t)
    if m:
        lo, hi, unit = m.group(1), m.group(2), m.group(3).strip()
        return f"{lo} to {hi}", unit or None, t

    # Single value: "60V", "3.2 mA", "< 5"
    m = _UNIT_RE.match(t)
    if m:
        val, unit = m.group(1).strip(), m.group(2).strip()
        return val, unit or None, t

    return None, None, t


def detect_role(column_header: str) -> str | None:
    """Detect Min/Typ/Max role from column header."""
    if not column_header:
        return None
    for role, pat in _ROLE_PATTERNS.items():
        if pat.search(column_header):
            return role
    return None


def detect_abs_max(table_title: str) -> bool:
    """Detect absolute maximum ratings from table title."""
    return bool(_ABS_MAX_RE.search(table_title or ""))


def classify_table(columns: list[str], table_title: str) -> str:
    """Classify table type for routing. Returns 'param'|'pin'|'dim'|'unknown'."""
    cols_joined = " ".join(c or "" for c in columns)
    title = table_title or ""

    # Pin table: has pin number + pin name/description columns
    if _PIN_COL_RE.search(cols_joined):
        return "pin"

    # Package dimensions: has DIM + INCHES + MILLIMETERS
    if (_DIM_COL_RE.search(cols_joined) and
            _INCH_COL_RE.search(cols_joined) and
            _MM_COL_RE.search(cols_joined)):
        return "dim"

    # Parameter table: has parameter/symbol column and value-like columns
    if _PARAM_COL_RE.search(cols_joined) or _PARAM_COL_RE.search(title):
        return "param"

    return "unknown"


def normalize_rows_for_verify(table: dict) -> dict:
    """Normalize vision-v2 rows to verify_tables expected shape.

    Worker emits:  row = {"cells": {"col": "val"}, "bbox": [...]}
    Verify expects: row = {"col": {"value": "val"}, "bbox": [...]}
    """
    norm_table = {"rows": []}
    for row in table.get("rows", []):
        if not isinstance(row, dict):
            continue
        norm_row = {}
        cells = row.get("cells", {})
        if isinstance(cells, dict):
            for col, val in cells.items():
                norm_row[col] = {"value": val}
        if "bbox" in row:
            norm_row["bbox"] = row["bbox"]
        norm_table["rows"].append(norm_row)
    return norm_table


def table_to_claims(
    table: dict,
    region: dict,
    doc_sha256: str,
    filename: str,
    extractor: str,
    extractor_version: str,
    region_idx: int,
    table_idx: int,
    verification: list | None = None,
    pdf_path: str | None = None,
) -> tuple[list[dict], list[dict]]:
    """Convert one table to claim_data rows.

    Returns (claims, held) — claims are eligible for downstream admission,
    held are raw evidence with explicit unsupported/unmapped reason.
    """
    claims = []
    held = []

    table_title = table.get("table_title", "") or ""
    columns = table.get("columns", []) or []
    rows = table.get("rows", []) or []

    if not columns or not rows:
        held.append({
            "reason": "empty_columns_or_rows",
            "table_idx": table_idx,
            "columns": columns,
            "row_count": len(rows),
        })
        return claims, held

    tclass = classify_table(columns, table_title)
    abs_max = detect_abs_max(table_title)
    is_op = bool(_OP_RE.search(table_title or ""))

    # Route unknown table classes to held evidence (not claims)
    if tclass == "unknown":
        for r_idx, row in enumerate(rows):
            cells = row.get("cells", {}) if isinstance(row, dict) else {}
            for col_name, raw_val in cells.items():
                if raw_val is not None and str(raw_val).strip():
                    held.append({
                        "reason": "unsupported_table_class",
                        "table_idx": table_idx,
                        "row_idx": r_idx,
                        "col": col_name,
                        "value": str(raw_val)[:200],
                        "columns": columns[:10],
                        "table_title": table_title[:100],
                    })
        return claims, held

    for r_idx, row in enumerate(rows):
        if not isinstance(row, dict):
            held.append({
                "reason": "malformed_row",
                "table_idx": table_idx,
                "row_idx": r_idx,
            })
            continue

        cells = row.get("cells", {})
        if not isinstance(cells, dict) or not cells:
            held.append({
                "reason": "empty_cells",
                "table_idx": table_idx,
                "row_idx": r_idx,
            })
            continue

        cell_bbox = row.get("bbox")
        row_text = " | ".join(str(v) for v in cells.values() if v)

        for col_name, raw_val in cells.items():
            if raw_val is None or (isinstance(raw_val, str) and not raw_val.strip()):
                continue

            raw_str = str(raw_val).strip()
            numeric_val, unit, quote_text = parse_value_unit(raw_str)
            role = detect_role(col_name)
            col_header = col_name if col_name else ""

            # Determine the field name
            if tclass == "pin":
                # For pin tables: field is "pin" or the column name
                field = col_header if col_header else "pin"
            elif tclass == "dim":
                field = col_header if col_header else "dimension"
            else:
                # Parameter table: use row label as field name if available
                # Otherwise use the column name
                field = col_header if col_header else "unknown"

            # Real crop verification
            pdf_bbox = transform_bbox_to_pdf(
                region.get("region_bbox"), cell_bbox
            )
            crop_result = crop_verify_cell(
                pdf_path, int(str(region.get("page", "1")) or "1"),
                pdf_bbox, raw_str
            )

            # Build the claim
            claim = {
                "symbol": field[:64],
                "value": numeric_val if numeric_val is not None else raw_str,
                "unit": unit,
                "qualifier": role,
                "condition": table_title if table_title else None,
                "quote": quote_text,
                "column_header": col_header,
                "row_header": _find_row_header(cells, columns),
                "page": str(region.get("page", "")),
                "doc_sha256": doc_sha256,
                "region_bbox": region.get("region_bbox"),
                "cell_bbox": cell_bbox,
                "pdf_bbox": pdf_bbox,
                "extractor": extractor,
                "extractor_version": extractor_version,
                "verification": [crop_result],
                "verification_status": crop_result["verdict"],
                "table_class": tclass,
                "absolute_max": abs_max,
                "recommended_operation": is_op,
                "source_file": f"{filename}",
                "region_idx": region_idx,
                "table_idx": table_idx,
                "row_idx": r_idx,
                "col_name": col_name,
                # Row relationship: all claims from the same row share this ID
                # so downstream consumers know Pin=1, Name=GND, Type=Power
                # are one coherent pin description, not independent facts
                "row_id": f"{region_idx}_{table_idx}_{r_idx}",
                "row_cells": {k: str(v) for k, v in cells.items() if v},
                "doc_identity": {
                    "sha256": doc_sha256,
                    "filename": filename,
                    "opn_established": False,
                    "family_applicability": "unknown",
                },
            }
            claims.append(claim)

    return claims, held


def _find_row_header(cells: dict, columns: list[str]) -> str | None:
    """Extract the row label — typically the first non-empty cell value."""
    # Prefer cells whose key matches a known row-label column
    for col in columns:
        if col and col in cells and cells[col]:
            return str(cells[col])
    # Fallback: first non-empty cell value
    for v in cells.values():
        if v:
            return str(v)
    return None


def _match_verification(
    verdicts: list, table_idx: int, row_idx: int, col_name: str
) -> list:
    """Find verification verdicts matching this cell."""
    matched = []
    for v in verdicts or []:
        if (v.get("table") == table_idx and
                v.get("row") == row_idx and
                v.get("col") == col_name):
            matched.append(v)
    return matched


def _verification_status(
    verdicts: list, table_idx: int, row_idx: int, col_name: str
) -> str:
    """Return verification status: PASS, FAIL, or UNKNOWN.

    Empty verdict list is UNKNOWN, not PASS. This is the rule from
    the assignment: "An empty verdict list is UNKNOWN, not successful
    verification."
    """
    matched = _match_verification(verdicts, table_idx, row_idx, col_name)
    if not matched:
        return "UNKNOWN"
    if all(v.get("match") for v in matched):
        return "PASS"
    return "FAIL"


def resolve_pdf(sha256: str, filename: str) -> str | None:
    """Resolve PDF path from content-addressed blob store."""
    if not sha256 or len(sha256) != 64:
        return None
    blob_root = Path("/Volumes/M5_4TB/exports/corpus-deliveries")
    hits = list(blob_root.glob(f"*/blobs/{sha256[:2]}/{sha256[2:4]}/{sha256}.pdf"))
    if hits:
        return str(hits[0])
    return None


def process_doc(input_path: Path, verbose: bool = False) -> dict:
    """Process one vision-v2 artifact. Returns {stem, claims, held, stats}."""
    rec = json.loads(input_path.read_text())
    doc_sha = rec.get("sha256", "")
    filename = rec.get("filename", input_path.stem)
    extractor = rec.get("extractor", "vision_worker")
    extractor_version = rec.get("extractor_version", "unknown")

    # Resolve PDF for crop verification
    pdf_path = resolve_pdf(doc_sha, filename)

    all_claims = []
    all_held = []
    stats = {"tables": 0, "rows": 0, "cells": 0}

    for r_idx, region in enumerate(rec.get("page_regions", [])):
        tables = region.get("tables", [])
        crop_verdicts = region.get("crop_verdicts", [])

        for t_idx, table in enumerate(tables):
            if not isinstance(table, dict):
                all_held.append({
                    "reason": "malformed_table",
                    "region_idx": r_idx,
                    "table_idx": t_idx,
                })
                continue

            stats["tables"] += 1
            stats["rows"] += len(table.get("rows", []))

            claims, held = table_to_claims(
                table, region, doc_sha, filename,
                extractor, extractor_version,
                r_idx, t_idx, crop_verdicts,
                pdf_path=pdf_path,
            )
            all_claims.extend(claims)
            all_held.extend(held)
            stats["cells"] += len(claims) + len(held)

    return {
        "stem": input_path.stem,
        "claims": all_claims,
        "held": all_held,
        "stats": stats,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="vision-v2 directory")
    ap.add_argument("--output", required=True, help="isolated output directory")
    ap.add_argument("--limit", type=int, default=0, help="max docs to process")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    input_dir = Path(args.input)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    if not input_dir.is_dir():
        print(f"ERROR: input dir not found: {input_dir}", file=sys.stderr)
        return 1

    # Read-only: never modify input
    files = sorted(input_dir.glob("*.json"))
    if args.limit:
        files = files[: args.limit]

    print(f"processing {len(files)} documents from {input_dir}")

    totals = {"docs": 0, "tables": 0, "rows": 0, "cells": 0,
              "claims": 0, "held": 0}

    for f in files:
        try:
            result = process_doc(f, args.verbose)
        except Exception as e:
            print(f"  ERROR {f.name}: {e}", file=sys.stderr)
            continue

        # Write adapter output (burn-compatible format)
        out_rec = {
            "stem": result["stem"],
            "tier": "vision-adapter",
            "claims": len(result["claims"]),
            "surviving": len(result["claims"]),
            "p0": 0,
            "claim_data": result["claims"],
            "held_evidence": result["held"],
            "adapter": {
                "version": "0.1.0",
                "source": str(f),
                "doc_sha256": result["claims"][0]["doc_sha256"] if result["claims"] else None,
            },
        }
        out_path = output_dir / f"{result['stem']}.json"
        out_path.write_text(json.dumps(out_rec, ensure_ascii=False))

        totals["docs"] += 1
        totals["tables"] += result["stats"]["tables"]
        totals["rows"] += result["stats"]["rows"]
        totals["cells"] += result["stats"]["cells"]
        totals["claims"] += len(result["claims"])
        totals["held"] += len(result["held"])

        if args.verbose:
            print(f"  {result['stem'][:40]}: "
                  f"{len(result['claims'])} claims, "
                  f"{len(result['held'])} held, "
                  f"{result['stats']['tables']} tables")

    print(f"\n=== SUMMARY ===")
    print(f"documents: {totals['docs']}")
    print(f"tables:    {totals['tables']}")
    print(f"rows:      {totals['rows']}")
    print(f"cells:     {totals['cells']}")
    print(f"claims:    {totals['claims']}")
    print(f"held:      {totals['held']}")

    # Write summary manifest
    manifest = {
        "adapter": "vision-to-claims",
        "version": "0.1.0",
        "input": str(input_dir),
        "output": str(output_dir),
        "totals": totals,
    }
    (output_dir / "_adapter_manifest.json").write_text(
        json.dumps(manifest, indent=2)
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
