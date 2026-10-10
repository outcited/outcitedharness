#!/usr/bin/env python3
"""Reference-design burn: every L0-classified reference_design document
through refdesign_extract. Deterministic, CPU-only."""

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

from harness.electronics.refdesign_extract import refdesign_facts_from_text

PAGE_CAP = 40


def _queue() -> list[str]:
    paths = set()
    for f in glob.glob(str(ROOT / "results/l0-census-*/l0-records.jsonl")):
        for line in open(f):
            if '"reference_design"' not in line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("doc_class") == "reference_design" and r.get("source_path"):
                paths.add(r["source_path"])
    return sorted(paths)


def _scan(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return {"error": "missing_pdf", "source_path": path}
    try:
        doc = pymupdf.open(p)
        try:
            texts = [doc[i].get_text()
                     for i in range(min(PAGE_CAP, doc.page_count))]
            pages = doc.page_count
        finally:
            doc.close()
        res = refdesign_facts_from_text(texts)
        res["error"] = None
        res["source_path"] = path
        res["pdf_pages"] = pages
        res["pdf_sha"] = hashlib.sha256(p.read_bytes()).hexdigest()
        return res
    except Exception as exc:
        return {"error": type(exc).__name__, "source_path": path}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    paths = _queue()
    if args.limit:
        paths = paths[: args.limit]
    print(f"reference_design docs queued: {len(paths):,}")

    tally: Counter[str] = Counter()
    out_rows = []
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for res in pool.map(_scan, paths):
            if res["error"]:
                tally[res["error"]] += 1
                continue
            if res.get("design_id"):
                tally["with_design_id"] += 1
            if res.get("evaluates"):
                tally["with_evaluates"] += 1
            n = res["requirements_filled"]
            tally[f"req_{min(n, 4)}"] += 1
            for k in res["requirements"]:
                tally[f"axis:{k}"] += 1
            res["verdict"] = "extracted" if n or res.get("design_id") else "no_facts_found"
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
