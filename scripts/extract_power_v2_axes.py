#!/usr/bin/env python3
"""power-axes-v2 wave: vout_min/max + iq from held TI power PDFs.

Runs the v2 detector (harness/electronics/power_axes_v2.py) over the
ti-parametric-request queue using the cached ti-{part}.pdf corpus. The
original wave shipped gold-required axes only; these two axes were held
for this detector. Fill-only rows in the wave contract:

    (part_number, field, value, unit, role, page_1based, verbatim,
     condition_verbatim, table_kind, pdf_sha, source_url)

Specified-operation rows only — the Absolute Maximum Ratings table never
fills an axis (string-audited in the detector).
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

from harness.electronics.power_axes_v2 import (  # noqa: E402
    collapse_rows,
    front_vout_rows,
    iq_rows,
    vout_rows,
)


def _queue_parts(path: Path) -> list[dict]:
    payload = json.loads(path.read_text())
    parts = payload.get("parts") or payload.get("queue") or payload
    return [
        {
            "part_number": item["part_number"],
            "source_url": item.get("datasheet_url")
            or item.get("source_url")
            or f"https://www.ti.com/lit/ds/symlink/{str(item['part_number']).lower()}.pdf",
        }
        for item in parts
    ]


def _scan(item: dict, pdf_root: str) -> dict:
    pdf = Path(pdf_root) / f"ti-{item['part_number']}.pdf"
    if not pdf.exists():
        return {"rows": [], "error": "missing_pdf"}
    try:
        from harness.electronics.power_datasheet import read_characteristic_tables

        doc = pymupdf.open(pdf)
        try:
            facts = read_characteristic_tables(doc)
            front = [doc[index].get_text() for index in range(min(3, doc.page_count))]
        finally:
            doc.close()
        rows = vout_rows(facts) + iq_rows(facts) + front_vout_rows(front)
        winner = collapse_rows(rows)
        out = list(winner.values())
        return {"rows": out, "error": None, "found": sorted(winner)}
    except Exception as exc:
        return {"rows": [], "error": type(exc).__name__}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--queue", type=Path, required=True)
    ap.add_argument("--pdf-root", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    parts = _queue_parts(args.queue)
    if args.limit:
        parts = parts[: args.limit]
    out_rows = []
    tally: Counter[str] = Counter()
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for item, result in zip(
            parts,
            pool.map(_scan, parts, [str(args.pdf_root)] * len(parts)),
        ):
            if result["error"]:
                tally[result["error"]] += 1
                continue
            pdf = args.pdf_root / f"ti-{item['part_number']}.pdf"
            sha = hashlib.sha256(pdf.read_bytes()).hexdigest()
            if result["rows"]:
                tally["parts_with_rows"] += 1
            else:
                tally["parts_no_rows"] += 1
            for row in result["rows"]:
                row["part_number"] = item["part_number"]
                row["pdf_sha"] = sha
                row["source_url"] = item["source_url"]
                row["verdict"] = "printed_row"
                out_rows.append(row)
                tally[f"axis:{row['field']}"] += 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in out_rows)
    )
    print(json.dumps({"parts": len(parts), "rows": len(out_rows), **dict(sorted(tally.items()))}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
