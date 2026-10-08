#!/usr/bin/env python3
"""Cloud vs local V4.1 on identical docs: agreement, divergence, quality.

Runs N docs through both proposers, diffs claims per doc:
  shared symbols w/ same value | same symbol different value |
  cloud-only | local-only | P0 rate per tier. The seam matters: the burst
is cloud and steady-state is local — if they diverge materially we need to
know before mixing their outputs into training data.
"""

import json
import re
import sys
import urllib.request
from collections import Counter
from pathlib import Path

sys.path.insert(0, "/Users/samkim/Harnessv1")
sys.path.insert(0, "/Users/samkim/Harnessv1/scripts")

from burn_aisle import (CLOUD_MODEL, OPENROUTER, STUDENT_PROMPT, V41,
                        call, load_substrate)

OUT = Path("/Volumes/M5_4TB/extract-results/cloud-vs-local-v1.json")


def extract(endpoint, model, text, part):
    return call(endpoint, model, [
        {"role": "system", "content": STUDENT_PROMPT},
        {"role": "user", "content": f"Part: {part}\n\nDatasheet pages:\n{text}"}])


def key_of(c):
    return (c.get("symbol") or "").lower()


def val_of(c):
    return c.get("value")


def diff_doc(a, b):
    ka = {key_of(c): c for c in a if isinstance(c, dict)}
    kb = {key_of(c): c for c in b if isinstance(c, dict)}
    same = diff = cloud_only = local_only = 0
    for k in set(ka) & set(kb):
        va, vb = val_of(ka[k]), val_of(kb[k])
        if va == vb or (isinstance(va, (int, float)) and isinstance(vb, (int, float))
                        and abs(float(va) - float(vb)) <= max(1e-9, abs(float(va)) * 1e-4)):
            same += 1
        else:
            diff += 1
    cloud_only = len(set(kb) - set(ka))
    local_only = len(set(ka) - set(kb))
    return same, diff, cloud_only, local_only


def main(n=100):
    stems = sorted(p.stem for p in
                   Path("/Volumes/M5_4TB/exports/power-datasheet-pairs").glob("*/pdf/*.pdf"))[:n]
    agg = Counter()
    per_doc = []
    for i, stem in enumerate(stems):
        sub = load_substrate(stem)
        if not sub:
            continue
        text = "\n".join(p.get("text", "") for p in sub.get("pages", []))[:28000]
        part = sub.get("filename", stem).rsplit(".", 1)[0]
        try:
            cloud = extract(OPENROUTER, CLOUD_MODEL, text, part) or []
        except Exception:
            cloud = []
        try:
            local = extract(V41, "deepseek-v4.1-flash", text, part) or []
        except Exception:
            local = []
        s, d, co, lo = diff_doc(local, cloud)
        agg["same_value"] += s
        agg["same_symbol_diff_value"] += d
        agg["cloud_only_symbols"] += co
        agg["local_only_symbols"] += lo
        agg["cloud_claims"] += len(cloud)
        agg["local_claims"] += len(local)
        per_doc.append({"stem": stem, "cloud_n": len(cloud), "local_n": len(local),
                        "same": s, "diff": d, "cloud_only": co, "local_only": lo})
        if (i + 1) % 10 == 0:
            print(f"{i+1}/{n} {dict(agg)}", flush=True)
    report = {
        "docs": len(per_doc), "aggregate": dict(agg),
        "value_agreement": round(agg["same_value"] /
                                 max(1, agg["same_value"] + agg["same_symbol_diff_value"]), 4),
        "coverage_ratio_cloud_over_local": round(
            agg["cloud_claims"] / max(1, agg["local_claims"]), 3),
        "per_doc": per_doc,
    }
    OUT.write_text(json.dumps(report, indent=2))
    print(json.dumps({k: report[k] for k in ("docs", "aggregate", "value_agreement",
                                             "coverage_ratio_cloud_over_local")}, indent=2))


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 100)
