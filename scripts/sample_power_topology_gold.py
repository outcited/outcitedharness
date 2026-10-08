#!/usr/bin/env python3
"""Sample power parts for the topology gold fixture (deterministic).

Stratified by vendor x selector aisle with a floor per aisle, joining
pairs.jsonl to substrate page text. Output JSONL rows carry the front
text so labels can be written against the printed evidence by hand.
"""

from __future__ import annotations

import argparse
import collections
import glob
import json
import random
from pathlib import Path

PAIRS_ROOT = Path("/Volumes/M5_4TB/exports/power-datasheet-pairs")
SUBSTRATE_GLOB = "/Volumes/M5_4TB/extract-results/collected/*/{sha}.json"

# per-aisle sample quota (dc-dc carries the topology load)
QUOTA = {
    ("ti", "dc-dc-converters"): 46,
    ("ti", "ldo-regulators"): 8,
    ("ti", "battery-management"): 6,
    ("ti", "ac-dc"): 6,
    ("ti", "gate-drivers"): 4,
    ("ti", "load-switches"): 4,
    ("ti", "power-management"): 6,
    ("ti", "power-stages"): 4,
    ("ti", "voltage-references"): 3,
    ("ti", "power-protection"): 3,
    ("rohm", "ldo-regulators"): 8,
    ("rohm", "discrete-mosfets"): 3,
    ("infineon", "discrete-mosfets"): 4,
}


def substrate_path(sha: str) -> Path | None:
    for hit in glob.glob(SUBSTRATE_GLOB.format(sha=sha)):
        return Path(hit)
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=20261008)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    by_stratum: dict[tuple[str, str], list[dict]] = collections.defaultdict(list)
    for vendor in ("ti", "infineon", "rohm"):
        f = PAIRS_ROOT / f"{vendor}-20260908" / "pairs.jsonl"
        if not f.exists():
            continue
        for line in f.open():
            r = json.loads(line)
            by_stratum[(vendor, r["aisle"])].append(r)

    rows: list[dict] = []
    for stratum, items in sorted(by_stratum.items()):
        quota = QUOTA.get(stratum, 0)
        if not quota:
            continue
        rng.shuffle(items)
        taken = 0
        for r in items:
            if taken >= quota:
                break
            sp = substrate_path(r["pdf_sha256"])
            if not sp:
                continue
            try:
                d = json.loads(sp.read_text())
            except Exception:
                continue
            pages = d.get("pages") or []
            if not pages:
                continue
            rows.append({
                "vendor": r["vendor"],
                "part_number": r["part_number"],
                "selector_aisle": r["aisle"],
                "sha256": r["pdf_sha256"],
                "front_text": " ".join(
                    " ".join((p.get("text") or "").split()) for p in pages[:3]
                )[:1500],
            })
            taken += 1

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w") as h:
        for row in rows:
            h.write(json.dumps(row, ensure_ascii=False) + "\n")
    dist = collections.Counter((r["vendor"], r["selector_aisle"]) for r in rows)
    print(f"sampled {len(rows)} parts -> {args.out}")
    for k, n in sorted(dist.items()):
        print(f"  {k[0]:9s} {k[1]:22s} {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
