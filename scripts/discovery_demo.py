#!/usr/bin/env python3
"""Discovery acceptance demo: the spec section 27/28 example, real catalog.

Design: 24 V industrial rail (18-36 V) -> switch must block >= 36 V,
carry >= 5 A continuous, junction-rated >= 125 C. Runs the Phase-1
evaluator over catalog.db claims and prints the elimination ledger:
cohorts, per-verdict evidence, and the constraint-driver statistic.
Deterministic, read-only, no model calls.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness.discovery.evaluator import Atom, evaluate_design  # noqa: E402

DB = "/Volumes/M5_4TB/extract-results/catalog.db"

ATOMS = [
    Atom(axis="vds_rating_v", value=36.0, hard=True),
    Atom(axis="id_continuous_a", value=5.0, hard=True,
         required_condition=None),
    Atom(axis="tj_max_c", value=125.0, hard=True),
]


def main() -> int:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    claims_by_part: dict[str, list[dict]] = defaultdict(list)
    for row in con.execute(
            "SELECT opn, symbol, qualifier, condition_norm, value, unit,"
            " provenance FROM claims_canonical"):
        claims_by_part[row["opn"]].append({
            "symbol": row["symbol"], "qualifier": row["qualifier"],
            "condition": row["condition_norm"], "value": row["value"],
            "unit": row["unit"],
            "provenance": json.loads(row["provenance"]),
        })
    con.close()
    print(f"population: {len(claims_by_part)} parts, "
          f"{sum(len(v) for v in claims_by_part.values())} claims\n")

    result = evaluate_design(ATOMS, claims_by_part)

    print("cohorts:")
    for name, parts in result["cohorts"].items():
        print(f"  {name:11s} {len(parts):5d}")
    print("\nconstraint driver (the spec's killer feature):")
    for axis, stat in sorted(result["constraint_driver"].items(),
                             key=lambda kv: -kv[1]["eliminates_pct"]):
        print(f"  {axis:18s} eliminates {stat['eliminates_pct']:5.1f}% "
              f"({stat['failed_parts']} parts)")

    print("\nwhy is this here / why isn't that (spec section 52):")
    sample_part = result["cohorts"]["passing"][0]
    nm = result["cohorts"]["near_miss"][0] if result["cohorts"]["near_miss"] else None
    el = result["cohorts"]["eliminated"][0] if result["cohorts"]["eliminated"] else None
    for label, part in (("PASS", sample_part), ("NEAR_MISS", nm), ("FAIL", el)):
        if not part:
            continue
        print(f"  [{label}] {part}")
        for axis, v in result["ledger"][part].items():
            ev = v.evidence
            quote = (ev.get("provenance") or {}).get("quote", "")
            print(f"      {axis:18s} {v.verdict:9s} {v.reason:20s}"
                  f" val={ev.get('claim', {}).get('value')}"
                  f" quote={str(quote)[:24]!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
