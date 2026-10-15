#!/usr/bin/env python3
"""Exact comparator for frozen-response replay runs (RUST-CUTOVER-01).

Both engines consumed the SAME frozen model responses, so every byte of
per-stem output must agree: counters, tier, and the full claim_data arrays
(key-normalized, order-preserved as each engine emitted it). Any diff is a
deterministic-engine divergence — there is no model variance to blame.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def load(root: Path, rust: bool):
    rows = {}
    if rust:
        for f in root.glob("chunk-*/*.json"):
            if f.name == "manifest.json":
                continue
            d = json.loads(f.read_text())
            rows[d["stem"]] = d
    else:
        for f in root.glob("*.json"):
            d = json.loads(f.read_text())
            if "stem" in d:
                rows[d["stem"]] = d
    return rows


def diff_row(a, b, path, out):
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a:
                out.append(f"{path}.{k}: python-missing")
            elif k not in b:
                out.append(f"{path}.{k}: rust-missing")
            else:
                diff_row(a[k], b[k], f"{path}.{k}", out)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append(f"{path}: len python={len(a)} rust={len(b)}")
        for i, (x, y) in enumerate(zip(a, b)):
            diff_row(x, y, f"{path}[{i}]", out)
    elif a != b or type(a) is not type(b):
        out.append(f"{path}: python={a!r} rust={b!r}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rust-root", type=Path, required=True)
    ap.add_argument("--python-root", type=Path, required=True)
    args = ap.parse_args()

    rust = load(args.rust_root, rust=True)
    py = load(args.python_root, rust=False)
    common = sorted(set(rust) & set(py))
    print(json.dumps({"rust": len(rust), "python": len(py), "common": len(common)}))

    mismatches = {}
    for stem in common:
        diffs = []
        diff_row(py[stem], rust[stem], stem, diffs)
        if diffs:
            mismatches[stem] = diffs[:5]

    claims = {"python": 0, "rust": 0, "surviving": {"python": 0, "rust": 0}}
    for stem in common:
        for side, row in (("python", py[stem]), ("rust", rust[stem])):
            claims[side] += row.get("claims", 0)
            claims["surviving"][side] += row.get("surviving", 0)

    print(json.dumps({
        "identical_stems": len(common) - len(mismatches),
        "diverging_stems": len(mismatches),
        "claims_python": claims["python"],
        "claims_rust": claims["rust"],
        "surviving_python": claims["surviving"]["python"],
        "surviving_rust": claims["surviving"]["rust"],
        "divergence_examples": dict(list(mismatches.items())[:5]),
    }, indent=1, ensure_ascii=False))
    return 1 if mismatches else 0


if __name__ == "__main__":
    raise SystemExit(main())
