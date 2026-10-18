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
import os
import re
import sys
from pathlib import Path

# Configurable source roots (for happy compatibility)
# Override with V2C_BLOB_ROOT env var
BLOB_ROOT = Path(os.environ.get(
    "V2C_BLOB_ROOT",
    "/Volumes/M5_4TB/exports/corpus-deliveries"
))

# Coordinate transform: image-local pixels -> PDF page coordinates
# vision-v2 region_bbox is PDF points (from pymupdf)
# row.bbox is image-local pixels at 200 DPI
# PDF_coord = region_origin + pixel * (72 / DPI)
DPI = 200
PDF_PER_PIXEL = 72.0 / DPI  # 0.36


def transform_bbox_to_pdf(region_bbox: list, pixel_bbox: list, dpi: int = 200) -> dict:
    """Transform image-local pixel bbox to PDF-page coordinates.

    Returns dict with distinct coordinate names (per assignment §3):
      - pdf_region_bbox: PDF points of the region (from pymupdf)
      - image_row_bbox: image-local pixels (from vision model)
      - pdf_row_bbox: transformed PDF points for the row
      - pdf_cell_bbox: verified PDF points for the cell (when available)
    """
    if not region_bbox or not pixel_bbox:
        return {"pdf_region_bbox": region_bbox, "image_row_bbox": pixel_bbox,
                "pdf_row_bbox": None, "scale_factor": None}

    scale = 72.0 / dpi  # PDF points per pixel
    rx0, ry0 = region_bbox[0], region_bbox[1]
    px0, py0, px1, py1 = pixel_bbox[0], pixel_bbox[1], pixel_bbox[2], pixel_bbox[3]

    pdf_row_bbox = (
        rx0 + px0 * scale,
        ry0 + py0 * scale,
        rx0 + px1 * scale,
        ry0 + py1 * scale,
    )

    return {
        "pdf_region_bbox": region_bbox,
        "image_row_bbox": pixel_bbox,
        "pdf_row_bbox": pdf_row_bbox,
        "scale_factor": scale,
        "dpi": dpi,
    }


def verify_with_source_geometry(pdf_path: str, page_1based: int, region_bbox: list,
                                 table_columns: list, row_cells: dict,
                                 target_col: str, expected_value: str,
                                 dpi: int = 200, row_idx: int = 0,
                                 table_idx: int = 0, region_idx: int = 0) -> dict:
    """LOCATION-FIRST verification. Not answer-first.

    1. Locate the source table row using PDF geometry (word positions).
    2. Establish the source column using printed header geometry.
    3. Find the cell at row×column intersection.
    4. Read/OCR that cell.
    5. Compare observed value against extracted claim.

    Does NOT use expected_value to select the source cell.
    Where table geometry is unavailable, returns REVIEW.
    """
    if not pdf_path:
        return {"verdict": "REVIEW", "reason": "no_source_pdf",
                "method": "location_first", "engine_version": "3.0"}

    try:
        import pymupdf
        import tempfile
        import subprocess
        import os

        doc = pymupdf.open(pdf_path)
        if page_1based > len(doc):
            doc.close()
            return {"verdict": "REVIEW", "reason": "page_out_of_range",
                    "method": "location_first", "engine_version": "3.0"}

        page = doc[page_1based - 1]
        words = page.get_text("words")  # (x0, y0, x1, y1, word, block, line, idx)

        # STEP 1: Locate the source row using row position.
        # We know the row_idx. Find the Nth row in the table region.
        region_words = [w for w in words
                       if _in_region(w[0], w[1], region_bbox)]
        if not region_words:
            doc.close()
            return {"verdict": "REVIEW", "reason": "no_words_in_region",
                    "method": "location_first", "engine_version": "3.0"}

        # Group words into rows by y-position
        rows_by_y = {}
        for w in region_words:
            y_key = round(w[1] / 5) * 5  # cluster by 5pt bands
            rows_by_y.setdefault(y_key, []).append(w)

        # Sort rows by y-position (top to bottom)
        sorted_y = sorted(rows_by_y.keys())
        # row_idx 0 = first data row (after header)
        # We'll adjust after finding header_y_idx
        # For now, use row_idx directly (will adjust below)

        # STEP 2: Establish column geometry from headers.
        # Find the actual column header row — skip title rows.
        # The header row is the one whose words match known column names.
        if len(sorted_y) < 2:
            doc.close()
            return {"verdict": "REVIEW", "reason": "no_header_row",
                    "method": "location_first", "engine_version": "3.0"}

        # Look for header row by checking if words match table_columns
        header_words = None
        header_y_idx = 0
        for yi, y_key in enumerate(sorted_y[:3]):  # check first 3 rows
            row_words = rows_by_y[y_key]
            row_texts = [w[4].strip() for w in row_words]
            # If any word matches a known column name, this is the header row
            matches = sum(1 for t in row_texts if t in (table_columns or []))
            if matches >= max(1, len(table_columns or []) // 3):
                header_words = row_words
                header_y_idx = yi
                break

        if not header_words:
            # Fallback: use second row (skip title)
            if len(sorted_y) >= 2:
                header_words = rows_by_y[sorted_y[1]]
                header_y_idx = 1
            else:
                doc.close()
                return {"verdict": "REVIEW", "reason": "no_header_row",
                        "method": "location_first", "engine_version": "3.0"}
        # Map column headers to x-ranges
        col_x_ranges = {}
        for hw in header_words:
            htext = hw[4].strip()
            if htext:
                col_x_ranges[htext] = (hw[0], hw[2])  # (x_start, x_end)

        # Find target column x-range
        target_col_x = None
        for header, (x0, x1) in col_x_ranges.items():
            if header.lower() == target_col.lower() or header == target_col:
                target_col_x = (x0, x1)
                break

        if not target_col_x:
            # Column not found by header — cannot locate cell
            doc.close()
            return {"verdict": "REVIEW", "reason": "column_header_not_found",
                    "target_col": target_col,
                    "available_headers": list(col_x_ranges.keys())[:10],
                    "method": "location_first", "engine_version": "3.0"}

        # Adjust row_idx for header offset (row 0 = first data row)
        data_row_y_idx = header_y_idx + 1 + row_idx
        if data_row_y_idx >= len(sorted_y):
            doc.close()
            return {"verdict": "REVIEW", "reason": "row_idx_out_of_range",
                    "found_rows": len(sorted_y), "requested": row_idx,
                    "header_y": header_y_idx,
                    "method": "location_first", "engine_version": "3.0"}

        target_row_words = rows_by_y[sorted_y[data_row_y_idx]]

        # STEP 3: Find word at row×column intersection
        col_x0, col_x1 = target_col_x
        target_word = None
        for w in target_row_words:
            wx0, wx1 = w[0], w[2]
            # Word must be within column x-range (with tolerance)
            TOL = 5.0
            if (wx0 >= col_x0 - TOL and wx1 <= col_x1 + TOL) or                (wx0 >= col_x0 - TOL and wx0 <= col_x1 + TOL):
                target_word = {"bbox": (w[0], w[1], w[2], w[3]), "text": w[4].strip()}
                break

        if not target_word:
            doc.close()
            return {"verdict": "REVIEW", "reason": "no_word_in_cell",
                    "col_x_range": target_col_x,
                    "row_words": [w[4].strip() for w in target_row_words[:10]],
                    "method": "location_first", "engine_version": "3.0"}

        # STEP 4: OCR the located cell
        PAD = 1.0
        word_bbox = target_word["bbox"]
        cell_bbox = (
            max(0, word_bbox[0] - PAD),
            max(0, word_bbox[1] - PAD),
            word_bbox[2] + PAD,
            word_bbox[3] + PAD,
        )

        # Validate bounds
        page_rect = page.rect
        if (cell_bbox[0] < -PAD or cell_bbox[1] < -PAD or
                cell_bbox[2] > page_rect.width + PAD or
                cell_bbox[3] > page_rect.height + PAD):
            doc.close()
            return {"verdict": "REVIEW", "reason": "cell_bbox_out_of_bounds",
                    "cell_bbox": cell_bbox,
                    "method": "location_first", "engine_version": "3.0"}

        # OCR
        clip = pymupdf.Rect(*cell_bbox)
        pix = page.get_pixmap(dpi=300, clip=clip)
        tmpf = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        try:
            pix.save(tmpf.name)
            tmpf.close()
            result = subprocess.run(
                ["tesseract", tmpf.name, "stdout", "--psm", "7"],
                capture_output=True, text=True, timeout=30
            )
            ocr_text = result.stdout.strip()
        finally:
            try:
                os.unlink(tmpf.name)
            except OSError:
                pass

        doc.close()

        # STEP 5: Compare observed vs claimed
        observed_token = target_word["text"]
        expected_str = str(expected_value).strip()

        if not observed_token:
            return {"verdict": "REVIEW", "reason": "empty_observed_cell",
                    "cell_bbox": cell_bbox, "word_bbox": word_bbox,
                    "method": "location_first", "engine_version": "3.0"}

        # Exact match
        if observed_token == expected_str:
            return {"verdict": "SUPPORTED", "reason": "location_exact_match",
                    "observed": observed_token, "expected": expected_str,
                    "ocr_text": ocr_text, "cell_bbox": cell_bbox,
                    "word_bbox": word_bbox,
                    "column_header": target_col, "row_idx": row_idx,
                    "method": "location_first", "engine_version": "3.0"}

        # Case-insensitive
        if observed_token.upper() == expected_str.upper():
            return {"verdict": "SUPPORTED", "reason": "location_case_insensitive",
                    "observed": observed_token, "expected": expected_str,
                    "ocr_text": ocr_text, "cell_bbox": cell_bbox,
                    "word_bbox": word_bbox,
                    "column_header": target_col, "row_idx": row_idx,
                    "method": "location_first", "engine_version": "3.0"}

        # Numeric core match
        obs_num = _extract_numeric_core(observed_token)
        exp_num = _extract_numeric_core(expected_str)
        if obs_num and exp_num and obs_num == exp_num:
            return {"verdict": "SUPPORTED", "reason": "location_numeric_match",
                    "observed": observed_token, "expected": expected_str,
                    "ocr_text": ocr_text, "cell_bbox": cell_bbox,
                    "word_bbox": word_bbox,
                    "column_header": target_col, "row_idx": row_idx,
                    "method": "location_first", "engine_version": "3.0"}

        # Different value at the located cell
        return {"verdict": "REVIEW", "reason": "location_mismatch",
                "observed": observed_token, "expected": expected_str,
                "ocr_text": ocr_text, "cell_bbox": cell_bbox,
                "word_bbox": word_bbox,
                "column_header": target_col, "row_idx": row_idx,
                "method": "location_first", "engine_version": "3.0"}

    except Exception as e:
        return {"verdict": "REVIEW", "reason": f"verify_error:{str(e)[:60]}",
                "method": "location_first", "engine_version": "3.0"}


def _in_region(x: float, y: float, region_bbox: list) -> bool:
    """Check if point is within region bbox."""
    if not region_bbox:
        return True
    return (region_bbox[0] <= x <= region_bbox[2] and
            region_bbox[1] <= y <= region_bbox[3])


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
    """Classify table type. Symbol alone is NOT parameter.

    Returns 'param'|'pin'|'dim'|'unknown'.
    """
    cols_joined = " ".join(c or "" for c in columns)
    title = (table_title or "").lower()

    # Pin table: has pin number + pin name/description columns
    if _PIN_COL_RE.search(cols_joined):
        return "pin"

    # Package dimensions: has DIM + INCHES + MILLIMETERS
    if (_DIM_COL_RE.search(cols_joined) and
            _INCH_COL_RE.search(cols_joined) and
            _MM_COL_RE.search(cols_joined)):
        return "dim"

    # Parameter table: needs Min/Typ/Max columns AND parameter identifier
    # Symbol alone is NOT enough — it appears in pin tables too
    has_role_columns = any(detect_role(c) for c in columns)
    has_param_id = any(
        _PARAM_COL_RE.search(c or "") and detect_role(c) is None
        for c in columns
    )
    # Also check title for parameter context
    has_param_context = any(kw in title for kw in [
        'characteristic', 'rating', 'specification', 'electrical',
        'thermal', 'parametric', 'performance'
    ])

    if (has_role_columns and has_param_id) or (has_role_columns and has_param_context):
        return "param"

    # Dimension table: DIM column without inch/mm (simple dims)
    if _DIM_COL_RE.search(cols_joined) and not has_role_columns:
        return "dim"

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
    dpi: int = 200,
    sha_validated: bool = False,) -> tuple[list[dict], list[dict]]:
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

            # Coordinate transform
            coords = transform_bbox_to_pdf(region.get("region_bbox"), cell_bbox, dpi)

            # LOCATION-FIRST verification (single call, no duplicate OCR)
            crop_result = verify_with_source_geometry(
                pdf_path, int(str(region.get("page", "1")) or "1"),
                region.get("region_bbox"), columns, cells,
                col_name, raw_str,
                dpi=dpi, row_idx=r_idx, table_idx=table_idx, region_idx=region_idx
            )

            # Row identity: globally unique
            row_identity = f"{doc_sha256}|{region.get('page', '')}|{region_idx}|{table_idx}|{r_idx}"

            # Engineering record
            eng_record = None
            if tclass == "param":
                eng_record = _build_param_record(cells, columns, table_title, r_idx)
            elif tclass == "pin":
                eng_record = _build_pin_record(cells, columns, r_idx)
            elif tclass == "dim":
                eng_record = _build_dim_record(cells, columns, r_idx)

            # SHA HARD GATE: if SHA not validated, claim cannot be SUPPORTED
            if not sha_validated and crop_result["verdict"] == "SUPPORTED":
                crop_result["verdict"] = "REVIEW"
                crop_result["reason"] = "sha_not_validated"

            claim = {
                "symbol": field[:64],
                "value": numeric_val if numeric_val is not None else raw_str,
                "unit": unit,
                "qualifier": role,
                "condition": table_title if table_title else None,
                "quote": quote_text,
                "column_header": col_header,
                "row_header": _find_row_header(cells, columns),
                "page": int(region.get("page", 0)) if str(region.get("page", "")).isdigit() else None,
                "doc_sha256": doc_sha256,
                "pdf_region_bbox": coords["pdf_region_bbox"],
                "image_row_bbox": coords["image_row_bbox"],
                "pdf_row_bbox": coords["pdf_row_bbox"],
                "pdf_cell_bbox": crop_result.get("cell_bbox"),
                "dpi": dpi,
                "scale_factor": coords["scale_factor"],
                "extractor": extractor,
                "extractor_version": extractor_version,
                "verification": crop_result,
                "verification_status": crop_result["verdict"],
                "verification_method": crop_result.get("method", "unknown"),
                "engine_version": crop_result.get("engine_version", "unknown"),
                "table_class": tclass,
                "absolute_max": abs_max,
                "recommended_operation": is_op,
                "source_file": f"{filename}",
                "region_idx": region_idx,
                "table_idx": table_idx,
                "row_idx": r_idx,
                "col_name": col_name,
                "row_identity": row_identity,
                "row_cells": {k: str(v) for k, v in cells.items() if v},
                "engineering_record": eng_record,
                "doc_identity": {
                    "sha256": doc_sha256,
                    "filename": filename,
                    "opn_established": False,
                    "family_applicability": "unknown",
                },
                "source_sha_validated": sha_validated,
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
    hits = list(BLOB_ROOT.glob(f"*/blobs/{sha256[:2]}/{sha256[2:4]}/{sha256}.pdf"))
    if hits:
        return str(hits[0])
    return None


def validate_sha(pdf_path: str, expected_sha: str) -> bool:
    """Validate document SHA against actual source bytes."""
    if not pdf_path or not expected_sha:
        return False
    import hashlib
    try:
        with open(pdf_path, "rb") as f:
            actual = hashlib.sha256(f.read()).hexdigest()
        return actual == expected_sha
    except Exception:
        return False




def _build_param_record(cells: dict, columns: list, table_title: str, row_idx: int) -> dict:
    """Build parameter engineering record.

    Selects the parameter identifier correctly (not first arbitrary column).
    For example: VGS(th) is the parameter, not 'Symbol'.
    """
    # Parameter identifier: the cell value in the column that names the parameter
    # (e.g., 'Symbol', 'Parameter', 'Characteristic') — but the VALUE is what matters
    param_id = None
    for col in columns:
        if col and detect_role(col) is None and col.lower() not in ('unit', 'symbol'):
            val = cells.get(col)
            if val:
                param_id = str(val)
                break

    # If no non-symbol column, use Symbol column value
    if not param_id:
        for col in columns:
            if col and col.lower() == 'symbol':
                val = cells.get(col)
                if val:
                    param_id = str(val)
                    break

    # Collect measurements from Min/Typ/Max columns
    measurements = {}
    for col in columns:
        role = detect_role(col)
        val = cells.get(col)
        if role and val:
            measurements[role] = {"value": str(val), "column": col}

    # Units from Unit column
    unit = cells.get("Unit") or cells.get("unit") or None

    return {
        "type": "parameter",
        "parameter_id": param_id,
        "measurements": measurements,
        "unit": str(unit) if unit else None,
        "condition_source": table_title,
        "absolute_max": bool(_ABS_MAX_RE.search(table_title or "")),
        "row_idx": row_idx,
    }


def _build_pin_record(cells: dict, columns: list, row_idx: int) -> dict:
    """Build pin engineering record: identity + signal + type + description."""
    pin_id = cells.get("Pin") or cells.get("pin") or None
    signal = cells.get("Name") or cells.get("Signal") or None
    electrical = cells.get("Type") or None
    description = cells.get("Description(1)") or cells.get("Description") or None

    return {
        "type": "pin",
        "pin_identity": str(pin_id) if pin_id else None,
        "signal_name": str(signal) if signal else None,
        "electrical_type": str(electrical) if electrical else None,
        "description": str(description) if description else None,
    }


def _build_dim_record(cells: dict, columns: list, row_idx: int) -> dict:
    """Build dimension record. DIM identifier is NOT a limit."""
    dim_id = None
    for col in columns:
        if col and col.lower().strip() in ('dim', 'symbol', 'dimension'):
            val = cells.get(col)
            if val:
                dim_id = str(val)
                break

    # Limits: all columns EXCEPT the identifier column
    limits = {}
    for col in columns:
        if col and col.lower().strip() in ('dim', 'symbol', 'dimension'):
            continue  # skip identifier
        val = cells.get(col)
        if val:
            is_inch = 'inch' in (col or '').lower()
            is_mm = 'mm' in (col or '').lower() or 'millimeter' in (col or '').lower()
            unit_system = "inches" if is_inch else ("millimeters" if is_mm else None)
            role = detect_role(col)
            limits[col] = {"value": str(val), "unit_system": unit_system, "role": role}

    return {
        "type": "dimension",
        "dimension_id": dim_id,
        "limits": limits,
        "row_idx": row_idx,
    }


def resolve_pdf(sha256: str, filename: str) -> str | None:
    """Resolve PDF path from content-addressed blob store."""
    if not sha256 or len(sha256) != 64:
        return None
    hits = list(BLOB_ROOT.glob(f"*/blobs/{sha256[:2]}/{sha256[2:4]}/{sha256}.pdf"))
    if hits:
        return str(hits[0])
    return None


def validate_sha(pdf_path: str, expected_sha: str) -> bool:
    """Validate document SHA against actual source bytes."""
    if not pdf_path or not expected_sha:
        return False
    import hashlib
    try:
        with open(pdf_path, "rb") as f:
            actual = hashlib.sha256(f.read()).hexdigest()
        return actual == expected_sha
    except Exception:
        return False




def _build_param_record(cells: dict, columns: list, table_title: str, row_idx: int) -> dict:
    """Build parameter engineering record.

    Selects the parameter identifier correctly (not first arbitrary column).
    For example: VGS(th) is the parameter, not 'Symbol'.
    """
    # Parameter identifier: the cell value in the column that names the parameter
    # (e.g., 'Symbol', 'Parameter', 'Characteristic') — but the VALUE is what matters
    param_id = None
    for col in columns:
        if col and detect_role(col) is None and col.lower() not in ('unit', 'symbol'):
            val = cells.get(col)
            if val:
                param_id = str(val)
                break

    # If no non-symbol column, use Symbol column value
    if not param_id:
        for col in columns:
            if col and col.lower() == 'symbol':
                val = cells.get(col)
                if val:
                    param_id = str(val)
                    break

    # Collect measurements from Min/Typ/Max columns
    measurements = {}
    for col in columns:
        role = detect_role(col)
        val = cells.get(col)
        if role and val:
            measurements[role] = {"value": str(val), "column": col}

    # Units from Unit column
    unit = cells.get("Unit") or cells.get("unit") or None

    return {
        "type": "parameter",
        "parameter_id": param_id,
        "measurements": measurements,
        "unit": str(unit) if unit else None,
        "condition_source": table_title,
        "absolute_max": bool(_ABS_MAX_RE.search(table_title or "")),
        "row_idx": row_idx,
    }


def _build_pin_record(cells: dict, columns: list, row_idx: int) -> dict:
    """Build pin engineering record: identity + signal + type + description."""
    pin_id = cells.get("Pin") or cells.get("pin") or None
    signal = cells.get("Name") or cells.get("Signal") or None
    electrical = cells.get("Type") or None
    description = cells.get("Description(1)") or cells.get("Description") or None

    return {
        "type": "pin",
        "pin_identity": str(pin_id) if pin_id else None,
        "signal_name": str(signal) if signal else None,
        "electrical_type": str(electrical) if electrical else None,
        "description": str(description) if description else None,
    }


def _build_dim_record(cells: dict, columns: list, row_idx: int) -> dict:
    """Build dimension record: identifier + limits + unit system."""
    dim_id = None
    for col in columns:
        if col and 'dim' in col.lower():
            val = cells.get(col)
            if val:
                dim_id = str(val)
                break

    limits = {}
    for col in columns:
        val = cells.get(col)
        if val and col != dim_id:
            is_inch = 'inch' in (col or '').lower()
            is_mm = 'mm' in (col or '').lower() or 'millimeter' in (col or '').lower()
            unit_system = "inches" if is_inch else ("millimeters" if is_mm else None)
            role = detect_role(col)
            limits[col] = {"value": str(val), "unit_system": unit_system, "role": role}

    return {
        "type": "dimension",
        "dimension_id": dim_id,
        "limits": limits,
    }



def process_doc(input_path: Path, verbose: bool = False) -> dict:
    """Process one vision-v2 artifact. Returns {stem, claims, held, stats, errors}."""
    rec = json.loads(input_path.read_text())
    doc_sha = rec.get("sha256", "")
    filename = rec.get("filename", input_path.stem)
    extractor = rec.get("extractor", "vision_worker")
    extractor_version = rec.get("extractor_version", "unknown")

    # Resolve and validate PDF
    pdf_path = resolve_pdf(doc_sha, filename)
    sha_validated = False
    if pdf_path:
        sha_validated = validate_sha(pdf_path, doc_sha)

    all_claims = []
    all_held = []
    all_errors = []
    stats = {"tables": 0, "rows": 0, "cells": 0}

    # Get DPI from provenance
    provenance = rec.get("provenance", {})
    dpi = provenance.get("dpi", 200)

    for r_idx, region in enumerate(rec.get("page_regions", [])):
        tables = region.get("tables", [])

        for t_idx, table in enumerate(tables):
            if not isinstance(table, dict):
                all_held.append({
                    "reason": "malformed_table",
                    "region_idx": r_idx,
                    "table_idx": t_idx,
                })
                continue

            stats["tables"] += 1
            rows = table.get("rows", [])
            stats["rows"] += len(rows)

            try:
                claims, held = table_to_claims(
                    table, region, doc_sha, filename,
                    extractor, extractor_version,
                    r_idx, t_idx,
                    pdf_path=pdf_path,
                    dpi=dpi,
                    sha_validated=sha_validated,
                )
                all_claims.extend(claims)
                all_held.extend(held)
                stats["cells"] += len(claims) + len(held)
            except Exception as e:
                all_errors.append({
                    "region_idx": r_idx,
                    "table_idx": t_idx,
                    "error": str(e)[:200],
                })

    return {
        "stem": input_path.stem,
        "claims": all_claims,
        "held": all_held,
        "errors": all_errors,
        "stats": stats,
        "sha_validated": sha_validated,
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

    totals = {"docs": 0, "tables": 0, "rows": 0, "input_cells": 0,
              "candidates": 0, "held": 0,
              "text_matched": 0,
              "location_established": 0,
              "row_col_association": 0,
              "sha_validated": 0,
              "supported": 0, "review": 0, "unsupported": 0,
              "consumable": 0, "errors": 0}

    for f in files:
        try:
            result = process_doc(f, args.verbose)
        except Exception as e:
            print(f"  ERROR {f.name}: {e}", file=sys.stderr)
            totals["errors"] += 1
            continue

        all_claims = result["claims"]
        held = result["held"]
        errors = result.get("errors", [])
        sha_val = result.get("sha_validated", False)

        supported = [c for c in all_claims if c.get("verification_status") == "SUPPORTED"]
        review = [c for c in all_claims if c.get("verification_status") == "REVIEW"]
        unsupported = [c for c in all_claims if c.get("verification_status") == "UNSUPPORTED"]

        # Distinct metrics
        text_matched = [c for c in all_claims
                       if c.get("verification", {}).get("reason", "").startswith(("location_exact", "location_case", "location_numeric"))]
        location_established = [c for c in supported if c.get("verification", {}).get("cell_bbox")]
        row_col_assoc = [c for c in supported
                        if c.get("verification", {}).get("column_header") and
                           c.get("verification", {}).get("row_idx") is not None]

        # Consumable: SHA validated AND SUPPORTED
        consumable = supported if sha_val else []

        out_rec = {
            "stem": f.stem,
            "tier": "vision-adapter",
            "counts": {
                "candidates": len(all_claims),
                "held": len(held),
                "text_matched": len(text_matched),
                "location_established": len(location_established),
                "row_col_association": len(row_col_assoc),
                "sha_validated": sha_val,
                "supported": len(supported),
                "review": len(review),
                "unsupported": len(unsupported),
                "consumable": len(consumable),
                "errors": len(errors),
            },
            "claim_data": all_claims,
            "held_evidence": held,
            "errors": errors,
            "sha_validated": sha_val,
        }
        out_path = output_dir / f"{f.stem}.json"
        out_path.write_text(json.dumps(out_rec, ensure_ascii=False))

        totals["docs"] += 1
        totals["tables"] += result["stats"]["tables"]
        totals["rows"] += result["stats"]["rows"]
        totals["input_cells"] += result["stats"]["cells"]
        totals["candidates"] += len(all_claims)
        totals["held"] += len(held)
        totals["text_matched"] += len(text_matched)
        totals["location_established"] += len(location_established)
        totals["row_col_association"] += len(row_col_assoc)
        if sha_val:
            totals["sha_validated"] += 1
        totals["supported"] += len(supported)
        totals["review"] += len(review)
        totals["unsupported"] += len(unsupported)
        totals["consumable"] += len(consumable)
        totals["errors"] += len(errors)

    print(f"\n=== SUMMARY ===")
    print(f"documents:              {totals['docs']}")
    print(f"tables:                 {totals['tables']}")
    print(f"rows:                   {totals['rows']}")
    print(f"input cells:            {totals['input_cells']}")
    print(f"candidates:             {totals['candidates']}")
    print(f"held:                   {totals['held']}")
    print(f"conservation:           {totals['candidates'] + totals['held']} == {totals['input_cells']} -> {totals['candidates'] + totals['held'] == totals['input_cells']}")
    print()
    print(f"text_matched:           {totals['text_matched']}")
    print(f"location_established:   {totals['location_established']}")
    print(f"row_col_association:    {totals['row_col_association']}")
    print(f"sha_validated:          {totals['sha_validated']}/{totals['docs']}")
    print()
    print(f"SUPPORTED:              {totals['supported']}")
    print(f"REVIEW:                 {totals['review']}")
    print(f"UNSUPPORTED:            {totals['unsupported']}")
    print(f"consumable:             {totals['consumable']}  (SHA validated AND SUPPORTED)")
    print(f"errors:                 {totals['errors']}")



if __name__ == "__main__":
    sys.exit(main())

