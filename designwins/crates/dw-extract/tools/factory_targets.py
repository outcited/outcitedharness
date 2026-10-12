#!/usr/bin/env python3
"""The volume loop's guidance layer: inventory -> factory target lists.

Sam's mandate (2026-10-12): "as many as we have" — find the waiting PDFs,
guide the factory to grab/ process around MPN or category, extract ->
normalize -> push to the API.

The logic that pushes (coverage gain, in order):
  1. CATEGORIES WITH ZERO EXTRACTION and big inventory first — every doc we
     process adds a whole new claim family (the corpus is 41,501 capacitors
     deep and we hold none of it).
  2. Categories the product sells (mcu drill-down) next — depth where the
     front end already walks.
  3. The machine's gaps: docs whose likely symbols fill the unbound
     top-20 knives (VGS(TH), IDSS, QG, CISS...), and the connector stratum
     (EMPTY in the gold — the one named hole).

Outputs (api-store-export/):
  factory_targets.json   inventory summary + process_priorities + fetch_targets

Usage: python3 designwins/crates/dw-extract/tools/factory_targets.py
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

INV = Path("/Users/samkim/Volumes/Electronicdataassets/m5-4tb/exports/corpus-inventory/brand-category-inventory-2026-10-05.csv")
DELIVERIES = Path("/Users/samkim/Volumes/Electronicdataassets/m5-4tb/exports/corpus-deliveries")
EXPORT = Path("/Users/samkim/mnt/yosemiteraid5-nfs/m5-opencode/api-store-export")

# What our extract currently HOLDS, by category (measured 2026-10-12):
#   power/semiconductor  1,882 parts / 86,736 claims  (burn lane)
#   mcu (family docs)      538 docs / 23,835 grid claims
#   connector               0 pin claims (parametric only)
HELD = {"power_management": 1882, "mosfet": 1882, "microcontroller": 538, "mcu": 538,
        "connector": 0, "connector(s)": 0, "diode": 0, "capacitor": 0, "resistor": 0}


def main() -> int:
    inv = []
    with INV.open() as fh:
        for row in csv.DictReader(fh):
            row["assets"] = int(row["assets"])
            inv.append(row)
    total = sum(r["assets"] for r in inv)
    by_cat: dict[str, int] = {}
    for r in inv:
        by_cat[r["category"]] = by_cat.get(r["category"], 0) + r["assets"]

    # delivery inventory: manifest rows per delivery lane
    deliveries = {}
    for d in sorted(DELIVERIES.iterdir()):
        man = d / "corpus-delivery-manifest.jsonl"
        if man.exists():
            with man.open() as fh:
                deliveries[d.name] = sum(1 for _ in fh)

    # PROCESS priorities: held coverage per category vs inventory
    process = []
    for cat, assets in sorted(by_cat.items(), key=lambda kv: -kv[1]):
        held = HELD.get(cat, 0)
        gap = assets - held
        if gap <= 0:
            continue
        tier = 1 if held == 0 else (2 if held < 1000 else 3)
        process.append({"category": cat, "inventory_assets": assets, "held": held,
                        "gap": gap, "priority_tier": tier,
                        "why": "zero extraction — every doc adds a new claim family"
                        if held == 0 else ("thin coverage" if held < 1000 else "depth")})
    process.sort(key=lambda x: (x["priority_tier"], -x["gap"]))

    # FETCH targets: the named holes -> cr_requests-style target rows
    fetch = [
        {"target": "connector stratum", "why": "gold class EMPTY; parametric-only today",
         "fetch_logic": "connector datasheet URLs from brand-category inventory rows with category=connector*", "est_docs": by_cat.get("connector", 0) + by_cat.get("connector(s)", 0)},
        {"target": "unbound-knife docs", "why": "VGS(TH)/IDSS/QG/CISS families concentrate in power MOSFET + diode datasheets",
         "fetch_logic": "MPN clusters whose vendor pages print the top-20 unbound symbols", "est_docs": by_cat.get("diode", 0) + by_cat.get("mosfet", 0)},
        {"target": "series lines (411 nodes)", "why": "released_parts=0 on 411 series nodes — the sellable lines",
         "fetch_logic": "the 411 source_urls in series_content_crawl_targets (crawler lane)", "est_docs": 411},
    ]

    out = {
        "mandate": "Sam 2026-10-12: pump datasheet volume — grab -> extract -> normalize -> push",
        "inventory": {
            "brand_category_rows": len(inv),
            "total_assets": total,
            "deliveries": deliveries,
            "top10_categories": [{"category": c, "assets": a} for c, a in sorted(by_cat.items(), key=lambda kv: -kv[1])[:10]],
        },
        "held_today": HELD,
        "process_priorities": process,
        "fetch_targets": fetch,
    }
    (EXPORT / "factory_targets.json").write_text(json.dumps(out, indent=1, sort_keys=True))
    print(f"inventory: {total} assets across {len(inv)} brand-category rows")
    print("deliveries:", deliveries)
    print("top categories:", [(x['category'], x['assets']) for x in out['inventory']['top10_categories'][:6]])
    print("process priorities (tier 1 = zero-held):")
    for p in process[:8]:
        print(f"  t{p['priority_tier']} {p['category']}: inventory {p['inventory_assets']} / held {p['held']} / gap {p['gap']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
