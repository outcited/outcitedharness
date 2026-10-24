#!/usr/bin/env python3
"""S5 — comparable_accepted scorer: count comparable part-pairs per axis.

Reads normalized + identity-bound rows, groups by (part_identity, axis),
and counts pairs of parts that share at least one normalized facet axis
with values on both sides. This is the number the board serves.

usage: python3 comparable_score.py --rows normalized/*.jsonl --out comparable.json
"""
from __future__ import annotations
import argparse, json, sys
from collections import Counter, defaultdict
from pathlib import Path

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rows", nargs="+", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    # axis → part → canonical_value
    axis_parts: dict[str, dict[str, float]] = defaultdict(dict)
    all_parts: set[str] = set()
    
    for rp in args.rows:
        for line in open(rp, errors="replace"):
            try: row = json.loads(line)
            except Exception: continue
            # only verified + identity-bound + normalized rows count
            v = (row.get("_verify") or {}).get("verdict")
            if v != "verified": continue
            part = row.get("part_identity")
            if not part: continue
            canon = (row.get("_norm") or {}).get("canonical_value")
            if canon is None: continue
            all_parts.add(part)
            # determine the axis
            axis = row.get("metric") or row.get("field") or row.get("mode") or "unknown"
            # for mode rows, the axis is sleep_current + the canonical mode
            if row.get("mode"):
                mode_canon = (row.get("_norm") or {}).get("mode_canon") or row["mode"]
                axis = f"mode:{mode_canon}"
            # keep the first value per (axis, part) — could be improved to best-per-role
            if part not in axis_parts[axis]:
                axis_parts[axis][part] = canon

    # count comparable pairs per axis
    axis_pairs: dict[str, int] = {}
    axis_parts_count: dict[str, int] = {}
    for axis, parts in sorted(axis_parts.items()):
        n = len(parts)
        axis_parts_count[axis] = n
        if n >= 2:
            # count all pairs (n choose 2) — parts sharing this axis
            axis_pairs[axis] = n * (n - 1) // 2

    total_pairs = sum(axis_pairs.values())
    result = {
        "schema": "harness.comparable-accepted.v1",
        "unique_parts": len(all_parts),
        "axes_with_data": len(axis_parts),
        "axes_with_pairs": len(axis_pairs),
        "total_comparable_pairs": total_pairs,
        "by_axis": {
            axis: {"parts": axis_parts_count[axis], "pairs": axis_pairs.get(axis, 0)}
            for axis in sorted(axis_parts, key=lambda a: -axis_pairs.get(a, 0))
        },
    }
    Path(args.out).write_text(json.dumps(result, indent=1))
    print(json.dumps({
        "parts": len(all_parts),
        "axes": len(axis_parts),
        "comparable_pairs": total_pairs,
        "top_axes": list(result["by_axis"].keys())[:5],
    }, indent=1))
    return 0

if __name__ == "__main__":
    sys.exit(main())
