#!/usr/bin/env python3
"""S4 — L4 identity binding: join identity stamps to extraction rows.

Takes verified lane-out rows + identity stamps → identity-bound rows where
every row carries (manufacturer, part_identity, identity_source) from the
stamp file, keyed by pdf_sha. Rows without stamps keep identity: null
(quarantined, never judged as candidates).

Also joins the KIM category (from aisle coverage / manifest category field)
so every row carries its category context.

usage: python3 identity_bind.py --rows verified/thermal.s0.jsonl \
    --stamps identity-stamps.jsonl --out bound/thermal.s0.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rows", nargs="+", required=True, help="verified JSONL files")
    ap.add_argument("--stamps", required=True, help="identity stamps JSONL")
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()

    # load stamps: sha → identity record
    stamps: dict[str, dict] = {}
    for line in open(args.stamps, errors="replace"):
        try:
            o = json.loads(line)
        except Exception:
            continue
        sha = o.get("sha256")
        if sha:
            stamps[sha] = o
    print(f"loaded {len(stamps)} identity stamps", file=sys.stderr)

    args.out_dir = Path(args.out_dir)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    tally: Counter = Counter()
    for rows_path in args.rows:
        rows_path = Path(rows_path)
        out_path = args.out_dir / rows_path.name.replace(".s0.", ".bound.").replace(".s1.", ".bound.").replace(".s2.", ".bound.")
        out_fh = open(out_path, "w")
        lane_tally: Counter = Counter()
        for line in open(rows_path, errors="replace"):
            try:
                row = json.loads(line)
            except Exception:
                continue
            sha = row.get("pdf_sha") or row.get("document_sha256") or row.get("sha256")
            stamp = stamps.get(sha, {})
            row["manufacturer"] = stamp.get("manufacturer")
            row["part_identity"] = stamp.get("part_identity")
            row["identity_source"] = stamp.get("identity_source")
            row["identity_status"] = stamp.get("identity_status", "unstamped")
            if stamp.get("part_identity"):
                lane_tally["identity_bound"] += 1
            else:
                lane_tally["identity_null"] += 1
            # carry the verify verdict up
            v = (row.get("_verify") or {}).get("verdict")
            if v:
                lane_tally[f"verify:{v}"] += 1
            out_fh.write(json.dumps(row, ensure_ascii=True) + "\n")
        out_fh.close()
        bound = lane_tally.get("identity_bound", 0)
        total = bound + lane_tally.get("identity_null", 0)
        print(f"{rows_path.name}: {bound}/{total} identity-bound ({bound/max(1,total)*100:.0f}%)",
              file=sys.stderr)
        tally.update(lane_tally)

    print(json.dumps({
        "files": len(args.rows),
        "identity_bound": tally.get("identity_bound", 0),
        "identity_null": tally.get("identity_null", 0),
        "verified": tally.get("verify:verified", 0),
    }, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
