#!/usr/bin/env python3
"""Catalog attachment swarm: burn results -> parts -> claims -> consensus.

Reads every burn result JSON, registers parts (OPN from filename/part-field
via plausible_opn), attaches quote-bound claims, runs consensus per part.
This fills catalog.db — the part-keyed product. Idempotent: reruns skip
already-attached (opn, doc, symbol, value) tuples.
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, "/Users/samkim/Harnessv1")

from harness.catalog.store import (attach_claim, connect, plausible_opn,
                                   register_part, resolve_consensus)

BURN_DIRS = [
    Path("/Volumes/M5_4TB/extract-results/burn-power-v1"),
    Path("/Volumes/M5_4TB/extract-results/burn-vault-v1"),
]
CATALOG_DB = "/Volumes/M5_4TB/extract-results/catalog.db"


def opn_from_filename(stem):
    tokens = re.split(r"[-_ /.]", stem)
    for t in reversed(tokens):
        if plausible_opn(t):
            return t
    for t in tokens:
        if plausible_opn(t):
            return t
    return None


def main(limit=None):
    con = connect(CATALOG_DB)
    files = []
    for d in BURN_DIRS:
        if d.exists():
            files += sorted(d.glob("*.json"))
    files = [f for f in files if f.stem != "_report"]
    if limit:
        files = files[:limit]
    print(f"burn results: {len(files)}", flush=True)

    existing = set()
    for r in con.execute("SELECT opn, doc_sha256, symbol, value FROM claims_canonical"):
        existing.add((r[0], r[1], r[2], r[3]))

    parts_new = claims_new = conflicts = 0
    for i, f in enumerate(files):
        try:
            res = json.loads(f.read_text())
        except Exception:
            continue
        if "skip" in res or not res.get("claim_data"):
            continue
        stem = f.stem
        opn = opn_from_filename(stem)
        if not opn:
            continue
        doc_sha = res.get("sha256") or f"unhashed:{stem}"
        if con.execute("SELECT 1 FROM parts WHERE opn=?", (opn,)).fetchone() is None:
            vendor = "unknown"
            low = stem.lower()
            for v in ("infineon", "rohm", "ti-", "st_", "stm", "nxp", "renesas",
                       "microchip", "atmel", "gd32", "esp"):
                if v in low:
                    vendor = v.strip("-_")
                    break
            register_part(con, opn, vendor=vendor, doc_sha=doc_sha,
                          coverage="burn-primary", evidence=stem)
            parts_new += 1
        for c in res.get("claim_data", []):
            if not isinstance(c, dict) or c.get("value") is None:
                continue
            key = (opn, doc_sha, (c.get("symbol") or "").upper(), c.get("value"))
            if key in existing:
                continue
            attach_claim(con, opn, c, doc_sha, extractor="burn",
                         version="v41-cloud-1")
            existing.add(key)
            claims_new += 1
        if (i + 1) % 500 == 0:
            print(f"{i+1}/{len(files)} parts+{parts_new} claims+{claims_new}", flush=True)

    con.commit()
    print(f"attachment done: parts_new={parts_new} claims_new={claims_new}", flush=True)

    parts = con.execute("SELECT DISTINCT opn FROM claims_canonical").fetchall()
    for j, (opn,) in enumerate(parts):
        symbols = con.execute(
            "SELECT DISTINCT symbol FROM claims_canonical WHERE opn=?", (opn,)).fetchall()
        for (sym,) in symbols:
            resolve_consensus(con, opn, sym)
        if (j + 1) % 500 == 0:
            print(f"consensus {j+1}/{len(parts)}", flush=True)

    stats = {
        "parts": con.execute("SELECT COUNT(*) FROM parts").fetchone()[0],
        "claims": con.execute("SELECT COUNT(*) FROM claims_canonical").fetchone()[0],
        "consensus_rows": con.execute("SELECT COUNT(*) FROM consensus").fetchone()[0],
        "resolved": con.execute(
            "SELECT COUNT(*) FROM consensus WHERE resolved_value IS NOT NULL").fetchone()[0],
        "conflicted": con.execute(
            "SELECT COUNT(*) FROM consensus WHERE conflict_class IS NOT NULL"
            " AND conflict_class != 'single_source'").fetchone()[0],
    }
    print(json.dumps(stats, indent=2))
    Path("/Volumes/M5_4TB/extract-results/sentinel/catalog-fill.json").write_text(
        json.dumps(stats, indent=2))


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else None)
