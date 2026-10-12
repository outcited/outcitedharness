#!/usr/bin/env python3
"""Fold the extraction strata into the served store — the ASSEMBLY step.

Extracts nothing new. It makes the served store (`api-store-export/`) carry
what the shelves already hold:

  from-cr-core/*.json  ->  parts_cr_core.jsonl   (one PartRecord row per
                            covered part; claims flattened from
                            parametric_specs / overview / pinout; `_meta`
                            provenance preserved — T1_DATASHEET + source_pdf)
  all parts_*.jsonl    ->  cube.json              (regenerated cells)
                         fold_manifest.json      (counts + per-file sha256)

The dw-api loader (ExportStore) walks `parts_*.jsonl` and re-reads
`cube.json` per request, so the front end picks the fold up on reload.

Provenance honesty (D9): cr-core claims carry source_pdf + provenance_tier
but NO page chain — they resolve to no page and say so. Catalog claims keep
their quote/page/sha chain. Nothing is blended.

Usage: python3 scripts/fold_strata.py [--root <api-store-export>]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

STAGING = Path("/Volumes/M5_4TB/staging/datasheet_extractions")
CR_CORE = STAGING / "mcu_family_content" / "from-cr-core"
DEFAULT_ROOT = Path("/Users/samkim/mnt/yosemiteraid5/Public/m5-opencode/api-store-export")

SKIP_FIELDS = {"part_number", "family", "series", "type"}


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _load(fn: str):
    try:
        return json.loads((CR_CORE / fn).read_text())
    except Exception:
        return None


def _flatten_claims(rec: dict, meta: dict) -> list[dict]:
    """Flatten one rich record into PartClaim wire shapes."""
    source = {
        "column_header": None,
        "crop": None,
        "page": None,
        "row_header": None,
        "sha": None,
        "doc_sha256": None,
        "source_pdf": meta.get("source_pdf"),
        "provenance_tier": meta.get("provenance_tier"),
    }
    claims = []

    def add(field: str, value, quote=None):
        if value is None:
            return
        if isinstance(value, (list, dict)):
            value = json.dumps(value, ensure_ascii=False)[:200]
        claims.append({
            "field": str(field)[:64],
            "value": str(value),
            "quote": quote,
            "source": dict(source),
        })

    ps = rec.get("parametric_specs") or {}
    if isinstance(ps, dict):
        for k, v in ps.items():
            if k not in SKIP_FIELDS:
                add(f"spec.{k}", v)

    ov = rec.get("overview") or {}
    if isinstance(ov, dict):
        def walk(node, prefix):
            for k, v in node.items():
                if isinstance(v, dict):
                    walk(v, f"{prefix}{k}.")
                elif isinstance(v, list):
                    add(f"{prefix}{k}", v)
                else:
                    add(f"{prefix}{k}", v)
        walk(ov, "")

    pinout = rec.get("pinout") or {}
    pins = pinout.get("pins") if isinstance(pinout, dict) else None
    funcs = (pinout.get("pinFunctions") if isinstance(pinout, dict) else None) or {}
    if isinstance(pins, list):
        for pin in pins:
            if not isinstance(pin, dict):
                continue
            no = pin.get("no", "?")
            name = pin.get("name") or "?"
            ptype = pin.get("type") or ""
            quote = None
            if isinstance(funcs, dict):
                f = funcs.get(name)
                if isinstance(f, str):
                    quote = f[:200]
                elif f is not None:
                    quote = json.dumps(f, ensure_ascii=False)[:200]
            add(f"pin_{no}", f"{name} ({ptype})" if ptype else name, quote)
    return claims


def fold_cr_core(root: Path) -> dict:
    files = sorted(f for f in os.listdir(CR_CORE) if f.endswith(".json"))
    with ThreadPoolExecutor(max_workers=32) as ex:
        recs = [r for r in ex.map(_load, files) if r]
    rows = []
    seen = set()
    for rec in recs:
        meta = rec.get("_meta") or {}
        ps = rec.get("parametric_specs") or {}
        ov = rec.get("overview") or {}
        parts = []
        if isinstance(ov, dict):
            pn = ov.get("part_numbers")
            if isinstance(pn, list):
                parts.extend(str(p) for p in pn if p)
        for candidate in (meta.get("part_number"), ps.get("part_number") if isinstance(ps, dict) else None):
            if candidate and candidate not in parts:
                parts.append(str(candidate))
        if not parts:
            continue
        claims = _flatten_claims(rec, meta)
        vendor = meta.get("canonical_brand_domain") or meta.get("vendor") or "unknown"
        aisle = meta.get("aisle") or "unassigned"
        for pn in parts:
            if pn in seen:
                continue
            seen.add(pn)
            rows.append({
                "part_number": pn,
                "vendor": vendor,
                "aisle": aisle,
                "claims": claims,
            })
    body = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    (root / "parts_cr_core.jsonl").write_text(body)
    return {"records": len(recs), "rows": len(rows), "bytes": len(body), "sha256": sha(body)}


def regen_cube(root: Path) -> dict:
    """Rebuild cube.json cells from every parts_*.jsonl feed present."""
    cells: dict[tuple, dict] = {}
    for feed in sorted(root.glob("parts_*.jsonl")):
        for line in feed.read_text().splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            vendor = row.get("vendor") or "unknown"
            if feed.name == "parts_cr_core.jsonl":
                key = (row.get("aisle") or "unassigned", "cr-core", vendor)
            elif feed.name == "parts_family.jsonl":
                key = ("mcu", "family-documents", vendor)
            elif feed.name == "parts_burn.jsonl":
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
    body = json.dumps(cube, indent=1, sort_keys=True)
    (root / "cube.json").write_text(body)
    return {"cells": len(cube["cells"]), "sha256": sha(body)}


RESULTS_DIR = Path(__file__).resolve().parents[1] / "results"


def fold_burn_multi(root: Path, burn_dirs: list) -> dict:
    """Fold every burn output dir into one parts_burn.jsonl."""
    rows = []
    for burn_dir in burn_dirs:
        rows.extend(_burn_rows(burn_dir))
    body = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    (root / "parts_burn.jsonl").write_text(body)
    return {"dirs": [str(d) for d in burn_dirs], "rows": len(rows), "bytes": len(body), "sha256": sha(body)}


def _burn_rows(burn_dir: Path) -> list:
    """Burn outputs -> parts_burn.jsonl. The per-doc claim_data rows carry
    quote + row + column provenance (the burn prompt mandates it); the
    source_pdf is the pdf stem by construction (`f"{p.stem}.json"`)."""
    if not burn_dir.is_dir():
        return []
    rows = []
    for f in sorted(burn_dir.glob("*.json")):
        if f.name.startswith("_"):
            continue
        try:
            rec = json.loads(f.read_text())
        except Exception:
            continue
        stem = rec.get("stem") or f.stem
        if "-" in stem:
            vendor, part = stem.split("-", 1)
        else:
            vendor, part = "unknown", stem
        claims = []
        for c in rec.get("claim_data") or []:
            claims.append({
                "field": str(c.get("symbol") or "unknown")[:64],
                "value": "" if c.get("value") is None else str(c.get("value")),
                "quote": c.get("quote"),
                "source": {
                    "column_header": c.get("column_header"),
                    "crop": None,
                    "page": None,
                    "row_header": c.get("row_header"),
                    "sha": None,
                    "doc_sha256": None,
                    "source_pdf": f"{stem}.pdf",
                    "provenance_tier": None,
                },
            })
        rows.append({"part_number": part, "vendor": vendor, "claims": claims})
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(DEFAULT_ROOT))
    ap.add_argument("--burn-dir", default="", nargs="*")
    args = ap.parse_args()
    root = Path(args.root)
    root.mkdir(parents=True, exist_ok=True)
    cr = fold_cr_core(root)
    burn_dirs = [Path(d) for d in args.burn_dir] if args.burn_dir else sorted(
        d for d in RESULTS_DIR.glob("burn-power-*-local") if d.is_dir()
    )
    burn = fold_burn_multi(root, burn_dirs)
    cube = regen_cube(root)
    files = {}
    for f in ("parts_catalog.jsonl", "parts_family.jsonl", "parts_cr_core.jsonl", "parts_burn.jsonl", "cube.json"):
        p = root / f
        if p.exists():
            files[f] = {"sha256": sha(p.read_text()), "bytes": p.stat().st_size}
    manifest = {"fold": "strata->served", "cr_core": cr, "burn": burn, "cube": cube, "files": files}
    (root / "fold_manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True))
    print(json.dumps(manifest, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
