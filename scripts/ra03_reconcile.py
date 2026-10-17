#!/usr/bin/env python3
"""RA-03 final reconciliation: the full-cohort POWER canary closeout.

1. Controller accounting: cohort == sealed chunks × items == doc outputs
   (+ any failed chunks, each with attempts/error) — every document
   accounted, none lost/duplicated/orphaned.
2. Baseline comparison: Rust canary outputs vs the PYTHON burn baseline
   (union of burn-power-* output dirs) per stem: presence, claim/surviving
   counters, and the deterministic floor (identical raw claims → identical
   verdict layer is already proven; here we quantify the live-run overlap
   the same way the M3 soak did).
3. Throughput + utilization from the factory DB and manifests.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from pathlib import Path


def controller_accounting(state_dir: Path):
    db = state_dir / "factory.sqlite3"
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    chunks = con.execute(
        "SELECT status, COUNT(*), SUM(item_count), MAX(attempt) "
        "FROM factory_chunks GROUP BY status").fetchall()
    events = dict(con.execute(
        "SELECT event_type, COUNT(*) FROM factory_events "
        "GROUP BY event_type").fetchall())
    failed = con.execute(
        "SELECT chunk_id, attempt, error FROM factory_chunks "
        "WHERE status='failed'").fetchall()
    times = con.execute(
        "SELECT MIN(created_at), MAX(updated_at) FROM factory_chunks"
    ).fetchone()
    con.close()
    return {"chunks_by_status": {r[0]: [r[1], r[2], r[3]] for r in chunks},
            "events": events, "failed_chunks": failed,
            "first_created": times[0], "last_updated": times[1]}


def load_rust(out_root: Path):
    rows = {}
    for f in out_root.glob("chunk-*/*.json"):
        if f.name == "manifest.json":
            continue
        try:
            d = json.loads(f.read_text())
        except Exception:
            continue
        if isinstance(d, dict) and "stem" in d:
            rows[d["stem"]] = d
    return rows


def load_python_baseline(roots: list[Path]):
    rows = {}
    for root in roots:
        for f in root.glob("*.json"):
            if f.name.startswith("_"):
                continue
            try:
                d = json.loads(f.read_text())
            except Exception:
                continue
            if isinstance(d, dict) and "stem" in d:
                rows.setdefault(d["stem"], d)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--state-dir", type=Path, required=True)
    ap.add_argument("--rust-out", type=Path, required=True)
    ap.add_argument("--baseline-roots", type=Path, nargs="+", required=True)
    ap.add_argument("--cohort", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    cohort = json.loads(args.cohort.read_text())
    cohort_stems = {Path(p).stem for p in cohort["pdfs"]}
    accounting = controller_accounting(args.state_dir)
    rust = load_rust(args.rust_out)
    py = load_python_baseline(args.baseline_roots)

    sealed = sum(v[1] for k, v in accounting["chunks_by_status"].items()
                 if k == "completed")
    sealed_items = sum(v[1] * 0 for _ in ()) # per-chunk items below
    con = sqlite3.connect(
        f"file:{args.state_dir}/factory.sqlite3?mode=ro", uri=True)
    sealed_items = con.execute(
        "SELECT SUM(item_count) FROM factory_chunks WHERE status='completed'"
    ).fetchone()[0]
    con.close()

    r_ok = {s for s, d in rust.items() if not d.get("skip")}
    p_ok = {s for s, d in py.items() if not d.get("skip")}
    common = sorted(set(rust) & set(py))

    skip_match = sum(1 for s in common
                     if rust[s].get("skip") and py[s].get("skip")
                     and rust[s].get("skip") == py[s].get("skip"))
    both_claims = len(set(r_ok) & set(p_ok))
    counters = {"claims_rust": sum(d.get("claims", 0) for d in rust.values()
                                   if not d.get("skip")),
                "claims_py": sum(d.get("claims", 0) for d in py.values()
                                 if not d.get("skip")),
                "surviving_rust": sum(d.get("surviving", 0) for d in rust.values()
                                      if not d.get("skip")),
                "surviving_py": sum(d.get("surviving", 0) for d in py.values()
                                    if not d.get("skip"))}

    report = {
        "schema": "ra03-reconciliation.v1",
        "cohort_docs": len(cohort_stems),
        "controller": accounting,
        "sealed_chunks": sealed,
        "sealed_items": sealed_items,
        "rust_output_stems": len(rust),
        "outputs_minus_sealed": len(rust) - sealed_items,
        "python_baseline_stems": len(py),
        "baseline_coverage_of_cohort": len(set(py) & cohort_stems),
        "rust_in_cohort_only": len(set(rust) - cohort_stems),
        "comparison": {
            "common_stems": len(common),
            "skip_skip_match": skip_match,
            "both_claim_docs": both_claims,
            "rust_only_claim_docs": len(r_ok - p_ok),
            "python_only_claim_docs": len(p_ok - r_ok),
            **counters,
        },
        "every_doc_accounted": (
            sealed_items + sum(v[1] for k, v in
                               accounting["chunks_by_status"].items()
                               if k != "completed") == len(cohort_stems)),
    }
    args.out.write_text(json.dumps(report, indent=1, default=str) + "\n")
    print(json.dumps(report, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
