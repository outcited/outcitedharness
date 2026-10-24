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


def _pin_claims(rec: dict) -> list[dict]:
    """Verified pin rows -> PartClaim wire shape (shared by OPN + slug paths)."""
    claims = []
    seen = set()
    for p in rec.get("rows") or []:
        name = str(p.get("name") or "?")
        pin = str(p.get("pin") or "?")
        ptype = p.get("type")
        field = f"pin_{pin}_{name}"[:64]
        if field in seen:
            continue
        seen.add(field)
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
    return claims


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--joins", type=Path, required=True)
    ap.add_argument("--pins", type=Path, required=True)
    ap.add_argument("--root", type=Path, required=True)
    args = ap.parse_args()

    sha_to_opn: dict[str, dict] = {}
    slug_index: dict[str, dict] = {}
    for line in args.joins.open():
        line = line.strip()
        if not line:
            continue
        j = json.loads(line)
        slug_index[j["sha"]] = j
        if j.get("status") in ("exact", "alias") and j.get("canonical_opn"):
            sha_to_opn[j["sha"]] = j
    print(f"joined shas -> served OPNs: {len(sha_to_opn)}; "
          f"{len(slug_index) - len(sha_to_opn)} unresolved (slug grain)")

    rows_out = []
    slug_rows: dict[str, dict] = {}
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
            # unresolved join: serve at slug grain (same law as the lane
            # feed) with the join outcome recorded — never a fabricated OPN
            u = slug_index.get(rec.get("sha") or {})
            if not u:
                continue
            if not (rec.get("rows") or []):
                continue
            slug = u.get("slug") or rec["sha"][:16]
            slug_rows.setdefault(slug, {
                "part_number": slug,
                "vendor": u.get("manufacturer") or "unknown",
                "aisle": "mcu",
                "pinout_tier": u.get("tier"),
                "join_status": "unresolved",
                "join_cause": u.get("cause"),
                "claims": [],
            })
            have = {c["field"] for c in slug_rows[slug]["claims"]}
            for c in _pin_claims(rec):
                if c["field"] not in have:
                    slug_rows[slug]["claims"].append(c)
                    have.add(c["field"])
            continue
        pins = rec.get("rows") or []
        if not pins:
            continue
        claims = _pin_claims(rec)
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
    slug_merged = sorted(slug_rows.values(), key=lambda r: r["part_number"])
    slug_pins = sum(len(r["claims"]) for r in slug_merged)
    print(f"slug-grain pinout rows: {len(slug_merged)} parts / {slug_pins} claims")

    # union claims the earlier feeds already carry for these parts, so the
    # first-match walk returning OUR row loses nothing (OPN rows AND slug
    # rows — the lane feed keys some of the same slugs)
    mine = {r["part_number"] for r in rows_out} | {r["part_number"] for r in slug_merged}
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
            if opn not in mine:
                continue
            have = {c["field"] for c in earlier_claims.get(opn, [])}
            for c in row.get("claims") or []:
                if isinstance(c, dict) and c.get("field") and c["field"] not in have:
                    earlier_claims.setdefault(opn, []).append(c)
                    have.add(c["field"])
            if opn not in earlier_meta:
                earlier_meta[opn] = {"vendor": row.get("vendor"),
                                     "aisle": row.get("aisle")}
    all_rows = rows_out + slug_merged
    merged_rows = 0
    for r in all_rows:
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
    print(f"merged earlier-feed claims into {merged_rows}/{len(all_rows)} rows")

    # two feeds: OPN rows walk FIRST (small, merged claims so nothing is
    # shadowed); slug rows walk LAST (96MB+ — a miss-only tax, so common
    # part lookups never read it)
    opn_body = "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n"
                       for r in rows_out)
    feed = args.root / "parts_a_pinout.jsonl"
    tmp = feed.with_suffix(".jsonl.tmp")
    tmp.write_text(opn_body)
    tmp.replace(feed)
    print(f"parts_a_pinout.jsonl: {len(rows_out)} OPN rows, "
          f"{sum(len(r['claims']) for r in rows_out)} claims, {len(opn_body)} bytes")

    slug_body = "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n"
                        for r in slug_merged)
    feed2 = args.root / "parts_z_pinout_staged.jsonl"
    tmp2 = feed2.with_suffix(".jsonl.tmp")
    tmp2.write_text(slug_body)
    tmp2.replace(feed2)
    print(f"parts_z_pinout_staged.jsonl: {len(slug_merged)} slug rows, "
          f"{slug_pins} pin claims, {len(slug_body)} bytes")
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
            if name.startswith("parts_a_pinout") or name.startswith("parts_z_pinout"):
                dim = ("pinout-staged" if row.get("join_status") == "unresolved"
                       else "pinout-gold")
                key = (row.get("aisle") or "mcu", dim, vendor)
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
        "opn_rows": len(by_opn),
        "slug_rows": len(slug_merged),
        "slug_pin_claims": slug_pins,
        "bytes_opn": len(opn_body),
        "sha256_opn": sha256(opn_body),
        "bytes_slug": len(slug_body),
        "sha256_slug": sha256(slug_body),
        "pin_claims": sum(len(r["claims"]) for r in rows_out),
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
