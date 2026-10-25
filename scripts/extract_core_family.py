#!/usr/bin/env python3
"""core-family wave: core_family + core_bits from printed page text.

Deterministic read over held PDFs. Zero model calls. Every claim carries the
document sha, the 1-based page, the exact printed line (verbatim) and the
matched span. A family is emitted only when the text layer names it; a
datasheet that never prints its core stays Unknown (no inference from part
numbers, no off-family benchmark sentences).

Two axes, one pass:
  field=core_family  cortex-m0/m0+/m3/m4/m7/m23/m33/m55/m85, avr, pic,
                     risc-v, xtensa, msp430 (as printed/measured)
  field=core_bits    8/16/32 when printed adjacent to the core claim

Queue wiring is the thermal lane's contract (m5_wave_runner.sh):

  {"documents": [{"source_path": "/Volumes/M5_4TB/vault/cas/.../ti-X.pdf",
                  "part_number": "X"}]}

  python3 scripts/extract_core_family.py --queue /tmp/m5-queue.json \
      --pdf-root /Volumes/M5_4TB --out "$OUT/core-family-w.jsonl" --workers 12

The stdout summary reports the measured stamp rate for the queue
(docs_with_claims / docs_scanned), directly comparable to identity_stamp's
83.5% corpus baseline. Read the whole document text; page 1 usually wins but
feature blocks on later pages are evidence too.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, wait
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harness.electronics.core_family import (  # noqa: E402
    collapse_core_family_rows,
    core_family_rows,
)


def _queue_parts(path: Path) -> list[dict]:
    payload = json.loads(path.read_text())
    parts = payload.get("parts") or payload.get("queue") or payload.get("documents") or payload
    out = []
    for item in parts:
        part = item.get("part_number")
        source = item.get("source_path")
        if part:
            out.append(
                {
                    "part_number": part,
                    "source_url": item.get("datasheet_url") or item.get("source_url"),
                    "source_path": source,
                }
            )
        elif source:
            url = item.get("source_url")
            if url:
                stem = Path(url.split("?")[0]).stem
            else:
                stem = Path(source).stem
            out.append(
                {
                    "part_number": stem[3:] if stem.startswith("ti-") else stem,
                    "source_url": url,
                    "source_path": source,
                }
            )
    return out


def _scan(item: dict, pdf_root: str) -> dict:
    direct = item.get("source_path")
    pdf = Path(direct) if direct else Path(pdf_root) / f"ti-{item['part_number']}.pdf"
    if not pdf.exists():
        return {"rows": [], "error": "missing_pdf"}
    try:
        data = pdf.read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        doc = pymupdf.open(stream=data, filetype="pdf")
        try:
            pages = [(index + 1, doc[index].get_text()) for index in range(doc.page_count)]
        finally:
            doc.close()
        rows = core_family_rows(
            pages, sha256=sha, part_number=item["part_number"]
        )
        return {
            "rows": list(collapse_core_family_rows(rows).values()),
            "error": None,
        }
    except Exception as exc:
        return {"rows": [], "error": type(exc).__name__}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--queue", type=Path, required=True)
    ap.add_argument("--pdf-root", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument(
        "--doc-timeout",
        type=float,
        default=120.0,
        help="seconds per doc batch before hung docs are abandoned",
    )
    args = ap.parse_args()

    parts = _queue_parts(args.queue)
    if args.limit:
        parts = parts[: args.limit]
    tally: Counter[str] = Counter()
    rows_out = 0
    families: Counter[str] = Counter()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    fh = args.out.open("w")
    items = list(zip(parts, [str(args.pdf_root)] * len(parts)))
    i = 0
    while i < len(items):
        batch = items[i : i + args.workers * 2]
        pool = ProcessPoolExecutor(max_workers=args.workers)
        futures = {pool.submit(_scan, item, root): item for item, root in batch}
        try:
            done, pending = wait(futures, timeout=args.doc_timeout * 2)
            for fut in done:
                item = futures[fut]
                result = fut.result()
                tally["docs"] += 1
                if result["error"]:
                    tally[f"error:{result['error']}"] += 1
                    continue
                if result["rows"]:
                    tally["docs_with_claims"] += 1
                else:
                    tally["docs_no_claims"] += 1
                for row in result["rows"]:
                    row["source_url"] = item.get("source_url")
                    row["verdict"] = "printed_row"
                    fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
                    rows_out += 1
                    families[f"{row['field']}={row['value']}"] += 1
            for fut in pending:
                fut.cancel()
                tally["error:doc_timeout"] += 1
        finally:
            for proc in list(getattr(pool, "_processes", {}).values()):
                proc.terminate()
            pool.shutdown(wait=False, cancel_futures=True)
        if (i // (args.workers * 2)) % 50 == 0:
            fh.flush()
        i += len(batch)
    fh.close()

    docs_scanned = tally["docs_with_claims"] + tally["docs_no_claims"]
    summary = {
        "docs": tally["docs"],
        "docs_scanned": docs_scanned,
        "docs_with_claims": tally["docs_with_claims"],
        "stamp_rate": round(tally["docs_with_claims"] / docs_scanned, 4) if docs_scanned else None,
        "rows": rows_out,
        "baseline_identity_stamp": 0.835,
    }
    errors = {key: value for key, value in sorted(tally.items()) if key.startswith("error:")}
    if errors:
        summary["errors"] = errors
    summary["claims"] = dict(sorted(families.items()))
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
