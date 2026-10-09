#!/usr/bin/env python3
"""Prime the DISCOVERY-01 policy-keyed release cache (B5).

Why: ``units.release_view`` used to fall back to the single-row ``meta``
release cache, which stores ONE policy identity. The index carries
``hybrid-v1.1(...)`` while DISCOVERY-01 serves ``discovery01-v1(...)``, so
every session request re-fingerprinted all ~443k unit rows (~2.9 s),
including session-cache hits, and the served discovery release was never
recorded anywhere.

This script performs the one-time writable-path priming that the read-only
serving path cannot do itself: it computes the discovery-policy fingerprint
once and persists it as an immutable, policy-keyed ``release_cache`` row
plus an append-only ``releases`` ledger entry. It touches no unit, no
vector, and no ``meta`` row.

Safety evidence recorded before and after: unit counts, the full ``meta``
dump, ledger row counts, and the served release id — which MUST be
identical before and after (priming changes lookup cost, never identity).
Invalidation afterwards is automatic: every unit/vector write marks the
row dirty (units._mark_release_dirty), and re-running this script
re-primes.

Usage:
  .venv/bin/python scripts/prime_discovery_release.py [--dry-run]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harness.search import orchestrator, units as units_store  # noqa: E402

OUT = ROOT / "results/discovery01-remediation"
DEFAULT_DB = os.environ.get(
    "SEARCH_DB", "/Volumes/M5_4TB/extract-results/search_index.db")


def _ro(path):
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _snapshot(path, policy):
    """Read-only view of everything priming must not change, plus the
    (expensive) fingerprint the serving path would otherwise pay for."""
    con = _ro(path)
    try:
        t0 = time.time()
        view = units_store.release_view(con, policy_identity=policy)
        cost = round((time.time() - t0) * 1000, 1)
        meta = {r["key"]: r["value"] for r in con.execute(
            "SELECT key, value FROM meta ORDER BY key")}
        try:
            cache_rows = [dict(r) for r in con.execute(
                "SELECT policy_identity, release, dirty, unit_count,"
                " active_count FROM release_cache ORDER BY policy_identity")]
        except sqlite3.OperationalError:
            cache_rows = "table absent"
        return {
            "units_total": con.execute(
                "SELECT count(*) FROM units").fetchone()[0],
            "units_active": con.execute(
                "SELECT count(*) FROM units WHERE retired=0").fetchone()[0],
            "vectors": con.execute(
                "SELECT count(*) FROM vectors").fetchone()[0],
            "releases_ledger_rows": con.execute(
                "SELECT count(*) FROM releases").fetchone()[0],
            "meta": meta,
            "meta_sha256": hashlib.sha256(
                json.dumps(meta, sort_keys=True).encode()).hexdigest(),
            "release_cache_rows": cache_rows,
            "discovery_release": view["release"],
            "lookup_cost_ms": cost,
            "release_cache_state": view.get("release_cache"),
        }
    finally:
        con.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--policy", default=orchestrator.DISCOVERY_POLICY)
    ap.add_argument("--dry-run", action="store_true",
                    help="measure and report without persisting")
    args = ap.parse_args()

    if not Path(args.db).exists():
        print(f"index not found: {args.db}", file=sys.stderr)
        return 2

    before = _snapshot(args.db, args.policy)

    primed = None
    if not args.dry_run:
        con = units_store.connect(args.db)      # writable; runs the schema
        try:
            t0 = time.time()
            primed = units_store.policy_release(
                con, args.policy, force=True, persist=True)
            primed["prime_cost_ms"] = round((time.time() - t0) * 1000, 1)
        finally:
            con.close()

    after = _snapshot(args.db, args.policy)

    unchanged = {
        "units_total": before["units_total"] == after["units_total"],
        "units_active": before["units_active"] == after["units_active"],
        "vectors": before["vectors"] == after["vectors"],
        "meta_sha256": before["meta_sha256"] == after["meta_sha256"],
        "discovery_release": before["discovery_release"] ==
        after["discovery_release"],
    }
    ledger_delta = after["releases_ledger_rows"] - \
        before["releases_ledger_rows"]
    report = {
        "operation": "prime_discovery_release_cache",
        "db": args.db,
        "policy_identity": args.policy,
        "dry_run": args.dry_run,
        "before": before,
        "after": after,
        "primed": primed,
        "invariants_held": unchanged,
        "releases_ledger_rows_added": ledger_delta,
        "steady_state_lookup_cost_ms": after["lookup_cost_ms"],
        "verdict": "PRIMED" if (not args.dry_run and all(unchanged.values())
                                and after["release_cache_state"] == "hit")
        else "DRY_RUN" if args.dry_run else "FAILED",
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "b5_release_priming.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str) + "\n")
    print(json.dumps({
        "verdict": report["verdict"],
        "discovery_release": after["discovery_release"],
        "before_lookup_cost_ms": before["lookup_cost_ms"],
        "after_lookup_cost_ms": after["lookup_cost_ms"],
        "prime_cost_ms": (primed or {}).get("prime_cost_ms"),
        "invariants_held": unchanged,
        "releases_ledger_rows_added": ledger_delta,
    }, indent=1))
    return 0 if report["verdict"] in ("PRIMED", "DRY_RUN") else 1


if __name__ == "__main__":
    raise SystemExit(main())
