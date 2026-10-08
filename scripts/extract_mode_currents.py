#!/usr/bin/env python3
"""power-modes wave: deterministic mode-current rows from MCU datasheets.

Reads a document queue (source-cohort or prioritized-work shape), opens each
PDF, scans tables on pages whose text carries power-mode words plus current
units, and emits fill-only rows:

    (part_number, mode, value, unit, value_role, page_1based, verbatim,
     conditions_verbatim, anchors, table_index, row_index, pdf_sha,
     source_url, verdict)

Values are printed evidence only — the mode must match the shared low-power
grammar, the value must be a printed current in the same row, and conditions
stay verbatim (typed VDD/f/TA anchors are selection aids, never replacements).
Competing (mode, role, vdd, freq) candidates collapse with TA = 25 C first,
the selector-gold rule of the TI parametrics wave.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harness.electronics.mode_currents import (  # noqa: E402
    collapse_competing,
    mode_current_rows,
)

MODE_PAGE_HINT = re.compile(
    r"power\s+(?:consumption|mode|current)|current\s+consumption|"
    r"low[- ]power|supply\s+current|"
    r"run|sleep|stop|standby|shutdown|hibernate|snooze|idle",
    re.I,
)
CURRENT_UNIT_HINT = re.compile(r"(?:nA|µA|uA|μA|mA)(?:\s*/\s*(?:MHz|kHz))?", re.I)


def _queue_documents(path: Path) -> list[dict]:
    payload = json.loads(path.read_text())
    if isinstance(payload, dict):
        items = payload.get("documents") or payload.get("work") or payload.get("parts")
    else:
        items = payload
    out = []
    for item in items or []:
        source = item.get("source_path") or item.get("pdf_path") or item.get("path")
        if not source:
            continue
        out.append(
            {
                "source_path": str(source),
                "document_sha256": item.get("document_sha256") or item.get("sha256"),
                "part_number": item.get("part_number")
                or (
                    item["exact_opns"][0]
                    if item.get("exact_opns") and len(item["exact_opns"]) == 1
                    else None
                ),
                "source_url": item.get("source_url") or item.get("url"),
            }
        )
    return out


def extract_document(pdf: Path) -> list[dict]:
    doc = pymupdf.open(pdf)
    rows: list[dict] = []
    try:
        for page_index in range(doc.page_count):
            page = doc[page_index]
            text = page.get_text()
            if not MODE_PAGE_HINT.search(text) or not CURRENT_UNIT_HINT.search(text):
                continue
            try:
                tables = page.find_tables().tables
            except Exception:  # pragma: no cover - PyMuPDF internals
                continue
            for table_index, table in enumerate(tables):
                try:
                    grid = table.extract()
                except Exception:  # pragma: no cover - PyMuPDF internals
                    continue
                rows.extend(
                    mode_current_rows(
                        {"table_index": table_index, "rows": grid},
                        document_sha256="",
                        page_1based=page_index + 1,
                        source_order=len(rows),
                    )
                )
    finally:
        doc.close()
    return collapse_competing(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--queue", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    documents = _queue_documents(args.queue)
    if args.limit:
        documents = documents[: args.limit]
    out_rows = []
    tally: Counter[str] = Counter()
    for item in documents:
        pdf = Path(item["source_path"])
        if not pdf.exists():
            tally["missing_pdf"] += 1
            continue
        try:
            rows = extract_document(pdf)
        except Exception as exc:
            tally[f"error:{type(exc).__name__}"] += 1
            continue
        sha = item["document_sha256"] or hashlib.sha256(pdf.read_bytes()).hexdigest()
        for row in rows:
            row.pop("_source_order", None)
            row["document_sha256"] = sha
            row["part_number"] = item["part_number"]
            row["pdf_sha"] = sha
            row["source_url"] = item["source_url"]
            row["verdict"] = "printed_row"
            out_rows.append(row)
            tally[f"mode:{row['mode'].lower()}"] += 1
        if rows:
            tally["docs_with_rows"] += 1
        else:
            tally["docs_no_rows"] += 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in out_rows)
    )
    print(json.dumps({"documents": len(documents), "rows": len(out_rows), **dict(sorted(tally.items()))}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
