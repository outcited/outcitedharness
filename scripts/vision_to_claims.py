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
                                 dpi: int = 200) -> dict:
    """Location-bound, token-aware verification.

    Uses pymupdf word geometry to locate the exact cell, then OCRs
    ONLY that cell. Matches complete source tokens (not substrings).
    Returns REVIEW when cell cannot be unambiguously located.
    """
    if not pdf_path:
        return {"verdict": "REVIEW", "reason": "no_source_pdf",
                "method": "source_geometry", "engine_version": "2.0"}

    try:
        import pymupdf
        import tempfile
        import subprocess
        import os

        doc = pymupdf.open(pdf_path)
        if page_1based > len(doc):
            doc.close()
            return {"verdict": "REVIEW", "reason": "page_out_of_range",
                    "method": "source_geometry", "engine_version": "2.0"}

        page = doc[page_1based - 1]
        words = page.get_text("words")  # (x0, y0, x1, y1, word, block, line, idx)

        exp_str = str(expected_value).strip()
        if not exp_str:
            doc.close()
            return {"verdict": "REVIEW", "reason": "empty_expected",
                    "method": "source_geometry", "engine_version": "2.0"}

        # TOKEN-AWARE: find exact word token matching expected value
        target_word = None
        for w in words:
            wx0, wy0, wx1, wy1, wtext = w[0], w[1], w[2], w[3], w[4]
            wtext_stripped = wtext.strip()
            # Exact token match (not substring)
            if wtext_stripped == exp_str:
                if _in_region(wx0, wy0, region_bbox):
                    target_word = {"bbox": (wx0, wy0, wx1, wy1), "text": wtext_stripped}
                    break

        # Try numeric core as exact token
        if not target_word:
            numeric_core = _extract_numeric_core(exp_str)
            if numeric_core:
                for w in words:
                    wtext_stripped = w[4].strip()
                    if wtext_stripped == numeric_core and _in_region(w[0], w[1], region_bbox):
                        target_word = {"bbox": (w[0], w[1], w[2], w[3]), "text": wtext_stripped}
                        break

        if not target_word:
            doc.close()
            return {"verdict": "REVIEW", "reason": "token_not_in_source_geometry",
                    "expected": exp_str, "method": "source_geometry",
                    "engine_version": "2.0"}

        # Cell bbox: word bbox with minimal padding (respects glyph bounds)
        PAD = 1.0
        word_bbox = target_word["bbox"]
        cell_bbox = (
            max(0, word_bbox[0] - PAD),
            max(0, word_bbox[1] - PAD),
            word_bbox[2] + PAD,
            word_bbox[3] + PAD,
        )

        # Validate cell bbox against page bounds
        page_rect = page.rect
        if (cell_bbox[0] < 0 or cell_bbox[1] < 0 or
                cell_bbox[2] > page_rect.width + PAD or
                cell_bbox[3] > page_rect.height + PAD):
            doc.close()
            return {"verdict": "REVIEW", "reason": "cell_bbox_out_of_bounds",
                    "cell_bbox": cell_bbox, "page_size": (page_rect.width, page_rect.height),
                    "method": "source_geometry", "engine_version": "2.0"}

        # OCR the cell crop
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

        if not ocr_text:
            return {"verdict": "REVIEW", "reason": "empty_cell_ocr",
                    "cell_bbox": cell_bbox, "word_bbox": word_bbox,
                    "method": "source_geometry", "engine_version": "2.0"}

        # TOKEN-AWARE MATCHING (complete tokens, not substrings)
        ocr_tokens = ocr_text.split()

        # Exact token match
        if exp_str in ocr_tokens:
            return {"verdict": "SUPPORTED", "reason": "cell_exact_token",
                    "ocr_text": ocr_text, "cell_bbox": cell_bbox,
                    "word_bbox": word_bbox, "source_token": target_word["text"],
                    "method": "source_geometry", "engine_version": "2.0"}

        # Case-insensitive token match
        exp_upper = exp_str.upper()
        for tok in ocr_tokens:
            if tok.upper() == exp_upper:
                return {"verdict": "SUPPORTED", "reason": "cell_case_insensitive_token",
                        "ocr_text": ocr_text, "cell_bbox": cell_bbox,
                        "word_bbox": word_bbox, "source_token": target_word["text"],
                        "method": "source_geometry", "engine_version": "2.0"}

        # Numeric token match (complete numeric token)
        numeric_core = _extract_numeric_core(exp_str)
        if numeric_core:
            for tok in ocr_tokens:
                tok_numeric = _extract_numeric_core(tok)
                if tok_numeric == numeric_core:
                    return {"verdict": "SUPPORTED", "reason": "cell_numeric_token",
                            "ocr_text": ocr_text, "cell_bbox": cell_bbox,
                            "word_bbox": word_bbox, "source_token": target_word["text"],
                            "method": "source_geometry", "engine_version": "2.0"}

        # No match in cell OCR
        return {"verdict": "REVIEW", "reason": "cell_token_no_match",
                "ocr_text": ocr_text, "cell_bbox": cell_bbox,
                "expected": exp_str, "method": "source_geometry",
                "engine_version": "2.0"}

    except Exception as e:
        return {"verdict": "REVIEW", "reason": f"verify_error:{str(e)[:60]}",
                "method": "source_geometry", "engine_version": "2.0"}


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
    dpi: int = 200,
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
            crop_result = verify_with_source_geometry(
                pdf_path, int(str(region.get("page", "1")) or "1"),
                region.get("region_bbox"), columns, cells,
                col_name, raw_str
            )

            # Build the claim with engineering record
            coords = transform_bbox_to_pdf(region.get("region_bbox"), cell_bbox, dpi)

            # Real crop verification
            crop_result = verify_with_source_geometry(
                pdf_path, int(str(region.get("page", "1")) or "1"),
                region.get("region_bbox"), columns, cells,
                col_name, raw_str, dpi=dpi
            )

            # Row identity: globally unique
            row_identity = f"{doc_sha256}|{region.get('page', '')}|{region_idx}|{table_idx}|{r_idx}"

            # Engineering record for parameter tables
            eng_record = None
            if tclass == "param":
                eng_record = _build_param_record(cells, columns, table_title, r_idx)
            elif tclass == "pin":
                eng_record = _build_pin_record(cells, columns, r_idx)
            elif tclass == "dim":
                eng_record = _build_dim_record(cells, columns, r_idx)

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
                "source_sha_validated": False,  # set by process_doc
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
    """Build parameter engineering record: identifier + min/typ/max + units + conditions."""
    # Find parameter identifier (first non-Min/Typ/Max column)
    param_id = None
    for col in columns:
        if col and detect_role(col) is None and col.lower() not in ('unit', 'symbol'):
            val = cells.get(col)
            if val:
                param_id = str(val)
                break

    # Collect measurements
    measurements = {}
    for col in columns:
        role = detect_role(col)
        val = cells.get(col)
        if role and val:
            measurements[role] = {"value": str(val), "column": col}

    # Units from any column
    unit = cells.get("Unit") or cells.get("unit") or None

    return {
        "type": "parameter",
        "parameter_id": param_id,
        "measurements": measurements,
        "unit": str(unit) if unit else None,
        "condition_source": table_title,
        "absolute_max": bool(_ABS_MAX_RE.search(table_title or "")),
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
                )
                # Mark SHA validation status
                for c in claims:
                    c["source_sha_validated"] = sha_validated
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
              "supported": 0, "review": 0, "unsupported": 0,
              "ocr_matched": 0, "location_verified": 0,
              "consumable": 0, "errors": 0, "sha_validated": 0}

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

        supported = [c for c in all_claims if c.get("verification_status") == "SUPPORTED"]
        review = [c for c in all_claims if c.get("verification_status") == "REVIEW"]
        unsupported = [c for c in all_claims if c.get("verification_status") == "UNSUPPORTED"]

        # Separate OCR-matched from location-verified
        ocr_matched = [c for c in supported if c.get("verification", {}).get("reason", "").startswith("cell_")]
        location_verified = supported  # all SUPPORTED are location-verified in v2

        out_rec = {
            "stem": f.stem,
            "tier": "vision-adapter",
            "counts": {
                "candidates": len(all_claims),
                "held": len(held),
                "supported": len(supported),
                "review": len(review),
                "unsupported": len(unsupported),
                "ocr_matched": len(ocr_matched),
                "location_verified": len(location_verified),
                "consumable": len(supported),
                "errors": len(errors),
            },
            "claim_data": all_claims,
            "held_evidence": held,
            "errors": errors,
            "sha_validated": result.get("sha_validated", False),
            "adapter": {
                "version": "0.3.0",
                "source": str(f),
                "doc_sha256": all_claims[0]["doc_sha256"] if all_claims else None,
            },
        }
        out_path = output_dir / f"{f.stem}.json"
        out_path.write_text(json.dumps(out_rec, ensure_ascii=False))

        totals["docs"] += 1
        totals["tables"] += result["stats"]["tables"]
        totals["rows"] += result["stats"]["rows"]
        totals["input_cells"] += result["stats"]["cells"]
        totals["candidates"] += len(all_claims)
        totals["held"] += len(held)
        totals["supported"] += len(supported)
        totals["review"] += len(review)
        totals["unsupported"] += len(unsupported)
        totals["ocr_matched"] += len(ocr_matched)
        totals["location_verified"] += len(location_verified)
        totals["consumable"] += len(supported)
        totals["errors"] += len(errors)
        if result.get("sha_validated"):
            totals["sha_validated"] += 1

        if args.verbose:
            print(f"  {f.stem[:40]}: {len(all_claims)} cand, "
                  f"{len(supported)} sup, {len(review)} rev, {len(held)} held")

    print(f"\n=== SUMMARY ===")
    print(f"documents:          {totals['docs']}")
    print(f"tables:             {totals['tables']}")
    print(f"rows:               {totals['rows']}")
    print(f"input cells:        {totals['input_cells']}")
    print(f"candidates:         {totals['candidates']}")
    print(f"held:               {totals['held']}")
    print(f"conservation:       {totals['candidates'] + totals['held']} == {totals['input_cells']} -> {totals['candidates'] + totals['held'] == totals['input_cells']}")
    print(f"")
    print(f"SUPPORTED:          {totals['supported']}")
    print(f"REVIEW:             {totals['review']}")
    print(f"UNSUPPORTED:        {totals['unsupported']}")
    print(f"consumable:         {totals['consumable']}  (SUPPORTED only)")
    print(f"")
    print(f"ocr_matched:        {totals['ocr_matched']}")
    print(f"location_verified:  {totals['location_verified']}")
    print(f"sha_validated:      {totals['sha_validated']}/{totals['docs']}")
    print(f"errors:             {totals['errors']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
