#!/usr/bin/env python3
"""errata-wave: deterministic errata delta records (T3, truth-relevant).

Reads an errata PDF queue (or a directory scan), emits one JSONL row per
numbered silicon issue plus one per data-sheet clarification:

    (record_kind, issue_id, title_verbatim, symptom_verbatim,
     workaround_verbatim, affected_silicon, correction_verbatim,
     document_sha256, source_path, pdf_sha, verdict)

Printed evidence only: affected revisions come from the printed X-matrix,
clarifications keep the correction text verbatim — that is the spec_delta
cr-core's truth audit consumes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harness.electronics.errata_deltas import (  # noqa: E402
    clarification_records,
    device_scope,
    errata_records,
)


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
                "source_url": item.get("source_url") or item.get("url"),
            }
        )
    return out


def extract_document(pdf: Path) -> list[dict]:
    doc = pymupdf.open(pdf)
    try:
        page_texts = [doc[index].get_text() for index in range(doc.page_count)]
        front = " ".join(" ".join(page_texts[:3]).split()).lower()
        is_ti = "advisories" in front and "errata" in front
        rows: list[dict] = []
        if is_ti:
            from harness.electronics.errata_deltas import (
                ti_advisory_details,
                ti_affected_matrix,
            )

            lines: list[str] = []
            for text in page_texts:
                for raw in text.splitlines():
                    line = " ".join(raw.split())
                    if line:
                        lines.append(line)
            rows = ti_advisory_details(lines)
            matrix = ti_affected_matrix(
                [doc[index].get_text("words") for index in range(doc.page_count)]
            )
            for row in rows:
                row["affected_silicon"] = {
                    "device": matrix.get(row["issue_id"], [])
                }
            # matrix-only advisories (no detail section) stay fill-only
            known = {row["issue_id"] for row in rows}
            for advisory, revisions in matrix.items():
                if advisory not in known:
                    rows.append(
                        {
                            "schema": row_schema(),
                            "record_kind": "issue",
                            "issue_id": advisory,
                            "title_verbatim": None,
                            "symptom_verbatim": None,
                            "workaround_verbatim": None,
                            "affected_silicon": {"device": revisions},
                        }
                    )
        else:
            rows = errata_records(page_texts) + clarification_records(page_texts)
        scope = device_scope(
            " ".join(page_texts[:2]), pdf.name
        )
        for row in rows:
            row["device_scope"] = scope
    finally:
        doc.close()
    for row in rows:
        row["verdict"] = "printed_row"
    return rows


def row_schema() -> str:
    from harness.electronics.errata_deltas import ERRATA_SCHEMA

    return ERRATA_SCHEMA


def _scan(item: dict) -> dict:
    pdf = Path(item["source_path"])
    try:
        rows = extract_document(pdf)
        return {"rows": rows, "error": None}
    except Exception as exc:
        return {"rows": [], "error": type(exc).__name__}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--queue", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--doc-timeout", type=float, default=60.0,
                    help="seconds per document before it is abandoned as hung")
    args = ap.parse_args()

    documents = _queue_documents(args.queue)
    if args.limit:
        documents = documents[: args.limit]
    out_rows = []
    tally: Counter[str] = Counter()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    # Stream rows to disk as they arrive so a later hang/fault cannot lose the
    # work already done (the lane previously held ~2GB and wrote once at the end).
    _fh = args.out.open("w")

    def _emit(row):
        _fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        _fh.flush()

    def _absorb(item, result):
        if result["error"]:
            tally[f"error:{result['error']}"] += 1
            return
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
            row["source_url"] = item["source_url"]
            _emit(row)
            out_rows.append(row)
            tally[row["record_kind"]] += 1
            if row.get("affected_silicon"):
                tally["with_affected_silicon"] += 1

    if args.workers > 1:
        from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
        from concurrent.futures import wait, FIRST_COMPLETED

        # pymupdf can hang forever in C on a malformed PDF (get_text never
        # returns). Under a plain pool.map (ordered, unbounded) ONE bad file
        # stalls the whole lane — the 6-hour stall of 2026-10-13. Instead: push
        # docs into a bounded pool and collect with a batch timeout; every doc
        # still pending after the timeout window is abandoned as hung and the
        # stuck pool is torn down (its workers cannot be reused).
        DOC_TIMEOUT = args.doc_timeout
        docs = list(documents)
        i = 0
        while i < len(docs):
            batch = docs[i:i + args.workers * 2]
            pool = ProcessPoolExecutor(max_workers=args.workers)
            futures = {pool.submit(_scan, item): item for item in batch}
            try:
                done, pending = wait(futures, timeout=DOC_TIMEOUT * 2)
                for fut in done:
                    _absorb(futures[fut], fut.result())
                for fut in pending:
                    fut.cancel()
                    tally["error:doc_timeout"] += 1
            finally:
                for p in list(getattr(pool, "_processes", {}).values()):
                    p.terminate()
                pool.shutdown(wait=False, cancel_futures=True)
            i += len(batch)
    else:
        for item in documents:
            pdf = Path(item["source_path"])
            if not pdf.exists():
                tally["missing_pdf"] += 1
                continue
            _absorb(item, _scan(item))
    _fh.close()
    print(json.dumps({"documents": len(documents), "rows": len(out_rows), **dict(sorted(tally.items()))}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
