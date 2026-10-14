#!/usr/bin/env python3
"""E4 — stamps recomputed from measured coverage (T-N7).

PLAN spec E4: "Feed the universe map's measured coverage into observe_coverage
-> Stamp::from_coverage ... extraction growth is what mechanically flips
stamps to ProvenFit. This is how 'extract everything' becomes 'recommendation
machine' without human judgment in the loop."

Input:  api-store-export/attribute_universe.json (G5 — the 3-of-N brand map)
Output: api-store-export/stamps.json — one row per tracked attribute:
        {attribute, fraction, stamp, brands_printing, brands_absent, why}

The law (phrase.rs, ported exactly — this is an adapter, not a second law):
  fraction = brands_printing / brands_total   (measured, never authored)
  stamp    = ProvenFit            if fraction >= 0.5 (ACTIVATION_FLOOR)
             Blocked(reason)      otherwise
  a stamp can never exceed its measured fraction (T-N7 negative)

Usage: python3 designwins/crates/dw-extract/tools/stamps_from_universe.py
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

EXPORT = Path("/Users/samkim/mnt/yosemiteraid5-nfs/m5-opencode/api-store-export")
ACTIVATION_FLOOR = 0.5  # phrase.rs — duplicated as a ported constant, gated by T-N7


def stamp_from_coverage(fraction: float, reason: str) -> str:
    """phrase.rs `Stamp::from_coverage` — the mechanical flip, nothing else."""
    if fraction >= ACTIVATION_FLOOR:
        return "ProvenFit"
    return f"Blocked({reason})"


def main() -> int:
    universe = json.loads((EXPORT / "attribute_universe.json").read_text())
    brands_total = int(universe.get("brands_total") or 5)
    rows = []
    tally: Counter[str] = Counter()

    for entry in universe.get("universe", []):
        printing = int(entry.get("brands_printing") or 0)
        fraction = printing / brands_total if brands_total else 0.0
        absent = entry.get("brands_absent") or []
        reason = (f"{printing}/{brands_total} brands; silent: {','.join(map(str, absent[:5]))}"
                  if absent else f"{printing}/{brands_total} brands")
        stamp = stamp_from_coverage(fraction, reason)
        tally[stamp.split("(")[0]] += 1
        rows.append({
            "attribute": entry.get("attribute"),
            "fraction": round(fraction, 4),
            "stamp": stamp,
            "brands_printing": printing,
            "brands_total": brands_total,
            "brands_absent": absent,
            "why": "measured from attribute_universe.json (T-N7: coverage only)",
        })

    out = {
        "schema": "stamps.v1",
        "law": "Stamp::from_coverage — fraction >= 0.5 -> ProvenFit, else Blocked; "
               "stamps recomputed from measured coverage only (T-N7)",
        "activation_floor": ACTIVATION_FLOOR,
        "brands_total": brands_total,
        "attributes": len(rows),
        "stamp_counts": dict(tally.most_common()),
        "rows": rows,
    }
    (EXPORT / "stamps.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(f"attributes: {len(rows)}  floor: {ACTIVATION_FLOOR}")
    for k, v in tally.most_common():
        print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
