#!/usr/bin/env python3
"""Sweep the deterministic word-columns extractor across the corpus.

Reads the sealed page index (profiles.jsonl), runs
harness.electronics.word_columns.extract_pin_columns on every
pin_or_ball lane page of every document, and emits an immutable
extraction receipt with word-level coordinate provenance for every
extracted row, plus per-vendor coverage statistics.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SWEEP_SCHEMA = "harness.datasheet-word-columns-sweep.v1"


def _vendor(source_path: str) -> str:
    stem = Path(source_path).stem
    return stem.split("_", 1)[0] if "_" in stem else stem


def _compact_columns(columns: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, column in columns.items():
        out[key] = {
            "header": column.header,
            "header_bbox": [
                round(v, 2) for v in column.header_span.bbox
            ],
            "rows": [
                [
                    row.pin_no,
                    row.name,
                    [round(v, 2) for v in row.identifier_span.bbox],
                    [round(v, 2) for v in row.name_span.bbox],
                ]
                for row in column.rows
            ],
        }
    return out


def _process_document(record: dict[str, Any]) -> dict[str, Any]:
    from harness.electronics.word_columns import extract_pin_columns

    source = Path(record["source_path"])
    pages = sorted(
        {
            int(page)
            for page in (record.get("lane_pages") or {}).get("pin_or_ball")
            or []
        }
    )
    result: dict[str, Any] = {
        "document_sha256": record["document_sha256"],
        "source_path": record["source_path"],
        "vendor": _vendor(record["source_path"]),
        "pages": {},
        "errors": {},
    }
    for page in pages:
        try:
            columns = extract_pin_columns(source, page)
            result["pages"][str(page)] = _compact_columns(columns)
        except Exception as error:
            result["errors"][str(page)] = f"{type(error).__name__}: {error}"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--page-index", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--limit-documents", type=int, default=0)
    args = parser.parse_args()

    records = [
        json.loads(line)
        for line in args.page_index.read_text().splitlines()
        if line.strip()
    ]
    records = [
        r
        for r in records
        if (r.get("lane_pages") or {}).get("pin_or_ball")
        and r.get("source_path")
        and Path(r["source_path"]).exists()
    ]
    if args.limit_documents:
        records = records[: args.limit_documents]
    print(f"sweeping {len(records)} documents", flush=True)

    output = args.output_directory.expanduser().resolve()
    if output.exists() or output.is_symlink():
        raise SystemExit(f"immutable output already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))

    doc_stats: Counter[str] = Counter()
    vendor_pages: dict[str, Counter[str]] = {}
    total_pages = 0
    pages_with_rows = 0
    total_rows = 0
    total_columns = 0
    extraction_path = temporary / "extractions.jsonl"
    started = datetime.now(timezone.utc)

    import multiprocessing

    with extraction_path.open("wb") as handle:
        if args.workers > 1:
            pool = multiprocessing.Pool(args.workers)
            iterator = pool.imap_unordered(
                _process_document, records, chunksize=4
            )
        else:
            iterator = map(_process_document, records)
        done = 0
        for result in iterator:
            handle.write(
                json.dumps(result, ensure_ascii=False, sort_keys=True).encode()
                + b"\n"
            )
            done += 1
            vendor = result["vendor"]
            bucket = vendor_pages.setdefault(vendor, Counter())
            for page, columns in result["pages"].items():
                total_pages += 1
                bucket["pages"] += 1
                row_count = sum(
                    len(column["rows"]) for column in columns.values()
                )
                if row_count:
                    pages_with_rows += 1
                    bucket["pages_with_rows"] += 1
                    total_rows += row_count
                    total_columns += len(columns)
                    bucket["rows"] += row_count
            for page, error in result["errors"].items():
                doc_stats[f"error:{error.split(':', 1)[0]}"] += 1
            if result["errors"]:
                doc_stats["documents_with_errors"] += 1
            doc_stats["documents_with_rows"] += bool(
                any(
                    any(c["rows"] for c in pages.values())
                    for pages in result["pages"].values()
                )
            )
            if done % 250 == 0:
                handle.flush()
                print(
                    f"processed {done}/{len(records)} docs, "
                    f"{total_pages} pages, {total_rows} rows",
                    flush=True,
                )
        if args.workers > 1:
            pool.close()
            pool.join()

    vendor_coverage = {
        vendor: {
            "pages": counter["pages"],
            "pages_with_rows": counter["pages_with_rows"],
            "rows": counter["rows"],
            "coverage": (
                round(counter["pages_with_rows"] / counter["pages"], 4)
                if counter["pages"]
                else None
            ),
        }
        for vendor, counter in sorted(vendor_pages.items())
    }
    receipt = {
        "schema": SWEEP_SCHEMA,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "started_at": started.isoformat(),
        "page_index": str(args.page_index.resolve()),
        "documents": len(records),
        "summary": {
            "pin_pages_attempted": total_pages,
            "pages_with_rows": pages_with_rows,
            "page_coverage": (
                round(pages_with_rows / total_pages, 4) if total_pages else None
            ),
            "columns": total_columns,
            "rows": total_rows,
            "documents_with_rows": doc_stats["documents_with_rows"],
            "documents_with_errors": doc_stats["documents_with_errors"],
        },
        "vendor_coverage": vendor_coverage,
        "error_counts": {
            k: v for k, v in doc_stats.items() if k.startswith("error:")
        },
    }
    handle_stat = extraction_path.stat()
    receipt["artifacts"] = {
        "extractions.jsonl": {
            "bytes": handle_stat.st_size,
            "sha256": __import__("hashlib").sha256(
                extraction_path.read_bytes()
            ).hexdigest(),
        }
    }
    (temporary / "manifest.json").write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n"
    )
    os.chmod(extraction_path, 0o444)
    os.chmod(temporary / "manifest.json", 0o444)
    os.rename(temporary, output)
    print(json.dumps(receipt["summary"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
