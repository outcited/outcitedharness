#!/usr/bin/env python3
"""Lookup-based store inventory — immune to NFS readdir truncation.

The Asustor NFS export enumerates directories non-deterministically (65,360 /
65,617 / 105,460-entry listings of the same tree across runs). Lookups, how-
ever, are reliable. So: take the known sha universe, stat each canonical
candidate path, emit sha<TAB>size<TAB>path for every hit.

usage: inventory_lookup.py --store NAME --root /path/to/cas --shas shas.txt --out inv.tsv
       (run ON the box that holds the store; root contains {sha[:2]}/{sha}*)
"""

from __future__ import annotations

import argparse
import os
import sys


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", required=True)
    ap.add_argument("--root", required=True)
    ap.add_argument("--shas", required=True, help="file with one hex sha per line")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    n = hit = 0
    with open(args.shas) as fin, open(args.out, "w") as fout:
        for line in fin:
            sha = line.strip().lower()
            if len(sha) < 31:
                continue
            n += 1
            d = os.path.join(args.root, sha[:2])
            for cand in (f"{sha}.pdf", sha, sha[:31], f"{sha[:31]}.pdf"):
                p = os.path.join(d, cand)
                if os.path.exists(p):
                    try:
                        size = os.path.getsize(p)
                    except OSError:
                        size = 0
                    fout.write(f"{sha}\t{size}\t{p}\n")
                    hit += 1
                    break
    print(f"{args.store}: hit {hit}/{n}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
