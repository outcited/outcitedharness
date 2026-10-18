#!/usr/bin/env python3
"""Build the served-store feed from deterministic lane rows (2026-10-18).

Reads enriched lane rows (thermal / power-modes / aec — verdict=printed_row,
deterministic extractors), deduplicates per (part, field) preferring typ over
max and later runs over earlier, and writes `parts_lane.jsonl` in the
PartRecord wire shape the ExportStore walks. Every claim carries the full
provenance chain: verbatim quote + printed page + pdf sha + source url.

Category routing: part -> aisle from the staging index (filename stems in
each aisle's datasheet_extractions dir); lane default only for parts staging
has never seen (mode->power, aec->mcu, thermal->unclassified — the default
is recorded on the row as category_source so the cube never lies about how
the category was known).

Cube: regenerated over EVERY parts_*.jsonl feed present (superset of
fold_strata.regen_cube); parts_lane rows key by their own category with
aisle "deterministic-lane".

Usage: python3 scripts/fold_lane_feed.py --bank results/lane-bank-20261018 \
    --root <api-store-export>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STAGING = Path("/Volumes/M5_4TB/staging/datasheet_extractions")
AISLE_DIRS = {
    "mcu": "mcu_family_content",
    "power/semiconductor": "power_family_content",
    "connector": "connector_family_content",
    "refdesign": "refdesign_family_content",
}
LANE_CATEGORY_DEFAULT = {"mode": "power/semiconductor", "aec": "mcu",
                         "thermal": "unclassified"}
HOST_RE = re.compile(r"https?://(?:www\.)?([^./]+)")


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def staging_index() -> dict[str, str]:
    """part slug -> category, from staging filename stems."""
    idx: dict[str, str] = {}

    def scan(cat: str, d: Path):
        try:
            names = os.listdir(d)
        except OSError:
            return
        for fn in names:
            if fn.endswith(".json"):
                pn = fn[:-5]
                if pn not in idx:
                    idx[pn] = cat

    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = [ex.submit(scan, cat, STAGING / aisle / "datasheet_extractions")
                for cat, aisle in AISLE_DIRS.items()]
        for f in futs:
            f.result()
    return idx


def lane_of(fn: str) -> str:
    n = Path(fn).name
    if "mode" in n:
        return "mode"
    if "aec" in n:
        return "aec"
    return "thermal"


_ROLE = {"typ": 3, "min": 1, "max": 1}


def claim_from_row(row: dict) -> dict:
    field = row.get("metric") or row.get("mode") or row.get("grade") or "value"
    role = row.get("value_role")
    if role and role != "typ":
        field = f"{field} ({role})"
    quote = (row.get("verbatim") or "")[:200]
    src = {
        "column_header": row.get("unit"),
        "crop": None,
        "page": row.get("page_1based"),
        "row_header": row.get("metric") or row.get("mode") or row.get("grade"),
        "sha": row.get("pdf_sha") or row.get("document_sha256"),
        "doc_sha256": row.get("document_sha256"),
        "source_pdf": row.get("source_url"),
        "provenance_tier": "T1_DATASHEET",
    }
    cond = row.get("condition_verbatim") or row.get("conditions_verbatim")
    if cond:
        src["row_header"] = f"{src['row_header']} | {str(cond)[:120]}" if src["row_header"] else str(cond)[:200]
    return {
        "field": str(field)[:64],
        "value": str(row.get("value") if row.get("value") is not None else row.get("grade")),
        "quote": quote,
        "source": src,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bank", type=Path, required=True)
    ap.add_argument("--root", type=Path, required=True)
    args = ap.parse_args()

    print("building staging index ...", flush=True)
    sidx = staging_index()
    print(f"staging index: {len(sidx)} parts", flush=True)

    # part -> field -> (rank, claim, seq); deterministic rows only
    best: dict[str, dict[str, tuple]] = {}
    lane_rows: dict[str, int] = defaultdict(int)
    loaded = skipped = 0
    seq = 0
    for path in sorted(args.bank.glob("*-enriched.jsonl")):
        lane = lane_of(path.name)
        for line in path.open():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("verdict") not in ("printed_row", "extracted"):
                skipped += 1
                continue
            loaded += 1
            lane_rows[lane] += 1
            pn = row.get("part_number")
            if not pn:
                continue
            claim = claim_from_row(row)
            rank = (_ROLE.get(row.get("value_role"), 2), -seq)
            cur = best.setdefault(pn, {}).get(claim["field"])
            if cur is None or rank > cur[0]:
                best[pn][claim["field"]] = (rank, claim, lane)
            seq += 1

    print(f"rows loaded {loaded}, skipped(non-printed) {skipped}", flush=True)

    rows_out = []
    cat_counts: dict[str, int] = defaultdict(int)
    src_counts: dict[str, int] = defaultdict(int)
    for pn, fields in sorted(best.items()):
        claims = [v[1] for v in fields.values()]
        lanes = {v[2] for v in fields.values()}
        if pn in sidx:
            cat, cat_src = sidx[pn], "staging"
        elif len(lanes) == 1:
            cat, cat_src = LANE_CATEGORY_DEFAULT[next(iter(lanes))], "lane-default"
        else:
            cat, cat_src = "unclassified", "lane-default"
        cat_counts[cat] += 1
        src_counts[cat_src] += 1
        src_url = None
        for c in claims:
            u = c["source"].get("source_url") or c["source"].get("source_pdf")
            if u:
                src_url = u
                break
        vendor = "unknown"
        if src_url:
            m = HOST_RE.match(src_url)
            if m:
                vendor = m.group(1)
        row = {
            "part_number": pn,
            "vendor": vendor,
            "aisle": cat,
            "category_source": cat_src,
            "claims": sorted(claims, key=lambda c: c["field"]),
        }
        rows_out.append(row)

    body = "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n"
                   for r in rows_out)
    feed = args.root / "parts_lane.jsonl"
    tmp = feed.with_suffix(".jsonl.tmp")
    tmp.write_text(body)
    tmp.replace(feed)
    print(f"parts_lane.jsonl: {len(rows_out)} parts, {sum(len(r['claims']) for r in rows_out)} claims, {len(body)} bytes")
    print("category counts:", dict(cat_counts))
    print("category sources:", dict(src_counts))

    # ---- cube regen over every feed present ----
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
            if name == "parts_lane.jsonl":
                key = (row.get("aisle") or "unclassified", "deterministic-lane", vendor)
            elif name.startswith("parts_a_pinout") or name.startswith("parts_z_pinout"):
                dim = ("pinout-staged" if row.get("join_status") == "unresolved"
                       else "pinout-gold")
                key = (row.get("aisle") or "mcu", dim, vendor)
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
    ctmp = (args.root / "cube.json.tmp")
    ctmp.write_text(cbody)
    ctmp.replace(args.root / "cube.json")
    print(f"cube.json: {len(cube['cells'])} cells")

    # ---- manifest ----
    mf = args.root / "fold_manifest.json"
    manifest = json.loads(mf.read_text()) if mf.exists() else {}
    manifest["lane_20261018"] = {
        "rows": len(rows_out),
        "bytes": len(body),
        "sha256": sha256(body),
        "bank": str(args.bank),
        "lane_rows": dict(lane_rows),
        "category_counts": dict(cat_counts),
        "category_sources": dict(src_counts),
        "claims": sum(len(r["claims"]) for r in rows_out),
    }
    mtmp = mf.with_suffix(".json.tmp")
    mtmp.write_text(json.dumps(manifest, indent=1, sort_keys=True))
    mtmp.replace(mf)
    print("fold_manifest.json updated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
