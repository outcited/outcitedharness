#!/usr/bin/env python3
"""The cross-brand attribute universe — the "3 of 20" map. The loop's feedback edge.

Sam (2026-10-12): "if you normalize everything to itself nothing is
normalized." A quantity only 3 of 20 brands print must (a) unify those 3
into one knife, (b) record the 17 silent brands as ABSENCE (data, not
error), and (c) hold the slot open for when the others print it or print
something similar. This tool builds exactly that map from what we hold:

  per attribute (norm_symbol cluster + label-key cluster):
    brands_printing   who speaks the quantity (and their spellings/units)
    brands_absent     who does NOT — the watchlist ("when they add it")
    variants          symbol spellings + row-header label variants (the
                      cross-brand normalization table: RDS(ON) vs
                      "Drain-Source On-Resistance" vs "RDS(on)@10V")
    units_seen        unit spellings that must fold to one (mOhm/Ohm/mW-glyph)

This map is simultaneously: the normalization authority (alias table), the
factory's targeting feed (extract more where a brand prints variants we
haven't folded, or where an important brand is silent on a knife its
competitors print), and the compare engine's equivalence proof.

Output: api-store-export/attribute_universe.json
Usage: python3 designwins/crates/dw-extract/tools/attribute_universe.py
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

CATALOG = Path("/Users/samkim/mnt/yosemiteraid5-nfs/m5-opencode/scratch/catalog.db")
EXPORT = Path("/Users/samkim/mnt/yosemiteraid5-nfs/m5-opencode/api-store-export")


def norm_symbol(s: str) -> str:
    return "".join(c for c in (s or "") if not c.isspace() and c not in "(),").upper()


def label_key(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def main() -> int:
    db = sqlite3.connect(f"file:{CATALOG}?mode=ro&immutable=1", uri=True)
    vendors = {}
    for opn, vendor in db.execute("SELECT opn, vendor FROM parts"):
        vendors[opn] = vendor or "unknown"
    all_brands = sorted(set(vendors.values()))

    # cluster rows by norm_symbol; capture spellings/labels/units per brand
    cl = defaultdict(lambda: {"brands": defaultdict(lambda: {"symbols": Counter(), "labels": Counter(), "units": Counter(), "claims": 0}),
                              "claims": 0, "parts": set()})
    for opn, symbol, row_header, unit, qualifier in db.execute(
        "SELECT opn, symbol, provenance, unit, qualifier FROM claims_canonical".replace(
            "provenance", "json_extract(provenance,'$.row_header')")
    ):
        key = norm_symbol(symbol)
        e = cl[key]
        brand = vendors.get(opn, "unknown")
        b = e["brands"][brand]
        b["symbols"][symbol or "?"] += 1
        b["labels"][label_key(row_header or "")] += 1
        b["units"][unit or ""] += 1
        b["claims"] += 1
        e["claims"] += 1
        e["parts"].add(opn)

    universe = []
    for key, e in cl.items():
        brands = e["brands"]
        printing = sorted(brands)
        absent = [b for b in all_brands if b not in brands]
        variants = {
            "symbols": sorted({s for b in brands.values() for s in b["symbols"]}),
            "labels": sorted({l for b in brands.values() for l in b["labels"] if l})[:12],
            "units": sorted({u for b in brands.values() for u in b["units"] if u}),
        }
        universe.append({
            "attribute": key,
            "claims": e["claims"],
            "parts": len(e["parts"]),
            "brands_printing": len(printing),
            "brands_total": len(all_brands),
            "coverage": f"{len(printing)}/{len(all_brands)} brands",
            "brands": {b: {"claims": brands[b]["claims"],
                           "symbols": dict(brands[b]["symbols"]),
                           "units": dict(brands[b]["units"])} for b in printing},
            "brands_absent": absent,
            "variants": variants,
        })
    universe.sort(key=lambda x: (-x["claims"], x["attribute"]))

    # The watchlist: brands silent on a knife that >=3 brands print — the
    # "when they add it, it lands in the same slot" list.
    watch = [
        {"attribute": u["attribute"], "coverage": u["coverage"],
         "absent_count": len(u["brands_absent"]),
         "why": "printed by peers; silent brands must snap into the same slot when they print it or a variant"}
        for u in universe
        if u["brands_printing"] >= 3 and u["brands_absent"]
    ]
    watch.sort(key=lambda x: -x["absent_count"])

    # Variant pressure: attributes whose spellings/labels/units are messy —
    # the normalization curation queue (alias tables to fold).
    messy = [
        {"attribute": u["attribute"], "spellings": u["variants"]["symbols"],
         "labels": u["variants"]["labels"], "units": u["variants"]["units"],
         "brands": u["brands_printing"]}
        for u in universe
        if len(u["variants"]["symbols"]) > 1 or len(u["variants"]["units"]) > 1
    ]
    messy.sort(key=lambda x: -x["brands"])

    out = {
        "mandate": "Sam 2026-10-12: normalize ACROSS brands — 3-of-20 unifies, 17 are absence, slot stays open",
        "brands_total": len(all_brands),
        "attributes_tracked": len(universe),
        "cross_brand_watchlist": watch[:40],
        "variant_curation_queue": messy[:40],
        "universe": universe,
    }
    (EXPORT / "attribute_universe.json").write_text(json.dumps(out, indent=1, sort_keys=True))
    print(f"brands: {len(all_brands)} | attribute clusters: {len(universe)}")
    print("3-of-20 style examples (small brand coverage, real claims):")
    for u in universe:
        if 2 <= u["brands_printing"] <= 4 and u["claims"] >= 100:
            print(f"  {u['attribute']}: {u['coverage']} claims={u['claims']} spellings={u['variants']['symbols'][:4]}")
    print(f"watchlist entries: {len(watch)}  | variant-curation entries: {len(messy)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
