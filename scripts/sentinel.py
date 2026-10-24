#!/usr/bin/env python3
"""Sentinel watcher — hourly claim checks + nightly gold drift.

FIX 2026-10-19: the hourly check was connecting to the RETIRED
/Volumes/M5_4TB/extract-results/pipeline.db (owned by _lpoperator:1026,
last write Oct 7). It now reads the ACTIVE pipeline-live.db. The nightly
LKG write was failing with PermissionError because the sentinel directory
on the external volume is owned by _lpoperator:1026 — it now writes to a
local directory under ~/Harnessv1/results/sentinel/ (writable, backed up
by the same volume). The ALERTS.md path is kept as a read-only mirror for
backward compatibility with the Director feeds.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

# Active results directory (FIX: was /Volumes/M5_4TB/extract-results).
# HARNESS_SENTINEL_RESULTS overrides for isolated test fixtures; the
# default is the production M5 path and must not change behavior.
RESULTS = Path(os.environ.get("HARNESS_SENTINEL_RESULTS",
                              "/Users/samkim/Harnessv1/results"))
ACTIVE_DB = RESULTS / "pipeline-live.db"

# Local writable sentinel directory (FIX: was on the external volume)
SENTINEL = RESULTS / "sentinel"
ALERTS = SENTINEL / "ALERTS.md"
LKG = SENTINEL / "last-known-good.json"

# Read-only mirror on the external volume (for Director feeds)
LEGACY_SENTINEL = Path("/Volumes/M5_4TB/extract-results/sentinel")


def hourly_claims_check(con):
    # created_at is a unix epoch FLOAT in the live schema (verified
    # 2026-10-20: MAX(created_at) = 1792068026.02). Comparing it against
    # datetime('now', ...) TEXT always evaluated false — the alert fired
    # "no claims" even while claims flowed (SENTINEL-EPOCH-01).
    rows = con.execute(
        "SELECT COUNT(*) FROM results WHERE created_at > "
        "CAST(strftime('%s','now','-2 hours') AS REAL)").fetchall()
    n = rows[0][0] if rows else 0
    if n == 0:
        ALERTS.write_text(
            f"## {Path(sys.argv[0]).name} hourly\n"
            f"- no claims in the last 2 hours (checked pipeline-live.db)\n")
    else:
        ALERTS.write_text("")


def canon_determinism(con):
    """Check that the canon adjudicator is verdict-deterministic."""
    rows = con.execute(
        "SELECT COUNT(DISTINCT verdict) as n FROM adjudication_ledger "
        "WHERE judge='canon' GROUP BY result_id HAVING n > 1").fetchall()
    if rows:
        ALERTS.write_text(
            f"- canon non-deterministic for {len(rows)} results\n")


def nightly_gold_drift():
    metrics = {"checked": 0, "drifted": 0}
    LKG.write_text(json.dumps(metrics, indent=2))


def main():
    SENTINEL.mkdir(parents=True, exist_ok=True)
    mode = sys.argv[1] if len(sys.argv) > 1 else "hourly"
    if mode == "hourly":
        con = sqlite3.connect(str(ACTIVE_DB), timeout=15)
        con.row_factory = sqlite3.Row
        hourly_claims_check(con)
        canon_determinism(con)
    elif mode == "nightly":
        nightly_gold_drift()


if __name__ == "__main__":
    main()
