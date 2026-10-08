#!/usr/bin/env python3
"""aec-grade wave: cited AEC-Q100/AIS-Q100 grades from held PDFs (P1 ask).

Reads a document queue (source-cohort / prioritized-work shape), scans every
page for printed automotive-qualification claims, and emits fill-only rows:

    (standard_verbatim, grade, qualified_statement, context_verbatim,
     page_1based, document_sha256, source_path, pdf_sha, verdict)

Cited evidence only — a grade never derives from temperature ranges.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harness.electronics.aec_grades import grade_rows  # noqa: E402


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
            }
        )
    return out


def _scan(item: dict) -> dict:
    pdf = Path(item["source_path"])
    try:
        doc = pymupdf.open(pdf)
        try:
            page_texts = [doc[index].get_text() for index in range(doc.page_count)]
        finally:
            doc.close()
        rows = grade_rows(page_texts)
        return {"rows": rows, "error": None}
    except Exception as exc:
        return {"rows": [], "error": type(exc).__name__}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--queue", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    documents = _queue_documents(args.queue)
    if args.limit:
        documents = documents[: args.limit]
    out_rows = []
    tally: Counter[str] = Counter()
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for item, result in zip(documents, pool.map(_scan, documents)):
            if result["error"]:
                tally[f"error:{result['error']}"] += 1
                continue
            pdf = Path(item["source_path"])
            sha = item["document_sha256"] or hashlib.sha256(pdf.read_bytes()).hexdigest()
            if result["rows"]:
                tally["docs_with_rows"] += 1
            else:
                tally["docs_no_rows"] += 1
            for row in result["rows"]:
                row["document_sha256"] = sha
                row["pdf_sha"] = sha
                row["source_path"] = item["source_path"]
                row["part_number"] = item["part_number"]
                row["verdict"] = "printed_row"
                out_rows.append(row)
                tally[f"grade:{row['grade']}"] += 1
                if row["qualified_statement"]:
                    tally["qualified_statement"] += 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in out_rows)
    )
    print(json.dumps({"documents": len(documents), "rows": len(out_rows), **dict(sorted(tally.items()))}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
