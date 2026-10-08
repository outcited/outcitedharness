#!/usr/bin/env python3
"""Backfill the typed-condition dictionary (fill-only, idempotent).

Runs harness.electronics.condition_model.parse_condition over every unique
condition_verbatim string in the power burn claims and upserts one row per
string into catalog.db table conditions_typed. That table is a dictionary
(verbatim string -> typed parse), never a replacement for the verbatim:
catalog_fill and discovery queries join on it. Existing catalog tables are
untouched; re-running refreshes parses under the recorded parser version.

Scoreboard printed at the end: coverage by class, unique + row-weighted.
"""

from __future__ import annotations

import argparse
import collections
import glob
import json
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness.electronics.condition_model import (  # noqa: E402
    CONDITION_MODEL_SCHEMA,
    parse_condition,
)

# behavior version: bumped when parse behavior changes (case-insensitive
# units landed after v1; content re-verified drift-0 on 2026-10-08)
PARSER_VERSION = "condition_model.v1.1"

BURN = Path("/Volumes/M5_4TB/extract-results/burn-power-v1")
CATALOG = Path("/Volumes/M5_4TB/extract-results/catalog.db")

DDL = """
CREATE TABLE IF NOT EXISTS conditions_typed (
    condition_verbatim TEXT PRIMARY KEY,
    parsed TEXT NOT NULL,            -- parse_condition JSON or {"reject": ..}
    parser_version TEXT NOT NULL,
    schema TEXT NOT NULL,
    row_count INTEGER NOT NULL DEFAULT 1,
    updated_at REAL NOT NULL
);
"""


def collect(burn: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    for f in sorted(glob.glob(str(burn / "*.json"))):
        if Path(f).name.startswith("_"):
            continue
        try:
            d = json.load(open(f))
        except Exception:
            continue
        for c in d.get("claim_data") or []:
            cond = (c.get("condition") or "").strip()
            if cond:
                counts[cond] = counts.get(cond, 0) + 1
    return counts


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--burn", type=Path, default=BURN)
    ap.add_argument("--db", type=Path, default=CATALOG)
    args = ap.parse_args()

    counts = collect(args.burn)
    con = sqlite3.connect(args.db, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(DDL)

    now = time.time()
    by_class_rows = collections.Counter()
    for cond, n in counts.items():
        parsed = parse_condition(cond)
        if "reject" in parsed:
            cls = parsed["reject"]
        elif parsed["keys"]:
            cls = "typed"
        else:
            cls = "mode_only"
        by_class_rows[cls] += n
        con.execute(
            "INSERT INTO conditions_typed (condition_verbatim, parsed,"
            " parser_version, schema, row_count, updated_at)"
            " VALUES (?,?,?,?,?,?)"
            " ON CONFLICT(condition_verbatim) DO UPDATE SET"
            " parsed=excluded.parsed, parser_version=excluded.parser_version,"
            " row_count=excluded.row_count, updated_at=excluded.updated_at",
            (cond, json.dumps(parsed, ensure_ascii=False),
             PARSER_VERSION, CONDITION_MODEL_SCHEMA, n, now),
        )
    con.commit()

    total = sum(by_class_rows.values())
    unique = len(counts)
    print(f"conditions_typed: {unique} unique strings"
          f" ({total} condition-bearing rows) -> {args.db}")
    for cls in ("typed", "mode_only", "no_anchors", "units_absent",
                "dash", "ambiguous_symbols"):
        n = by_class_rows[cls]
        print(f"  {cls:20s} rows {n:6d} ({100 * n / max(total, 1):5.1f}%)")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
