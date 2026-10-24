#!/usr/bin/env python3
"""Shared wave selector for facet runners (MBP / M5 / asus2 / tomorrow's boxes).

A doc is "todo" when at least one of the runner's facets is not yet done for
it and it is not parked. The slice (--mode front|tail|middle) keeps multiple
boxes working different regions of the queue without double-processing.

usage: wave_select.py STATE_DIR WAVE_N --mode front --facets thermal,mode \
       [--paths /tmp/vault-paths.json] [--out /tmp/wave-current.json]
"""
import argparse
import json
import os

ap = argparse.ArgumentParser()
ap.add_argument("state")
ap.add_argument("wave_n", type=int)
ap.add_argument("--mode", choices=["front", "tail", "middle"], required=True)
ap.add_argument("--facets", required=True, help="comma-separated facet names")
ap.add_argument("--paths", default="/tmp/vault-paths.json")
ap.add_argument("--out", default="/tmp/wave-current.json")
a = ap.parse_args()

paths = json.load(open(a.paths))
facets = [f for f in a.facets.split(",") if f]
done = {}
for f in facets:
    p = os.path.join(a.state, f"done-{f}.txt")
    done[f] = set(l.strip() for l in open(p)) if os.path.exists(p) else set()
parked_p = os.path.join(a.state, "parked.txt")
parked = set(l.strip() for l in open(parked_p)) if os.path.exists(parked_p) else set()

todo = [p for p in paths if p not in parked and any(p not in done[f] for f in facets)]

if a.mode == "front":
    sel = todo[: a.wave_n]
elif a.mode == "tail":
    sel = todo[-a.wave_n:]
else:
    half = a.wave_n // 2
    mid = len(todo) // 2
    sel = todo[max(0, mid - half): mid + half] or todo[: a.wave_n]

json.dump(sel, open(a.out, "w"))
print(len(todo), len(sel))
