#!/usr/bin/env python3
"""Crop-verify: the deterministic close-loop between image-truth and text-truth.

For a vision claim carrying a cell bbox: crop the exact region from the
source PDF page, OCR it (tesseract, free, deterministic), string-match the
OCR text against the claimed value. Image says one thing, pixels say
another -> P0. This is Phase 2 of TRUTH_PLAN.
"""

import re
import subprocess
import tempfile
from pathlib import Path

import pymupdf

from harness.pipeline.quote_verify import norm


def ocr_crop(pdf_path: str, page_1based: int, bbox, dpi=300):
    """Crop bbox from the PDF page and OCR it. Returns raw OCR text."""
    doc = pymupdf.open(pdf_path)
    if page_1based > len(doc):
        doc.close()
        return ""
    page = doc[page_1based - 1]
    clip = pymupdf.Rect(bbox)
    pix = page.get_pixmap(dpi=dpi, clip=clip)
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        pix.save(f)
        path = f.name
    doc.close()
    try:
        out = subprocess.run(
            ["tesseract", path, "stdout", "--psm", "6"],
            capture_output=True, text=True, timeout=60)
        return out.stdout
    except Exception:
        return ""
    finally:
        Path(path).unlink(missing_ok=True)


def verify_cell(pdf_path: str, page_1based: int, bbox, value, tolerance=1e-4):
    """Deterministic verdict for a grounded cell claim.

    Returns {verdict, reason_code, ocr_text, severity}:
      SUPPORTED  — OCR text contains the value (number-normalized)
      UNSUPPORTED cell_ocr_mismatch (P0) — OCR read the region, value absent
      REVIEW     bbox_unreadable (P1) — OCR produced nothing usable
    """
    ocr = ocr_crop(pdf_path, page_1based, bbox)
    ocr_norm = norm(ocr)
    if len(ocr_norm) < 2:
        return {"verdict": "REVIEW", "reason_code": "bbox_unreadable",
                "ocr_text": ocr[:80], "severity": "P1"}
    try:
        val = float(value)
        nums = re.findall(r"-?\d+(?:\.\d+)?", ocr_norm.replace(",", ""))
        if any(abs(float(n) - val) <= max(1e-9, abs(val) * tolerance) for n in nums):
            return {"verdict": "SUPPORTED", "reason_code": None,
                    "ocr_text": ocr[:80], "severity": "P2"}
    except (TypeError, ValueError):
        pass
    if isinstance(value, str) and norm(value) in ocr_norm:
        return {"verdict": "SUPPORTED", "reason_code": None,
                "ocr_text": ocr[:80], "severity": "P2"}
    return {"verdict": "UNSUPPORTED", "reason_code": "cell_ocr_mismatch",
            "ocr_text": ocr[:80], "severity": "P0"}


def verify_tables(pdf_path: str, page_1based: int, region_result: dict):
    """Verify all rows of a vision-v2 table extraction that carry bboxes."""
    verdicts = []
    for t_idx, table in enumerate(region_result.get("tables", [])):
        if not isinstance(table, dict):
            continue
        for r_idx, row in enumerate(table.get("rows", [])):
            if not isinstance(row, dict) or "bbox" not in row:
                continue
            for col, cell in row.items():
                if col in ("bbox",) or cell is None:
                    continue
                if not isinstance(cell, (dict,)) or "value" not in cell:
                    continue
                v = verify_cell(pdf_path, page_1based, row["bbox"], cell["value"])
                v.update({"table": t_idx, "row": r_idx, "col": col})
                verdicts.append(v)
    return verdicts
