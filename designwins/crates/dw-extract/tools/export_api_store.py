#!/usr/bin/env python3
"""Export the existing extracted corpus into ApiStore wire shapes.

The migration test is the PIPELINE: raw -> extracted -> processed -> API ->
front end. The API seam is `dw_api::store::ApiStore`; this tool produces the
data half — the corpus we already have, in the exact wire shapes the store
serves:

  parts_catalog.jsonl   1,881 parts x 86,736 claims (catalog.db, quote+page)
  parts_family.jsonl    538 family-document records via the live reference
                        grid (key_features_grid.build_grid) — the MCU lane
  cube.json             category x aisle x vendor cells (drill-down spine)

D8 discipline: the family feed is produced by running the LIVE reference
(build_grid), not by re-deriving what it emits. The catalog feed is a pure
projection of stored claims.

Page fold (r2): claims_canonical.provenance.page measures 0/86,736 — the
located pages live in the claim_pages evidence chain (86,398 located). This
tool folds that chain in so the evidence viewer has pages for the catalog
lane too. Join key includes doc_sha256: the same quote in two documents
resolves to different pages (measured: 0 disagreements on the 9-tuple, 30 on
the 8-tuple). Rows the chain cannot place keep `page: null` with `match`
naming why — honest absence, never an invented page.

Output: yosemiteraid5 Public/m5-opencode/api-store-export/ (data side of the
seam; the dw-api loader reads it behind `ApiStore`).

Usage: python3 designwins/crates/dw-extract/tools/export_api_store.py
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path

REPO = Path("/Users/samkim/Harnessv1")
sys.path.insert(0, str(REPO))

OUT = Path("/Users/samkim/mnt/yosemiteraid5-nfs/m5-opencode/api-store-export")
CATALOG = Path("/Users/samkim/mnt/yosemiteraid5-nfs/m5-opencode/scratch/catalog.db")
RECORDS = REPO / "results" / "family-documents-cr-20260908" / "records.jsonl"
CLAIM_PAGES = REPO / "tests" / "fixtures" / "gold" / "claim_pages_v1.jsonl"


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def load_page_chain() -> dict:
    """claim_pages gold: content key -> (page, match). See module docstring."""
    chain = {}
    for line in CLAIM_PAGES.open():
        r = json.loads(line)
        key = (
            r["opn"], r["symbol"], r["quote"], r["row_header"], r["column_header"],
            r["qualifier"], r["condition_norm"], r["value_text"], r["doc_sha256"],
        )
        chain[key] = (r.get("page"), r.get("match"))
    return chain


def export_catalog(chain: dict) -> tuple[list, dict]:
    """claims_canonical -> PartRecord wire shape (dw_api::store::PartRecord)."""
    db = sqlite3.connect(f"file:{CATALOG}?mode=ro&immutable=1", uri=True)
    by_opn: dict[str, dict] = {}
    folded = {"located": 0, "absent": 0, "unjoined": 0}
    for opn, symbol, qualifier, condition, unit, value_text, provenance, doc_sha in db.execute(
        "SELECT opn, symbol, qualifier, condition_norm, unit, value_text, provenance, doc_sha256"
        " FROM claims_canonical"
    ):
        try:
            prov = json.loads(provenance or "{}")
        except Exception:
            prov = {}
        field = (symbol or "unknown").lower()
        if qualifier:
            field = f"{field}_{qualifier.lower()}"
        if condition:
            field = f"{field}_{condition.lower().replace(' ', '_').replace('=', '').replace(',', '')}"
        entry = by_opn.setdefault(opn, {"part_number": opn, "vendor": "unknown", "claims": []})
        key = (
            opn, symbol, prov.get("quote"), prov.get("row_header"), prov.get("column_header"),
            qualifier, condition, value_text, doc_sha,
        )
        if key in chain:
            page, match = chain[key]
        else:
            page, match = (prov.get("page"), "unjoined")
            folded["unjoined"] += 1
        if page is not None and page != 0:
            folded["located"] += 1
        else:
            folded["absent"] += 1
        entry["claims"].append(
            {
                "field": field[:64],
                "value": value_text if value_text is not None else "",
                "quote": prov.get("quote"),
                "source": {
                    "column_header": prov.get("column_header"),
                    "crop": None,
                    "page": page,
                    "row_header": prov.get("row_header"),
                    "sha": prov.get("sha"),
                    "doc_sha256": doc_sha,
                    "match": match,
                },
            }
        )
    for opn, vendor in db.execute("SELECT opn, vendor FROM parts"):
        if opn in by_opn:
            by_opn[opn]["vendor"] = vendor or "unknown"
    print(
        f"catalog page fold: {folded['located']} located / {folded['absent']} absent"
        f" (unjoined: {folded['unjoined']} — honest absence, match named)"
    )
    parts = sorted(by_opn.values(), key=lambda p: p["part_number"])
    cells: dict[tuple, dict] = {}
    for p in parts:
        key = ("power/semiconductor", "catalog", p["vendor"])
        cell = cells.setdefault(
            key,
            {"category": key[0], "aisle": key[1], "vendor": key[2], "parts": 0, "claims": 0, "laned": True, "curves": False},
        )
        cell["parts"] += 1
        cell["claims"] += len(p["claims"])
    return parts, cells


def export_family() -> tuple[list, dict]:
    """family-document records -> PartRecord via the LIVE reference grid."""
    from harness.electronics.key_features_grid import build_grid

    parts = []
    cells: dict[tuple, dict] = {}
    for line in RECORDS.open():
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        if "error" in rec:
            continue
        meta = rec.get("_meta", {})
        ident = rec.get("identity", {})
        doc_id = ident.get("document_id") or Path(str(meta.get("source_path", "doc"))).stem
        vendor = meta.get("vendor") or "unknown"
        grid = build_grid(rec)
        claims = []
        for row in grid.get("rows", []):
            if row.get("tier") != "grid":
                continue
            claims.append(
                {
                    "field": (row.get("label") or "feature")[:64],
                    "value": "" if row.get("value") is None else str(row.get("value")),
                    "quote": (row.get("verbatim") or "")[:200] or None,
                    "source": {
                        "column_header": None,
                        "crop": None,
                        "page": (row.get("source_pages") or [None])[0],
                        "row_header": row.get("group"),
                        "sha": sha(row.get("verbatim") or ""),
                        "doc_sha256": meta.get("document_sha256"),
                        "match": "family-grid",
                    },
                }
            )
        parts.append({"part_number": doc_id, "vendor": vendor, "claims": claims})
        key = ("mcu", "family-documents", vendor)
        cell = cells.setdefault(
            key,
            {"category": key[0], "aisle": key[1], "vendor": key[2], "parts": 0, "claims": 0, "laned": True, "curves": False},
        )
        cell["parts"] += 1
        cell["claims"] += len(claims)
    return sorted(parts, key=lambda p: p["part_number"]), cells


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    chain = load_page_chain()
    cat_parts, cat_cells = export_catalog(chain)
    fam_parts, fam_cells = export_family()

    def write_jsonl(name, rows):
        body = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
        (OUT / name).write_text(body)
        print(f"{name}: {len(rows)} rows, {len(body)} bytes, sha {sha(body)[:16]}")

    write_jsonl("parts_catalog.jsonl", cat_parts)
    write_jsonl("parts_family.jsonl", fam_parts)

    cells = list(cat_cells.values()) + list(fam_cells.values())
    cube = {
        "dimensions": ["category", "aisle", "vendor"],
        "cells": sorted(cells, key=lambda c: (c["category"], c["aisle"], c["vendor"])),
        "laned": True,
    }
    body = json.dumps(cube, indent=1, sort_keys=True)
    (OUT / "cube.json").write_text(body)
    print(f"cube.json: {len(cells)} cells, sha {sha(body)[:16]}")
    for c in cube["cells"]:
        print(f"  {c['category']} · {c['aisle']} · {c['vendor']}: {c['parts']} parts, {c['claims']} claims")

    total_parts = len(cat_parts) + len(fam_parts)
    total_claims = sum(len(p["claims"]) for p in cat_parts + fam_parts)
    located = sum(
        1
        for p in cat_parts + fam_parts
        for c in p["claims"]
        if c["source"]["page"] not in (None, 0)
    )
    manifest = {
        "page_chain": "tests/fixtures/gold/claim_pages_v1.jsonl (86,398/86,736 located)",
        "parts_total": total_parts,
        "claims_total": total_claims,
        "claims_with_page": located,
        "catalog_parts": len(cat_parts),
        "family_parts": len(fam_parts),
        "files": {
            f: {"sha256": sha((OUT / f).read_text())}
            for f in ("parts_catalog.jsonl", "parts_family.jsonl", "cube.json")
        },
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True))
    print(json.dumps({k: v for k, v in manifest.items() if k != "files"}, indent=1))
    print("export:", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
