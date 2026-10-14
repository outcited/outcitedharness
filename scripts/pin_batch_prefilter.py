#!/usr/bin/env python3
"""Prefilter the full corpus for the pin-table batch: docs whose substrate text
carries pin-table signals (local regex, free). Only these go to the cloud batch.

Skips docs already processed by the TP4 lane (pin-rows.jsonl).

Usage:
    python3 scripts/pin_batch_prefilter.py \
        --queue /tmp/full-corpus-queue-pn.json \
        --done results/pin-tables-20261014/pin-rows.jsonl \
        --out results/pin-tables-20261014/pin-batch-queue.jsonl
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.extract_pin_tables_tp4 import PIN_SIGNAL, PIPELINE_DB  # noqa: E402

_tls = threading.local()


def _con():
    if getattr(_tls, "con", None) is None:
        _tls.con = sqlite3.connect(f"file:{PIPELINE_DB}?mode=ro", uri=True, timeout=60)
        _tls.con.row_factory = sqlite3.Row
    return _tls.con


def passes(sha: str) -> bool:
    row = _con().execute(
        "SELECT output FROM results WHERE corpus_key=? AND kind_hint='substrate'"
        " ORDER BY id DESC LIMIT 1", (f"pdf:{sha}",)).fetchone()
    if not row:
        return False
    try:
        rec = json.loads(row["output"])
    except Exception:
        return False
    for p in rec.get("pages") or []:
        if PIN_SIGNAL.search(p.get("text") or ""):
            return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--queue", type=Path, required=True)
    ap.add_argument("--done", type=Path, default=None)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    docs = json.loads(args.queue.read_text())
    docs = docs.get("documents") or docs.get("parts") or docs
    done = set()
    if args.done and args.done.exists():
        for l in args.done.read_text().splitlines():
            try:
                done.add(json.loads(l)["sha"])
            except Exception:
                pass
    docs = [d for d in docs if Path(d["source_path"]).stem not in done]
    print(f"queue: {len(docs)} docs (skipped {len(done)} done)", flush=True)

    t0 = time.time()
    hits = []
    n = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool, args.out.open("w") as fh:
        futs = {pool.submit(passes, Path(d["source_path"]).stem): d for d in docs}
        for fut in as_completed(futs):
            n += 1
            if fut.result():
                d = futs[fut]
                fh.write(json.dumps({"sha": Path(d["source_path"]).stem,
                                     "part_number": d.get("part_number"),
                                     "source_path": d["source_path"]},
                                    sort_keys=True) + "\n")
                hits.append(d)
            if n % 20000 == 0:
                fh.flush()
                print(f"  {n}/{len(docs)} scanned, {len(hits)} pass "
                      f"({time.time()-t0:.0f}s)", flush=True)
    print(json.dumps({"scanned": n, "pass_pin_signal": len(hits),
                      "seconds": round(time.time()-t0)}, indent=1), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())