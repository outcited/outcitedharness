#!/usr/bin/env python3
"""Power topology wave: read topology/integration/isolation for all parts.

Runs harness.electronics.power_topology.read_topology over every part in
the power-datasheet pairs (substrate page text joined by sha), writing
one JSONL row per part and printing the scoreboard. Fill-only, printed
evidence only; absent answers are first-class results.
"""

from __future__ import annotations

import argparse
import collections
import glob
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness.electronics.power_topology import (  # noqa: E402
    TOPOLOGY_SCHEMA,
    read_topology,
)

PAIRS_ROOT = Path("/Volumes/M5_4TB/exports/power-datasheet-pairs")
SUBSTRATE_GLOB = "/Volumes/M5_4TB/extract-results/collected/*/{sha}.json"


def substrate_path(sha: str) -> Path | None:
    for hit in glob.glob(SUBSTRATE_GLOB.format(sha=sha)):
        return Path(hit)
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    topo_pop = collections.Counter()
    integ_pop = collections.Counter()
    iso_pop = collections.Counter()
    aisle_topo: dict[str, collections.Counter] = collections.defaultdict(
        collections.Counter)
    n = 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w") as h:
        for vendor in ("ti", "infineon", "rohm"):
            pairs = PAIRS_ROOT / f"{vendor}-20260908" / "pairs.jsonl"
            if not pairs.exists():
                continue
            for line in pairs.open():
                r = json.loads(line)
                if args.limit and n >= args.limit:
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
                out = read_topology(pages, part_number=r["part_number"])
                row = {
                    "schema": TOPOLOGY_SCHEMA,
                    "part_number": r["part_number"],
                    "vendor": r["vendor"],
                    "selector_aisle": r["aisle"],
                    "sha256": r["pdf_sha256"],
                    "reader": "power_topology.v1",
                    "topologies": out["topologies"],
                    "integration_class": out["integration_class"],
                    "isolated": out["isolated"],
                    "evidence": out["evidence"],
                }
                h.write(json.dumps(row, ensure_ascii=False) + "\n")
                for t in out["topologies"]:
                    topo_pop[t] += 1
                    aisle_topo[r["aisle"]][t] += 1
                integ_pop[out["integration_class"] or "absent"] += 1
                iso_pop[str(out["isolated"])] += 1
                n += 1
            if args.limit and n >= args.limit:
                break

    print(f"read {n} parts -> {args.out}")
    print("topology claims (parts may carry several):")
    for k, v in topo_pop.most_common():
        print(f"  {k:14s} {v:5d}")
    with_claims = sum(1 for _ in topo_pop.values()) and None
    # recompute from the written rows: parts carrying zero claims
    zero = 0
    for line in args.out.open():
        row = json.loads(line)
        if not row["topologies"]:
            zero += 1
    print(f"topology_absent: {zero} ({100 * zero / max(n, 1):.1f}% of parts carry zero)")
    print("integration_class:")
    for k, v in integ_pop.most_common():
        print(f"  {k:12s} {v:5d}")
    print("isolated:", dict(iso_pop))
    print("\nper-aisle topology coverage (parts with >=1 claim):")
    for aisle, counter in sorted(aisle_topo.items()):
        print(f"  {aisle:22s} {dict(counter)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
