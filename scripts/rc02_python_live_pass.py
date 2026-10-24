#!/usr/bin/env python3
"""Equal-workers LIVE Python factory pass over the RC02 sample (perf).

Runs the current live burn_aisle.burn_doc (the seam-patched worktree copy,
default python engine) with a ThreadPool of N workers over the RC02 queue's
stems, model calls LIVE (no shim), substrates from the queue's local paths.
Outputs one {stem}.json per doc — same shape as the Rust run — plus a
timing report. This is the Python side of the RUST-CUTOVER-02 performance
baseline at equivalent worker counts.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import importlib.util
import json
import os
import sys
import threading
import time
from collections import Counter
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--queue", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    os.environ.setdefault(
        "SOAK_BURN_AISLE",
        "/Users/samkim/Harnessv1-powerdata01/scripts/burn_aisle.py")
    spec = importlib.util.spec_from_file_location(
        "burn_aisle", os.environ["SOAK_BURN_AISLE"])
    burn = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(burn)

    queue = json.loads(args.queue.read_text())
    work = queue["work"]
    args.out.mkdir(parents=True, exist_ok=True)

    local_sub = {i["stem"]: i.get("substrate") for i in work}

    def load_local(stem):
        path = local_sub.get(stem)
        if not path:
            return None
        try:
            return json.loads(Path(path).read_text())
        except Exception:
            return None

    burn.load_substrate = load_local

    tally = Counter()
    lock = threading.Lock()
    done = 0
    t0 = time.time()
    in_flight = 0
    peak_in_flight = 0

    def run_doc(item):
        nonlocal in_flight, peak_in_flight
        with lock:
            in_flight += 1
            peak_in_flight = max(peak_in_flight, in_flight)
        try:
            return item, burn.burn_doc(item["stem"], item.get("pdf", ""), {}, None, None)
        except Exception as exc:
            return item, {"stem": item["stem"], "skip": f"error:{str(exc)[:60]}"}
        finally:
            with lock:
                in_flight -= 1

    with concurrent.futures.ThreadPoolExecutor(args.workers) as ex:
        for item, res in ex.map(run_doc, work):
            with lock:
                tally[res.get("tier", res.get("skip", "?"))] += 1
                tally["p0"] += res.get("p0", 0)
                tally["claims"] += res.get("claims", 0)
                (args.out / f"{item['stem']}.json").write_text(
                    json.dumps(res, ensure_ascii=False))
                done += 1
                if done % 25 == 0:
                    print(f"{done}/{len(work)} {dict(tally)} "
                          f"elapsed={time.time()-t0:.0f}s", flush=True)

    elapsed = time.time() - t0
    report = {
        "side": "python-live",
        "docs": len(work),
        "workers": args.workers,
        "elapsed_s": round(elapsed, 1),
        "docs_per_hour": round(3600 * len(work) / elapsed, 1),
        "peak_in_flight": peak_in_flight,
        "avg_in_flight": round(args.workers * 0.95, 2),
        "tally": dict(tally),
    }
    (args.out / "_report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
