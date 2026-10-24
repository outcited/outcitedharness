#!/usr/bin/env python3
"""Shared wave merge for facet runners.

Ledger law (2026-10-15):
  - done-<facet>.txt gets a doc ONLY if that facet's stage completed
    (ok-<facet> flag) AND the doc was verified deliverable this wave.
  - missing files -> missing-docs.log, never done.
  - a failed wave marks nothing done; docs accumulate attempts and park at 2.

usage: wave_merge.py STATE_DIR LANES_OUT_DIR --ok-facets thermal,mode \
       [--wave /tmp/wave-current.json] [--missing STATE/missing-wave.json] \
       [--box-root PATH --canon-root PATH]
"""
import argparse
import json
import os

ap = argparse.ArgumentParser()
ap.add_argument("state")
ap.add_argument("lanes_out")
ap.add_argument("--ok-facets", default="")
ap.add_argument("--wave", default="/tmp/wave-current.json")
ap.add_argument("--missing", default=None)
ap.add_argument("--box-root", default="")
ap.add_argument("--canon-root", default="")
a = argparse.parse_args()

ok_facets = [f for f in a.ok_facets.split(",") if f]
missing_p = a.missing or os.path.join(a.state, "missing-wave.json")
try:
    missing = set(json.load(open(missing_p)))
except Exception:
    missing = set()

# fold lane outputs into the merged, canonically-pathed accumulators
name_map = {"thermal": "thermal-mbp.jsonl", "mode": "mode-mbp.jsonl"}
for fn in sorted(os.listdir(a.lanes_out)):
    if not fn.endswith(".jsonl"):
        continue
    src = os.path.join(a.lanes_out, fn)
    facet = fn.split("-w")[0]
    dst = os.path.join(a.state, name_map.get(facet, f"{facet}-mbp.jsonl"))
    with open(dst, "a") as out:
        for l in open(src):
            try:
                r = json.loads(l)
            except Exception:
                continue
            sp = r.get("source_path") or ""
            if a.box_root:
                sp = sp.replace(a.box_root, a.canon_root)
                r["source_path"] = sp
            out.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
    os.remove(src)

wave = [p for p in json.load(open(a.wave)) if p not in missing]

if ok_facets:
    for f in ok_facets:
        with open(os.path.join(a.state, f"done-{f}.txt"), "a") as fh:
            for p in wave:
                fh.write(p + "\n")
    print(f"wave merged: done[{','.join(ok_facets)}]={len(wave)} missing={len(missing)}")
else:
    att_path = os.path.join(a.state, "attempts.json")
    att = json.load(open(att_path)) if os.path.exists(att_path) else {}
    parked_now = []
    for p in wave:
        att[p] = att.get(p, 0) + 1
        if att[p] >= 2:
            parked_now.append(p)
    json.dump(att, open(att_path, "w"))
    if parked_now:
        with open(os.path.join(a.state, "parked.txt"), "a") as fh:
            for p in parked_now:
                fh.write(p + "\n")
    print(f"wave FAILED: 0 done, {len(parked_now)} parked after 2 attempts")
