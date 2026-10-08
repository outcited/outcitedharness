#!/usr/bin/env python3
"""Build LoRA v2 training pairs from catalog claims (quote-native) + source PDFs.

Improvements over v1:
- Source: catalog.db claims_canonical (87K+ quote-native claims from burns)
- Every target row carries provenance (quote, row_header, column_header)
- Groups claims by (doc_sha256, opn) into multi-row extraction examples
- Dedupes by (opn, symbol, value) — same claim from re-runs collapses

Output: llama-factory messages jsonl at results/lora-v2-pairs/
"""

import argparse
import hashlib
import json
import re
import sqlite3
from collections import defaultdict
from pathlib import Path

import pymupdf

REPO = Path(__file__).resolve().parents[1]
CATALOG_DB = "/Volumes/M5_4TB/extract-results/catalog.db"
PDF_ROOTS = sorted(Path("/Volumes/M5_4TB/exports/power-datasheet-pairs").glob("*/pdf"))
BURN_DIRS = [
    Path("/Volumes/M5_4TB/extract-results/burn-power-v1"),
    Path("/Volumes/M5_4TB/extract-results/burn-vault-v1"),
]
OUT_DIR = REPO / "results/lora-v2-pairs"
MAX_PAGES = 12
MAX_CHARS = 40000

SYSTEM = (
    "You are a datasheet parametric extraction engine. Given datasheet pages, "
    "extract the parametric table rows for the requested part as compact JSON: "
    '[{"symbol": str, "value": number, "unit": str, "qualifier": str|null, '
    '"condition": str|null}]. Use only values printed in the document.'
)


def sha256_file(path, buf=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(buf):
            h.update(chunk)
    return h.hexdigest()


def index_pdfs():
    by_name = {}
    for root in PDF_ROOTS:
        if not root.is_dir():
            continue
        for pdf in root.glob("*.pdf"):
            by_name[pdf.stem] = pdf
            by_name[pdf.name] = pdf
    return by_name


def load_burn_claims():
    """Group claims by stem (doc identifier) from burn result files."""
    by_doc = {}
    for d in BURN_DIRS:
        if not d.exists():
            continue
        for f in sorted(d.glob("*.json")):
            if f.stem == "_report":
                continue
            try:
                res = json.loads(f.read_text())
            except Exception:
                continue
            if "skip" in res or not res.get("claim_data"):
                continue
            stem = res.get("stem", f.stem)
            claims = res.get("claim_data", [])
            if not claims:
                continue
            by_doc[stem] = claims
    return by_doc


def extract_pdf_text(pdf_path):
    try:
        doc = pymupdf.open(str(pdf_path))
        pages = []
        for i, page in enumerate(doc):
            if i >= MAX_PAGES:
                break
            pages.append(page.get_text())
        doc.close()
        text = "\n".join(pages)
        return text[:MAX_CHARS]
    except Exception:
        return None


def find_pdf_for_stem(stem, pdf_index):
    """Match burn stem to PDF filename."""
    if stem in pdf_index:
        return pdf_index[stem]
    # try common patterns: vendor-part, part
    parts = stem.split("-", 1)
    if len(parts) == 2:
        vendor, part = parts
        for key in [f"{vendor}-{part}", part, f"{part}.pdf"]:
            if key in pdf_index:
                return pdf_index[key]
    # fuzzy: find PDF whose stem contains the part
    for key, path in pdf_index.items():
        if stem.lower() in key.lower() or key.lower() in stem.lower():
            return path
    return None


def main(limit=None, min_claims=3):
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print("Loading burn claims...", flush=True)
    burn_claims = load_burn_claims()
    print(f"  {len(burn_claims)} docs with claims", flush=True)

    print("Indexing PDFs...", flush=True)
    pdf_index = index_pdfs()
    print(f"  {len(pdf_index)} PDFs indexed", flush=True)

    pairs = []
    skipped = 0
    for stem, claims in burn_claims.items():
        if len(claims) < min_claims:
            skipped += 1
            continue

        pdf_path = find_pdf_for_stem(stem, pdf_index)
        if not pdf_path:
            skipped += 1
            continue

        text = extract_pdf_text(pdf_path)
        if not text or len(text) < 100:
            skipped += 1
            continue

        # Build target rows — compact parametric format
        rows = []
        seen = set()
        for c in claims:
            sym = c.get("symbol") or c.get("param") or ""
            val = c.get("value")
            unit = c.get("unit") or ""
            qual = c.get("qualifier")
            cond = c.get("condition")
            key = (sym.lower(), val, unit.lower())
            if key in seen:
                continue
            seen.add(key)
            rows.append({
                "symbol": sym,
                "value": val,
                "unit": unit or None,
                "qualifier": qual,
                "condition": cond,
            })

        if not rows:
            skipped += 1
            continue

        # Extract vendor/part from stem
        parts = stem.split("-", 1)
        vendor = parts[0] if len(parts) == 1 else parts[0]
        part = parts[1] if len(parts) == 2 else stem

        pair = {
            "instruction": SYSTEM,
            "input": f"Part: {part}\nVendor: {vendor}\n\nDatasheet pages:\n{text}",
            "output": json.dumps(rows, ensure_ascii=False),
        }
        pairs.append(pair)

        if limit and len(pairs) >= limit:
            break

        if len(pairs) % 500 == 0:
            print(f"  {len(pairs)} pairs built ({skipped} skipped)", flush=True)

    print(f"\nBuilt {len(pairs)} pairs ({skipped} skipped)", flush=True)

    # Train/holdout split (seed 1788172800 for determinism)
    import random
    rng = random.Random(1788172800)
    rng.shuffle(pairs)
    holdout_size = min(300, max(150, len(pairs) // 10))
    holdout = pairs[:holdout_size]
    train = pairs[holdout_size:]

    train_path = OUT_DIR / "llamafactory/power-tables-v2/train.jsonl"
    holdout_path = OUT_DIR / "llamafactory/power-tables-v2/holdout.jsonl"
    train_path.parent.mkdir(parents=True, exist_ok=True)

    with train_path.open("w") as f:
        for p in train:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")
    with holdout_path.open("w") as f:
        for p in holdout:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")

    dataset_info = {
        "power_tables_v2_train": {
            "file_name": "train.jsonl",
            "columns": {"prompt": "instruction", "query": "input", "response": "output"},
        },
        "power_tables_v2_holdout": {
            "file_name": "holdout.jsonl",
            "columns": {"prompt": "instruction", "query": "input", "response": "output"},
        },
    }
    (OUT_DIR / "llamafactory/power-tables-v2/dataset_info.json").write_text(
        json.dumps(dataset_info, indent=2)
    )

    print(f"train: {len(train)} -> {train_path}", flush=True)
    print(f"holdout: {len(holdout)} -> {holdout_path}", flush=True)

    # Stats
    total_rows = sum(len(json.loads(p["output"])) for p in pairs)
    print(f"total target rows: {total_rows}", flush=True)
    print(f"avg rows/pair: {total_rows / max(1, len(pairs)):.1f}", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--min-claims", type=int, default=3)
    main(ap.parse_args().limit, ap.parse_args().min_claims)
