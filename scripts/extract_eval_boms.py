#!/usr/bin/env python3
"""BOM burn: every L0-classified bom document through bom_extract.

Queue source: L0 census records with doc_class=bom (all census runs).
Output: one JSONL row per document with design_id, board, items, sha.
Deterministic, CPU-only.
"""

from __future__ import annotations

import argparse
import glob
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

from harness.electronics.bom_extract import bom_from_document


def _queue() -> list[str]:
    paths = set()
    for f in glob.glob(str(ROOT / "results/l0-census-*/l0-records.jsonl")):
        for line in open(f):
            if '"bom"' not in line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("doc_class") == "bom" and r.get("source_path"):
                paths.add(r["source_path"])
    return sorted(paths)


def _scan(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return {"error": "missing_pdf", "path": path}
    try:
        doc = pymupdf.open(p)
        try:
            res = bom_from_document(doc)
        finally:
            doc.close()
        res["error"] = None
        res["source_path"] = path
        res["pdf_sha"] = hashlib.sha256(p.read_bytes()).hexdigest()
        return res
    except Exception as exc:
        return {"error": type(exc).__name__, "path": path}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    paths = _queue()
    if args.limit:
        paths = paths[: args.limit]
    print(f"bom docs queued: {len(paths):,}")

    tally: Counter[str] = Counter()
    out_rows = []
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for res in pool.map(_scan, paths):
            if res["error"]:
                tally[res["error"]] += 1
                continue
            n = res["item_count"]
            tally["parsed" if n else "zero_items"] += 1
            tally["items_total"] += n
            if res.get("design_id"):
                tally["with_design_id"] += 1
            res["verdict"] = "extracted" if n else "no_bom_table_found"
            out_rows.append(res)

    args.out.write_text(
        "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n"
                for r in out_rows)
    )
    print(json.dumps({"docs": len(paths), "rows": len(out_rows),
                      **dict(sorted(tally.items()))}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
