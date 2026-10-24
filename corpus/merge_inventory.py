#!/usr/bin/env python3
"""Merge per-store inventories into the corpus INDEX.

INDEX law (2026-10-19):
  - content is canonical by sha256; cas filename hex = the sha (prefix at minimum)
  - INDEX.jsonl: one row per sha: {sha, size, bytes_total, locations: {store: path}}
  - a sha present in <1 store is a gap; present in every store is fully mirrored

usage: merge_inventory.py --out INDEX.jsonl store=inv.tsv [store=inv.tsv ...]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict


def norm_sha(hexstr: str) -> str:
    h = hexstr.strip().lower()
    return h


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("stores", nargs="+", help="store_name=inventory.tsv")
    args = ap.parse_args()

    index: dict[str, dict] = {}
    for spec in args.stores:
        name, _, path = spec.partition("=")
        rows = 0
        with open(path, errors="replace") as f:
            for line in f:
                parts = line.rstrip("\n").split("\t")
                if len(parts) < 2:
                    continue
                sha = norm_sha(parts[0])
                if len(sha) < 31:  # sha prefix minimum used across stores
                    continue
                try:
                    size = int(parts[1])
                except ValueError:
                    size = 0
                path_str = parts[2] if len(parts) > 2 else ""
                rec = index.setdefault(sha, {"sha": sha, "size": size, "locations": {}})
                rec["locations"][name] = path_str
                if size and not rec["size"]:
                    rec["size"] = size
                rows += 1
        print(f"{name}: {rows} files", file=sys.stderr)

    with open(args.out, "w") as f:
        for sha in sorted(index):
            f.write(json.dumps(index[sha], sort_keys=True) + "\n")

    stores = [s.split("=")[0] for s in args.stores]
    full = sum(1 for r in index.values() if len(r["locations"]) == len(stores))
    print(json.dumps({
        "unique_shas": len(index),
        "stores": stores,
        "fully_mirrored": full,
        "gaps": len(index) - full,
        "bytes_unique": sum(r["size"] for r in index.values()),
    }, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
