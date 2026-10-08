#!/usr/bin/env python3
"""thermal-metrics wave: RθJA/RθJC/ΨJT/ΨJB/Zth from held power PDFs.

Deterministic read over the ti-{part}.pdf cache: every printed thermal
value with its unit (C/W or K/W) and condition verbatim. One collapsed
winner per metric per part (JEDEC-referenced and typ rows win). Fill-only;
a mention without a printed value never emits.
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

from harness.electronics.thermal_metrics import (  # noqa: E402
    collapse_thermal,
    thermal_rows,
)


def _queue_parts(path: Path) -> list[dict]:
    payload = json.loads(path.read_text())
    parts = payload.get("parts") or payload.get("queue") or payload.get("documents") or payload
    out = []
    for item in parts:
        part = item.get("part_number")
        source = item.get("source_path")
        if part:
            out.append({"part_number": part,
                        "source_url": item.get("datasheet_url") or item.get("source_url")})
        elif source:
            stem = Path(source).stem
            out.append({"part_number": stem[3:] if stem.startswith("ti-") else stem,
                        "source_url": item.get("source_url")})
    return out


def _scan(item: dict, pdf_root: str) -> dict:
    pdf = Path(pdf_root) / f"ti-{item['part_number']}.pdf"
    if not pdf.exists():
        return {"rows": [], "error": "missing_pdf"}
    try:
        from harness.electronics.power_datasheet import read_characteristic_tables

        doc = pymupdf.open(pdf)
        try:
            facts = read_characteristic_tables(doc)
        finally:
            doc.close()
        winner = collapse_thermal(thermal_rows(facts))
        return {"rows": list(winner.values()), "error": None}
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
            parts, pool.map(_scan, parts, [str(args.pdf_root)] * len(parts))
        ):
            if result["error"]:
                tally[result["error"]] += 1
                continue
            if result["rows"]:
                tally["parts_with_rows"] += 1
            else:
                tally["parts_no_rows"] += 1
            pdf = args.pdf_root / f"ti-{item['part_number']}.pdf"
            sha = hashlib.sha256(pdf.read_bytes()).hexdigest()
            for row in result["rows"]:
                row["part_number"] = item["part_number"]
                row["pdf_sha"] = sha
                row["source_url"] = item["source_url"]
                row["verdict"] = "printed_row"
                out_rows.append(row)
                tally[f"metric:{row['metric']}"] += 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in out_rows)
    )
    print(json.dumps({"parts": len(parts), "rows": len(out_rows), **dict(sorted(tally.items()))}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
