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
                "extractor": extractor,
                "extractor_version": extractor_version,
                "verification": _match_verification(
                    verification or [], table_idx, r_idx, col_name
                ),
                "table_class": tclass,
                "absolute_max": abs_max,
                "recommended_operation": is_op,
                "source_file": f"{filename}",
                "region_idx": region_idx,
                "table_idx": table_idx,
                "row_idx": r_idx,
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


def process_doc(input_path: Path, verbose: bool = False) -> dict:
    """Process one vision-v2 artifact. Returns {stem, claims, held, stats}."""
    rec = json.loads(input_path.read_text())
    doc_sha = rec.get("sha256", "")
    filename = rec.get("filename", input_path.stem)
    extractor = rec.get("extractor", "vision_worker")
    extractor_version = rec.get("extractor_version", "unknown")

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
