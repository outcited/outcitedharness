#!/usr/bin/env python3
"""L0 census: every readable factory document through the substrate.

Reads the corpus list (JSON array of paths), emits one L0 record per doc
(what it is, where things are, which pages need eyes), then prints the
census: documents per class, lane coverage, vision-backlog size per class.
Deterministic, CPU-only, resumable by path.
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

from harness.electronics.l0 import l0_record  # noqa: E402


def _scan(path: str) -> dict | str:
    try:
        doc = pymupdf.open(path)
        try:
            record = l0_record(doc, source_path=path)
        finally:
            doc.close()
        return record
    except Exception as exc:
        return type(exc).__name__


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--corpus", type=Path, required=True, help="JSON array of pdf paths")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--summary", type=Path, required=True)
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    paths = json.loads(args.corpus.read_text())
    done: set[str] = set()
    if args.out.exists():
        for line in args.out.read_text().splitlines():
            try:
                done.add(json.loads(line).get("source_path"))
            except Exception:
                continue
    todo = [p for p in paths if p not in done]
    args.out.parent.mkdir(parents=True, exist_ok=True)

    class_counts: Counter[str] = Counter()
    vision_by_class: Counter[str] = Counter()
    ocr_by_class: Counter[str] = Counter()
    pages_by_class: Counter[str] = Counter()
    errors: Counter[str] = Counter()
    errors["resumed_skipped"] = len(done)

    sink = args.out.open("a")
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for path, result in zip(todo, pool.map(_scan, todo)):
            if isinstance(result, str):
                errors[f"error:{result}"] += 1
                continue
            sink.write(json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n")
            sink.flush()
            doc_class = result["doc_class"]
            class_counts[doc_class] += 1
            vision_by_class[doc_class] += result["vision_candidate_count"]
            ocr_by_class[doc_class] += result["needs_ocr_count"]
            pages_by_class[doc_class] += result["page_count"]
    sink.close()

    summary = {
        "documents": len(paths),
        "scanned_this_run": len(todo),
        "classes": {
            doc_class: {
                "docs": class_counts[doc_class],
                "pages": pages_by_class[doc_class],
                "vision_candidate_pages": vision_by_class[doc_class],
                "needs_ocr_pages": ocr_by_class[doc_class],
            }
            for doc_class in sorted(class_counts, key=class_counts.get, reverse=True)
        },
        "errors": dict(errors),
    }
    args.summary.write_text(json.dumps(summary, ensure_ascii=False, indent=1))
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
