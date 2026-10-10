#!/usr/bin/env python3
"""Pillar extraction at corpus scale: universal comparison facets.

Runs the pillar_facts module over every datasheet in the queue. CPU-only,
deterministic, no vision. Output: one JSONL row per extracted fact, keyed
by source_path + pdf_sha, with page receipts.
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

from harness.electronics.pillar_facts import pillar_facts_from_document


def _scan(item: dict) -> dict:
    pdf = Path(item.get("source_path", ""))
    if not pdf.exists():
        return {"rows": [], "error": "missing_pdf"}
    try:
        doc = pymupdf.open(pdf)
        try:
            facts = pillar_facts_from_document(doc)
        finally:
            doc.close()
        sha = hashlib.sha256(pdf.read_bytes()).hexdigest()
        return {"rows": facts, "error": None, "sha": sha}
    except Exception as exc:
        return {"rows": [], "error": type(exc).__name__}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--queue", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    payload = json.loads(args.queue.read_text())
    docs = payload.get("documents") or payload.get("parts") or payload
    if args.limit:
        docs = docs[: args.limit]

    out_rows = []
    tally: Counter[str] = Counter()
    args.out.parent.mkdir(parents=True, exist_ok=True)

    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for item, result in zip(docs, pool.map(_scan, docs)):
            if result["error"]:
                tally[result["error"]] += 1
                continue
            sha = result.get("sha") or ""
            if result["rows"]:
                tally["docs_with_pillars"] += 1
            else:
                tally["docs_no_pillars"] += 1
            for fact in result["rows"]:
                fact["pdf_sha"] = sha
                fact["source_path"] = item.get("source_path")
                fact["verdict"] = "printed_row"
                out_rows.append(fact)
                tally[f"axis:{fact['field']}"] += 1

    args.out.write_text(
        "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in out_rows)
    )
    print(json.dumps({"documents": len(docs), "rows": len(out_rows), **dict(sorted(tally.items()))}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
