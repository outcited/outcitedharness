#!/usr/bin/env python3
"""P1 connector parametrics burn over resolved held bytes.

Queue: /tmp/connector-p1-queue.json (manifest rows joined to local PDFs).
Per series: label-anchored line reads over the datasheet text, collapsed
to one best value per axis (printed evidence only; nulls stay null).
Output rows are staging-ready: series_id + vendor + mpns + axes + sha.
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

from harness.electronics.connector_parametrics import (
    collapse_series,
    connector_parametric_rows,
)

PAGE_CAP = 40


def _scan(item: dict) -> dict:
    pdf = Path(item.get("source_path") or "")
    if not pdf.exists():
        return {"error": "missing_pdf"}
    try:
        doc = pymupdf.open(pdf)
        try:
            texts = [doc[i].get_text() for i in range(min(PAGE_CAP, doc.page_count))]
            pages = doc.page_count
        finally:
            doc.close()
        rows = connector_parametric_rows(texts)
        collapsed = collapse_series(rows)
        sha = item.get("doc_sha256") or hashlib.sha256(pdf.read_bytes()).hexdigest()
        return {"error": None, "rows": rows, "collapsed": collapsed,
                "sha": sha, "pages": pages}
    except Exception as exc:
        return {"error": type(exc).__name__}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--queue", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    items = json.loads(args.queue.read_text())
    tally: Counter[str] = Counter()
    out_rows = []
    args.out.parent.mkdir(parents=True, exist_ok=True)

    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for item, result in zip(items, pool.map(_scan, items)):
            if result["error"]:
                tally[result["error"]] += 1
                continue
            axes = result["collapsed"]
            core = {k: v for k, v in axes.items()
                    if not k.endswith(("_verbatim", "_prints"))}
            filled = sum(1 for v in core.values() if v is not None)
            tally["series_read"] += 1
            tally[f"filled_{filled}"] += 1
            for axis in core:
                if core[axis] is not None:
                    tally[f"axis:{axis}"] += 1
            out_rows.append({
                "schema": "harness.electronics-connector-parametrics.v1",
                "series_id": item.get("series_id"),
                "vendor": item.get("vendor"),
                "series": item.get("series"),
                "mpns": item.get("mpns"),
                "axes": axes,
                "fields_filled": filled,
                "expected_fields": item.get("expected_fields"),
                "doc_sha256": result["sha"],
                "pdf_pages": result["pages"],
                "source_path": item.get("source_path"),
                "resolved_via": item.get("resolved_via"),
                "verdict": "printed_row" if filled else "no_axes_printed",
                "method": "deterministic_connector_parametrics_v1",
            })

    args.out.write_text(
        "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n"
                for r in out_rows)
    )
    print(json.dumps({"series": len(items), "rows": len(out_rows),
                      **dict(sorted(tally.items()))}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
