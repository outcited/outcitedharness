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

from harness.pipeline.extraction_health import ExtractionFailures  # noqa: E402

from harness.electronics.power_axes_v2 import (  # noqa: E402
    bounded_axis_rows,
    collapse_rows,
    front_vout_rows,
    iq_rows,
    vout_rows,
)


def _queue_parts(path: Path) -> list[dict]:
    payload = json.loads(path.read_text())
    if isinstance(payload, list):
        parts = payload
    else:
        parts = (payload.get("parts") or payload.get("queue")
                 or payload.get("documents") or payload)
    out = []
    for item in parts:
        if isinstance(item, str):
            # vault-path queues carry plain path strings (the 2026-10-14
            # fullcorpus crash: string items hit dict indexing)
            stem = Path(item).stem
            out.append({
                "part_number": stem[:96],
                "source_path": item,
                "source_url": None,
            })
            continue
        if not isinstance(item, dict):
            continue
            # derive part_number from the path when the queue omits it
        pn = item.get("part_number")
        sp = item.get("source_path")
        if not pn and sp:
            pn = Path(str(sp)).stem[:96]
        if not pn:
            continue
        out.append(
            {
                "part_number": pn,
                "source_path": sp,
                "source_url": item.get("datasheet_url")
                or item.get("source_url")
                or f"https://www.ti.com/lit/ds/symlink/{str(pn).lower()}.pdf",
            }
        )
    return out


def _scan(item: dict, pdf_root: str) -> dict:
    direct = item.get("source_path")
    data = None
    if direct and (str(direct).startswith("http://") or str(direct).startswith("https://")):
        pdf = None
    else:
        pdf = Path(direct) if direct and Path(direct).exists() else Path(pdf_root) / f"ti-{item['part_number']}.pdf"
        if not pdf.exists():
            return {"rows": [], "error": "missing_pdf"}
    try:
        from harness.electronics.power_datasheet import read_characteristic_tables

        if pdf is None:
            from pdf_fetch import open_pdf, fetch_bytes

            data = fetch_bytes(direct)
            doc = pymupdf.open(stream=data, filetype="pdf")
        else:
            doc = pymupdf.open(pdf)
        try:
            failures = ExtractionFailures()
            facts = read_characteristic_tables(doc, failures=failures)
            if failures:
                print(f"EXTRACTION FAILURES: {failures.summary()}", file=sys.stderr, flush=True)
            front = [doc[index].get_text() for index in range(min(3, doc.page_count))]
        finally:
            doc.close()
        sha = hashlib.sha256(data).hexdigest() if data is not None else ""
        rows = (vout_rows(facts) + iq_rows(facts) + front_vout_rows(front)
                + bounded_axis_rows(facts))
        winner = collapse_rows(rows)
        out = list(winner.values())
        return {"rows": out, "error": None, "found": sorted(winner), "sha": sha}
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
    out_rows = 0
    tally: Counter[str] = Counter()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w") as sink, ProcessPoolExecutor(max_workers=args.workers) as pool:
        for done, (item, result) in enumerate(
            zip(parts, pool.map(_scan, parts, [str(args.pdf_root)] * len(parts))),
            start=1,
        ):
            if result["error"]:
                tally[result["error"]] += 1
            else:
                # sha comes from the worker (it already holds the bytes); the old
                # parent-side re-read of pdf_root/ti-{part}.pdf crashes on boxes
                # without that layout and loses the whole run at shutdown
                sha = result.get("sha", "")
                if result["rows"]:
                    tally["parts_with_rows"] += 1
                else:
                    tally["parts_no_rows"] += 1
                for row in result["rows"]:
                    row["part_number"] = item["part_number"]
                    row["pdf_sha"] = sha
                    row["source_url"] = item["source_url"]
                    row["verdict"] = "printed_row"
                    sink.write(json.dumps(row, ensure_ascii=True, sort_keys=True) + "\n")
                    out_rows += 1
                    tally[f"axis:{row['field']}"] += 1
            if done % 2000 == 0:
                sink.flush()
                print(json.dumps({"progress": done, "total": len(parts),
                                  "rows": out_rows}), flush=True)
    print(json.dumps({"parts": len(parts), "rows": out_rows, **dict(sorted(tally.items()))}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
