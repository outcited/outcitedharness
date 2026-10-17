# VISION-TO-CLAIMS-01 — Integration Note

## Producer → Adapter → Validation → Consumer

```
PRODUCER (existing, unchanged)
  vision_worker_v2.py
  Input:  routes/*.json (route.vision_pages) + source PDFs
  Output: /Volumes/M5_4TB/extract-results/vision-v2/{stem}.json
  Format: {sha256, filename, needs_ocr, extractor, extractor_version,
           pages_processed, provenance, page_regions}
  Region: {page, region_bbox, region_source, dpi, tables, crop_verdicts}
  Table:  {table_title, columns, rows}
  Row:    {cells: {"col_name": "value", ...}, bbox: [x0,y0,x1,y1]}
  Units:  in cell values as printed (e.g. "60V", "3.2 mA", "-55 to 150")
  Coordinates: region_bbox is IMAGE-LOCAL pixels at 200 DPI

ADAPTER (new — this assignment)
  scripts/vision_to_claims.py
  Input:  vision-v2/{stem}.json (read-only)
  Output: isolated test root (NOT production)
  Format: burn-compatible claim_data rows:
          {symbol, value, unit, qualifier, condition, quote,
           column_header, row_header, page, doc_sha256, region_bbox,
           cell_bbox, extractor, extractor_version, verification}
  Provenance: full chain preserved (see field mapping below)

VALIDATION (existing, used by adapter)
  harness/pipeline/crop_verify.py
  verify_cell(pdf_path, page_1based, bbox, value) — OCR crop + string-match
  verify_tables(pdf_path, page_1based, region_result) — expects rows where
    each column maps to {"value": "..."} dict
  MISMATCH: worker emits rows as {"cells": {"col": "value"}, "bbox": [...]}
    but verify_tables expects {"col": {"value": "value"}, "bbox": [...]}
  FIX: adapter normalizes rows before calling verify_tables

CONSUMER (existing, read-only)
  scripts/fold_strata.py
  Reads: burn output dirs (claim_data key)
  Projects to: {field, value, quote, source: {column_header, crop, page,
    row_header, sha, doc_sha256, source_pdf, provenance_tier}}
  Gap: projection loses unit, qualifier, condition, region_bbox, cell_bbox
  Fix: fold projection extended (minimal, backward-compatible)

  designwins/crates/dw-api/src/export.rs
  ExportStore walks parts_*.jsonl, resolves claims
  PartRecord: {part_number, vendor, claims: [{field, value, quote, source}]}
```

## Field mapping: vision-v2 → claim_data

| vision-v2 | claim_data | notes |
|-----------|-----------|-------|
| (derived from columns/rows) | symbol | column name or row label as field name |
| cells[col] | value | raw printed value string |
| cells[col] parsed | value (numeric) | numeric part of cell text |
| cells[col] parsed | unit | unit suffix (V, mA, °C, etc.) |
| table_title | condition | table title as context/condition |
| (from value parse) | qualifier | Min/Typ/Max from column header |
| (derived) | quote | the cell text itself (no fabrication) |
| columns[col] | column_header | original column label |
| (derived from row) | row_header | row label from cells |
| region.page | page | page number |
| sha256 | doc_sha256 | document SHA from producer |
| region_bbox | region_bbox | image-local pixel coords (200 DPI) |
| row.bbox | cell_bbox | row-level bbox for crop verify |
| extractor + version | extractor, extractor_version | provenance |
| crop_verdicts | verification | existing verification results |

## Coordinate system

- `region_bbox`: image-local pixels at 200 DPI (from pymupdf pixmap)
- `row.bbox`: row-level bbox within the region image
- `crop_verify.ocr_crop`: expects PDF-page coordinates
- **GAP**: pixel coords ≠ PDF coords. Adapter must transform or preserve
  both. For canary, we preserve the original coords and mark verification
  as UNKNOWN when transform is unavailable.

## Table classes supported in canary

1. **Parameter tables** (symbol + min/typ/max + unit) — unambiguous
2. **Pin tables** (pin number + name + description) — unambiguous
3. **Package dimensions** (DIM + INCHES + MILLIMETERS) — unambiguous

Unsupported (held as raw evidence with reason):
- Multi-page continuation tables
- Tables with merged cells or complex spans
- Tables where column headers are ambiguous (empty columns)
- Family documents where part applicability is uncertain

## Idempotency

Adapter output keyed by `{doc_sha256}|{page}|{region_idx}|{table_idx}|{row_idx}|{col_name}`.
Rerun produces identical output. Existing claims not overwritten.
