#!/usr/bin/env python3
"""Enrich lane rows with part_number via the corpus-delivery manifests.

The staging/board chain keys everything by part_number. The full-corpus lanes
key rows by pdf_sha (content-addressed blobs). This joins each row to its
delivery manifest row (sha256 -> source_url) and derives the part slug the
same way the 2026-10-08/09 waves did: `<vendor-token>-<url-file-stem>`,
lowercased and sanitized.

Usage:
    python3 scripts/enrich_lane_rows.py \
        --rows results/pillars-fullcorpus-20261013/pillar-rows.jsonl \
        --out results/pillars-fullcorpus-20261013/pillar-rows-enriched.jsonl
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DELIVERIES = Path("/Volumes/M5_4TB/exports/corpus-deliveries")


def build_manifest_index() -> dict[str, dict]:
    idx: dict[str, dict] = {}
    for mf in sorted(DELIVERIES.glob("*/corpus-delivery-manifest.jsonl")):
        for line in mf.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            sha = r.get("sha256")
            if sha and sha not in idx:
                idx[sha] = {"source_url": r.get("source_url") or "",
                            "asset_id": r.get("asset_id") or ""}
    return idx


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def part_slug(source_url: str, vendor_fallback: str) -> str:
    if source_url:
        stem = source_url.rstrip("/").rsplit("/", 1)[-1]
        stem = re.sub(r"\.(pdf|PDF)$", "", stem)
        host = ""
        m = re.match(r"https?://(?:www\.)?([^./]+)", source_url)
        if m:
            host = m.group(1)
        slug = _SLUG_RE.sub("-", f"{host}-{stem}".lower()).strip("-")
        if slug:
            return slug[:96]
    return vendor_fallback


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rows", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    idx = build_manifest_index()
    print(f"manifest index: {len(idx)} shas", flush=True)

    n = missed = 0
    with args.out.open("w") as out:
        for line in args.rows.open():
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            sha = r.get("pdf_sha") or r.get("document_sha256") or ""
            m = idx.get(sha)
            if m:
                r["source_url"] = r.get("source_url") or m["source_url"]
                r["part_number"] = r.get("part_number") or part_slug(
                    m["source_url"], sha[:16])
                n += 1
            else:
                # not from corpus-deliveries (vault/etc): slug from filename
                sp = r.get("source_path") or ""
                stem = Path(sp).stem
                r["part_number"] = r.get("part_number") or stem[:96]
                missed += 1
            out.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
    print(json.dumps({"enriched_from_manifest": n, "fallback_slug": missed}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())