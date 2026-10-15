#!/usr/bin/env python3
"""Parity analyzer: dw-soak (Rust) vs burn_aisle (Python) per-stem outputs.

Compares the Rust pilot/full outputs against the Python soak's per-stem
JSON rows on the same cohort:

- skip stems: must match exactly (same substrate resolution + model lane
  emptiness class)
- claim counts / surviving / p0 / supported / tier: reported per stem;
  mismatches counted, examples listed
- determinism floor: for stems whose RAW claim arrays are byte-identical
  (same model output), the verdict layer (surviving/p0/supported/page/
  doc_sha) must be IDENTICAL — any diff there is a Rust verify bug, not
  model noise
- page attribution coverage on surviving claims, both sides
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def load_rows(out_root: Path):
    rows = {}
    for chunk_dir in sorted(out_root.glob("chunk-*")):
        for f in chunk_dir.glob("*.json"):
            if f.name == "manifest.json":
                continue
            d = json.loads(f.read_text())
            rows[d["stem"]] = d
    return rows


def claim_signature(row):
    """The model-lane output before deterministic verify."""
    data = row.get("claim_data") or []
    raw = [(c.get("symbol"), c.get("value"), c.get("quote")) for c in data]
    return (row.get("tier"), row.get("claims"), json.dumps(raw, sort_keys=True))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rust-root", type=Path, required=True)
    ap.add_argument("--python-root", type=Path, required=True)
    args = ap.parse_args()

    rust = load_rows(args.rust_root)
    py = load_rows(args.python_root)
    common = sorted(set(rust) & set(py))
    print(json.dumps({
        "rust_stems": len(rust),
        "python_stems": len(py),
        "common": len(common),
    }))

    skips_match = skips_diff = 0
    counters_mismatch = []
    same_raw = raw_diff = 0
    verdict_mismatch = []
    page_null_rust = page_null_py = page_total = 0
    sha_mismatch = 0

    for stem in common:
        r, p = rust[stem], py[stem]
        r_skip, p_skip = r.get("skip"), p.get("skip")
        if r_skip or p_skip:
            if r_skip == p_skip:
                skips_match += 1
            else:
                skips_diff += 1
                counters_mismatch.append(
                    {"stem": stem, "kind": "skip", "rust": r_skip, "python": p_skip})
            continue
        if claim_signature(r) == claim_signature(p):
            same_raw += 1
            for key in ("claims", "surviving", "p0", "supported"):
                if r.get(key) != p.get(key):
                    verdict_mismatch.append({
                        "stem": stem, "kind": key,
                        "rust": r.get(key), "python": p.get(key)})
            for rc, pc in zip(r["claim_data"], p["claim_data"]):
                page_total += 1
                if rc.get("page") is None:
                    page_null_rust += 1
                if pc.get("page") is None:
                    page_null_py += 1
                if rc.get("doc_sha256") != pc.get("doc_sha256"):
                    sha_mismatch += 1
        else:
            raw_diff += 1
            counters_mismatch.append({
                "stem": stem, "kind": "raw_claims",
                "rust": {k: r.get(k) for k in ("tier", "claims", "surviving", "p0")},
                "python": {k: p.get(k) for k in ("tier", "claims", "surviving", "p0")},
            })

    print(json.dumps({
        "skips_match": skips_match,
        "skips_diff": skips_diff,
        "raw_identical_stems": same_raw,
        "raw_differing_stems": raw_diff,
        "verdict_mismatches_on_identical_raw": len(verdict_mismatch),
        "doc_sha_mismatches": sha_mismatch,
        "page_attribution": {
            "surviving_claims_compared": page_total,
            "page_null_rust": page_null_rust,
            "page_null_python": page_null_py,
        },
        "counter_mismatch_examples": counters_mismatch[:10],
        "verdict_mismatch_examples": verdict_mismatch[:10],
    }, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
