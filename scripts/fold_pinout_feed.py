#!/usr/bin/env python3
"""Fold the verified pin-table batch onto served OPNs (2026-10-18).

Joins (deterministically, no fuzzy matching — the IDENTITY-03 chain):
  identity-joins.jsonl  sha -> canonical served OPN   (status exact|alias)
  pin-rows-batch.jsonl  sha -> quote-verified pin rows

and writes `parts_a_pinout.jsonl` — one PartRecord per served OPN, one claim
per pin (field `pin_<n>_<name>`, value `name (type)`, quote = the verified
row quote). The feed name sorts FIRST in ExportStore's `parts_*.jsonl` walk
(a < burn < cr_core < family < lane) and each row UNIONS the claims the
other feeds already carry for that OPN, so pin claims are reachable through
`/api/extraction/v1/parts/{opn}` without shadowing anything the earlier
feeds serve. Every row carries the admission tier so completeness is never
overstated (UNCONFIRMED COMPLETENESS = the pins present are verified, the
absence of more is not). No page chain on this batch (cr-core law: resolves
to no page and says so); doc sha rides the source.

Cube + manifest are regenerated the same way fold_lane_feed does (walks
every parts_*.jsonl; pinout feed keys ("mcu", "pinout-gold", vendor)).

Usage: python3 scripts/fold_pinout_feed.py \
    --joins evidence/pin-batch-20261015/identity-joins.jsonl \
    --pins results/pin-tables-20261014/pin-rows-batch.jsonl \
    --root <api-store-export>
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fold_lane_feed import sha256  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--joins", type=Path, required=True)
    ap.add_argument("--pins", type=Path, required=True)
    ap.add_argument("--root", type=Path, required=True)
    args = ap.parse_args()

    sha_to_opn: dict[str, dict] = {}
    for line in args.joins.open():
        line = line.strip()
        if not line:
            continue
        j = json.loads(line)
        if j.get("status") in ("exact", "alias") and j.get("canonical_opn"):
            sha_to_opn[j["sha"]] = j
    print(f"joined shas -> served OPNs: {len(sha_to_opn)}")

    rows_out = []
    tiers = Counter()
    pins_total = 0
    for line in args.pins.open():
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        if rec.get("verdict") not in ("ok", None, ""):
            continue
        j = sha_to_opn.get(rec.get("sha") or "")
        if not j:
            continue
        pins = rec.get("rows") or []
        if not pins:
            continue
        claims = []
        seen_fields = set()
        for p in pins:
            name = str(p.get("name") or "?")
            pin = str(p.get("pin") or "?")
            ptype = p.get("type")
            field = f"pin_{pin}_{name}"[:64]
            if field in seen_fields:
                continue
            seen_fields.add(field)
            claims.append({
                "field": field,
                "value": f"{name} ({ptype})" if ptype else name,
                "quote": (p.get("quote") or "")[:200],
                "source": {
                    "column_header": rec.get("package"),
                    "crop": None,
                    "page": None,
                    "row_header": name,
                    "sha": rec["sha"],
                    "doc_sha256": rec["sha"],
                    "source_pdf": None,
                    "provenance_tier": "T1_DATASHEET",
                },
            })
        pins_total += len(claims)
        tiers[j.get("tier") or "?"] += 1
        rows_out.append({
            "part_number": j["canonical_opn"],
            "vendor": j.get("manufacturer") or "unknown",
            "aisle": "mcu",
            "pinout_tier": j.get("tier"),
            "join_status": j.get("status"),
            "join_rule": j.get("rule_version"),
            "claims": claims,
        })

    # one row per OPN (two slugs may canon to one OPN): merge claims
    by_opn: dict[str, dict] = {}
    for r in rows_out:
        cur = by_opn.get(r["part_number"])
        if cur is None:
            by_opn[r["part_number"]] = r
            continue
        have = {c["field"] for c in cur["claims"]}
        for c in r["claims"]:
            if c["field"] not in have:
                cur["claims"].append(c)
                have.add(c["field"])
    rows_out = sorted(by_opn.values(), key=lambda r: r["part_number"])

    # union claims the earlier feeds already carry for these OPNs, so the
    # first-match walk returning OUR row loses nothing
    earlier_claims: dict[str, list[dict]] = {}
    earlier_meta: dict[str, dict] = {}
    for fpath in sorted(args.root.glob("parts_*.jsonl")):
        if fpath.name.startswith("parts_a_pinout"):
            continue
        for line in fpath.read_text().splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            opn = row.get("part_number")
            if opn not in by_opn:
                continue
            have = {c["field"] for c in earlier_claims.get(opn, [])}
            for c in row.get("claims") or []:
                if isinstance(c, dict) and c.get("field") and c["field"] not in have:
                    earlier_claims.setdefault(opn, []).append(c)
                    have.add(c["field"])
            if opn not in earlier_meta:
                earlier_meta[opn] = {"vendor": row.get("vendor"),
                                     "aisle": row.get("aisle")}
    merged_rows = 0
    for r in rows_out:
        opn = r["part_number"]
        pre = earlier_claims.get(opn) or []
        if pre:
            merged_rows += 1
            have = {c["field"] for c in r["claims"]}
            r["claims"] = r["claims"] + [c for c in pre if c["field"] not in have]
            em = earlier_meta.get(opn) or {}
            if em.get("vendor"):
                r["vendor"] = em["vendor"]
            if em.get("aisle"):
                r["aisle"] = em["aisle"]
    print(f"merged earlier-feed claims into {merged_rows}/{len(rows_out)} rows")

    body = "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n"
                   for r in rows_out)
    feed = args.root / "parts_a_pinout.jsonl"
    tmp = feed.with_suffix(".jsonl.tmp")
    tmp.write_text(body)
    tmp.replace(feed)
    print(f"parts_a_pinout.jsonl: {len(rows_out)} served OPNs, "
          f"{sum(len(r['claims']) for r in rows_out)} pin claims, {len(body)} bytes")
    print("tiers:", dict(tiers))

    # cube regen over every feed (same walk as fold_lane_feed)
    cells: dict[tuple, dict] = {}
    for fpath in sorted(args.root.glob("parts_*.jsonl")):
        for line in fpath.read_text().splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            vendor = row.get("vendor") or "unknown"
            name = fpath.name
            if name.startswith("parts_a_pinout"):
                key = (row.get("aisle") or "mcu", "pinout-gold", vendor)
            elif name == "parts_lane.jsonl":
                key = (row.get("aisle") or "unclassified", "deterministic-lane", vendor)
            elif name == "parts_cr_core.jsonl":
                key = (row.get("aisle") or "unassigned", "cr-core", vendor)
            elif name == "parts_family.jsonl":
                key = ("mcu", "family-documents", vendor)
            elif name == "parts_burn.jsonl":
                key = ("power/semiconductor", "burn", vendor)
            else:
                key = ("power/semiconductor", "catalog", vendor)
            cell = cells.setdefault(key, {
                "category": key[0], "aisle": key[1], "vendor": key[2],
                "parts": 0, "claims": 0, "laned": True, "curves": False,
            })
            cell["parts"] += 1
            cell["claims"] += len(row.get("claims") or [])
    cube = {
        "dimensions": ["category", "aisle", "vendor"],
        "cells": sorted(cells.values(), key=lambda c: (c["category"], c["aisle"], c["vendor"])),
        "laned": True,
    }
    cbody = json.dumps(cube, indent=1, sort_keys=True)
    ctmp = args.root / "cube.json.tmp"
    ctmp.write_text(cbody)
    ctmp.replace(args.root / "cube.json")
    print(f"cube.json: {len(cube['cells'])} cells")

    mf = args.root / "fold_manifest.json"
    manifest = json.loads(mf.read_text()) if mf.exists() else {}
    manifest["pinout_20261018"] = {
        "rows": len(rows_out),
        "pin_claims": sum(len(r["claims"]) for r in rows_out),
        "bytes": len(body),
        "sha256": sha256(body),
        "joins": str(args.joins),
        "pins": str(args.pins),
        "tiers": dict(tiers),
    }
    mtmp = mf.with_suffix(".json.tmp")
    mtmp.write_text(json.dumps(manifest, indent=1, sort_keys=True))
    mtmp.replace(mf)
    print("fold_manifest.json updated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
