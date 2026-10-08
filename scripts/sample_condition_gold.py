#!/usr/bin/env python3
"""Sample stratified condition_verbatim strings for the gold fixture.

Deterministic (seeded). Strata: vendor x frequency band, plus a mandatory
include-list of edge/reject classes observed in the population survey
(burn-power-v1, 2026-10-07: 9,551 unique strings / 64,205 rows).

Output: JSONL {vendor, stem, condition_verbatim, freq} — labels are added
by hand in the fixture file, never by the parser under test.
"""

from __future__ import annotations

import argparse
import collections
import glob
import json
import random
from pathlib import Path

BURN = Path("/Volumes/M5_4TB/extract-results/burn-power-v1")

# Edge/reject classes that MUST be represented regardless of sampling.
INCLUDE = [
    "-",                                   # dash-only -> reject
    "–",                                   # en-dash -> reject
    "static",                              # mode word only
    "static;",
    "AC (f>1 Hz)",                         # mode + comparator freq
    "A ceramic capacitor is recommended.", # prose -> reject
    "minimal footprint",                   # prose -> reject
    "Ta = 25°C ,unless otherwise specified",  # anchor + trailing prose
    "Ta=25℃",                             # unicode celsius block
    "T C=25 °C",                           # subscript split
    "V GS=0 V, V DS=25 V, f =1 MHz",       # full subscript-split chain
    "V_GS=20 V, V_DS=0 V",                 # underscore subscripts
    "VDD ⋍ 300V",                          # approx-equal glyph
    "VGS = ±20V, VDS = 0V",                # symmetric magnitude
    "TC=100°C",                            # case temp, non-25
    "IOUT=0mA",                            # zero-value current
    "VEN=0V, OFF mode",                    # enable-pin + mode residue
    "VDS = 10V, ID = 1mA",                 # spaced classic
    "f = 1MHz, open drain",                # freq + config residue
]


def collect(burn: Path):
    conds: dict[str, dict] = {}
    per_vendor: dict[str, list[str]] = collections.defaultdict(list)
    for f in sorted(glob.glob(str(burn / "*.json"))):
        name = Path(f).name
        if name.startswith("_"):
            continue
        vendor = name.split("-")[0]
        try:
            d = json.load(open(f))
        except Exception:
            continue
        for c in d.get("claim_data") or []:
            cond = (c.get("condition") or "").strip()
            if not cond:
                continue
            key = cond
            if key not in conds:
                conds[key] = {"vendor": vendor, "stem": name[:-5], "freq": 0}
            conds[key]["freq"] += 1
            per_vendor[vendor].append(key)
    return conds


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=180, help="sampled strings")
    ap.add_argument("--seed", type=int, default=20261007)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    conds = collect(BURN)
    rng = random.Random(args.seed)

    picked: list[dict] = []
    seen: set[str] = set()

    for s in INCLUDE:
        if s in conds:
            picked.append({**conds[s], "condition_verbatim": s})
            seen.add(s)
        else:
            picked.append({"vendor": "include", "stem": "include-list",
                           "freq": 0, "condition_verbatim": s})
            seen.add(s)

    # Frequency bands per vendor: top-20, mid (freq 5-50), tail (freq 1-2)
    by_vendor: dict[str, list[str]] = collections.defaultdict(list)
    for s, meta in conds.items():
        if s not in seen:
            by_vendor[meta["vendor"]].append((meta["freq"], s))

    quota = max(1, (args.n - len(picked)) // 3)
    for vendor, items in sorted(by_vendor.items()):
        items.sort(key=lambda x: (-x[0], x[1]))
        top = [s for _, s in items[:20]]
        mid = [s for f_, s in items if 5 <= f_ <= 50]
        tail = [s for f_, s in items if f_ <= 2]
        rng.shuffle(mid)
        rng.shuffle(tail)
        per_band = max(1, quota // 3)
        for band in (top[:per_band], mid[:per_band], tail[:per_band]):
            for s in band:
                if s not in seen:
                    picked.append({**conds[s], "condition_verbatim": s})
                    seen.add(s)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w") as h:
        for row in picked:
            h.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"sampled {len(picked)} strings -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
