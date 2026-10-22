#!/usr/bin/env python3
"""BCMP pilot summarizer — computes the morning receipt from the job dir.

Usage: bcmp_summarize.py JOB_DIR [--post]
"""
from __future__ import annotations

import glob
import json
import os
import sys
import subprocess
import time


def main() -> int:
    job = sys.argv[1]
    post = "--post" in sys.argv
    receipts = sorted(glob.glob(os.path.join(job, "receipts", "*.json")))
    tri = {"qualified": 0, "eliminated": 0, "insufficient_evidence": 0}
    brand_states: dict[str, dict[str, int]] = {}
    atom_axes: dict[str, int] = {}
    st_states: dict[str, int] = {}
    survivors_total = 0
    gap_axes: dict[str, int] = {}
    first_ts = None
    feed_pin = None
    engine_git = None
    for path in receipts:
        r = json.load(open(path))
        feed_pin = feed_pin or r["pins"]["feed_sha256"]
        engine_git = engine_git or r["pins"]["engine"]["git"]
        for a in r["canonical"]:
            atom_axes[a["axis"]] = atom_axes.get(a["axis"], 0) + 1
        survivors_total += len(r["survivors"])
        for b, out in r["brands"].items():
            tri[out["state"]] = tri.get(out["state"], 0) + 1
            brand_states.setdefault(b, {}).setdefault(out["state"], 0)
            brand_states[b][out["state"]] += 1
            if b == "st":
                st_states[out["state"]] = st_states.get(out["state"], 0) + 1
            for ax in out.get("unknown_axis_counts", {}):
                gap_axes[ax] = gap_axes.get(ax, 0) + out["unknown_axis_counts"][ax]

    state = json.load(open(os.path.join(job, "state.json")))
    parked = {}
    vp = os.path.join(job, "parked.log.jsonl")
    if os.path.exists(vp):
        for line in open(vp):
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            code = row.get("code", "?")
            parked[code] = parked.get(code, 0) + 1
    variants = 0
    vpath = os.path.join(job, "variants.jsonl")
    if os.path.exists(vpath):
        variants = sum(1 for _ in open(vpath))

    gen_files = sorted(glob.glob(os.path.join(job, "scenarios", "gen-*.jsonl")))
    n_tp4 = n_hand = 0
    for gf in gen_files + glob.glob(os.path.join(job, "scenarios", "canary-*.jsonl")):
        for line in open(gf):
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("generated_by", {}).get("source") == "tp4":
                n_tp4 += 1
            else:
                n_hand += 1

    st = os.stat(receipts[0]) if receipts else None
    en = os.stat(receipts[-1]) if receipts else None
    wall_min = ((en.st_mtime - st.st_mtime) / 60) if st and en else 0
    rate = (len(receipts) / wall_min * 60) if wall_min > 0.05 else float("nan")

    summary = {
        "schema": "harness.bcmp-summary.v1",
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "job_dir": job,
        "feed_pin": feed_pin,
        "engine_git": engine_git,
        "unique_atom_sets": len(receipts),
        "scenarios": {"hand": n_hand, "tp4": n_tp4, "variants_parked": variants},
        "atom_axis_usage": atom_axes,
        "brand_outcome_totals": tri,
        "st_outcomes": st_states,
        "per_brand_states": brand_states,
        "survivor_opn_instances": survivors_total,
        "missing_evidence_gap_axes": gap_axes,
        "parked_reasons": parked,
        "receipt_wall_minutes": round(wall_min, 1),
        "receipts_per_hour_first_to_last": round(rate, 1) if rate == rate else None,
    }
    out = os.path.join(job, "logs", "summary.json")
    with open(out, "w") as h:
        json.dump(summary, h, indent=1, sort_keys=True)
    print(json.dumps(summary, indent=1, sort_keys=True))

    if post:
        body = (
            "## BCMP-00 batch summary (auto-generated)\n\n```json\n"
            + json.dumps(summary, indent=1, sort_keys=True)
            + "\n```"
        )
        subprocess.run(
            [
                "gh",
                "issue",
                "comment",
                "29",
                "-R",
                "outcited/outcitedharness",
                "--body",
                body,
            ],
            check=False,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
