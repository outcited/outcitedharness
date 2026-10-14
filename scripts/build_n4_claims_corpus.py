#!/usr/bin/env python3
"""Build the Gate N4 claim-parity corpus.

Gate N4 = the 87K claim diff: run the Python reference (`verify_claims`) and the
Rust candidate (`dw_truth::verify_claims`) over every claim in the live catalog
and prove zero unexplained drift (D2 cutover bar).

This builder emits two artifacts consumed by both engines identically:

  claims.jsonl   one line per claim:
                 {id, doc_sha256, field, claim:{value,quote,table,
                  column_header,row_header,qualifier}}
  doctext.json   {sha256: "<full document text>"} for every referenced doc,
                 extracted from the vault PDF (content-addressed by sha).

The subject passed to both engines is the claim `id` (a stable string), so
neither engine can be looking at different evidence — the whole point of the
replacement gate.

Usage:
    python3 scripts/build_n4_claims_corpus.py \
        --catalog results/catalog.db \
        --vault /Volumes/M5_4TB/vault \
        --out-dir results/n4-claims-20261013
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _vault_path(vault: Path, sha: str) -> Path | None:
    for base in ("cas", "landing"):
        for sub in (f"{sha[:2]}/{sha[2:4]}", sha[:2]):
            p = vault / base / sub / f"{sha}.pdf"
            if p.exists():
                return p
    return None


def _doc_text(vault: Path, sha: str) -> str | None:
    import pymupdf
    p = _vault_path(vault, sha)
    if p is None:
        return None
    try:
        doc = pymupdf.open(p)
        try:
            return "\n".join(page.get_text() for page in doc)
        finally:
            doc.close()
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--catalog", type=Path, default=ROOT / "results/catalog.db")
    ap.add_argument("--vault", type=Path, default=Path("/Volumes/M5_4TB/vault"))
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(f"file:{args.catalog}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row

    rows = con.execute(
        "SELECT id, opn, symbol, value, unit, value_text, provenance, doc_sha256 "
        "FROM claims_canonical ORDER BY id"
    ).fetchall()
    print(f"claims: {len(rows)}", flush=True)

    claims_out = args.out_dir / "claims.jsonl"
    shas: set[str] = set()
    with claims_out.open("w") as fh:
        for r in rows:
            try:
                prov = json.loads(r["provenance"] or "{}")
            except Exception:
                prov = {}
            claim = {
                "value": prov.get("value", r["value_text"] or r["value"]),
                "quote": prov.get("quote") or "",
                "table": prov.get("table") or "",
                "column_header": prov.get("column_header"),
                "row_header": prov.get("row_header"),
                "qualifier": prov.get("qualifier"),
            }
            rec = {
                "id": str(r["id"]),
                "doc_sha256": r["doc_sha256"],
                "field": r["symbol"],
                "claim": claim,
            }
            fh.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
            if r["doc_sha256"] and not r["doc_sha256"].startswith("unhashed:"):
                shas.add(r["doc_sha256"])

    print(f"distinct docs to extract: {len(shas)}", flush=True)
    doctext: dict[str, str] = {}

    # pymupdf is not thread-safe across a large concurrent pool (it segfaults
    # the whole process on some inputs). Extract sequentially — 1.6k docs is a
    # few minutes and the gate corpus is built once.
    for i, sha in enumerate(sorted(shas)):
        t = _doc_text(args.vault, sha)
        if t is not None:
            doctext[sha] = t
        if (i + 1) % 200 == 0:
            print(f"  extracted {i+1}/{len(shas)}", flush=True)

    (args.out_dir / "doctext.json").write_text(json.dumps(doctext, ensure_ascii=False))
    print(f"doc text extracted: {len(doctext)}/{len(shas)} -> {args.out_dir/'doctext.json'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())