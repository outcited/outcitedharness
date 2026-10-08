#!/usr/bin/env python3
"""Build LoRA v1 training pairs from validated extraction grids + source PDFs.

Pairs: (datasheet text pages, extraction instruction) -> compact parametric rows JSON.
Dedupes re-run datasets by document_sha256. Output: llama-factory messages jsonl.
"""

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path

import pymupdf

REPO = Path(__file__).resolve().parents[1]
DEFAULT_DATASETS = sorted(REPO.glob("results/power-grids-20260909*")) + [
    REPO / "results/key-features-grid-20260908"
]
DEFAULT_PDF_ROOTS = sorted(Path("/Volumes/M5_4TB/exports/power-datasheet-pairs").glob("*/pdf"))
MAX_PAGES = 12
MAX_CHARS = 40000

SYSTEM = (
    "You are a datasheet parametric extraction engine. Given datasheet pages, "
    "extract the parametric table rows for the requested part as compact JSON: "
    '[{"symbol": str, "value": number, "unit": str, "qualifier": str|null, '
    '"condition": str|null}]. Use only values printed in the document.'
)


def sha256(path, buf=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(buf):
            h.update(chunk)
    return h.hexdigest()


def index_pdfs(roots):
    by_name = {}
    for root in roots:
        if not root.is_dir():
            continue
        for pdf in root.glob("*.pdf"):
            by_name[pdf.name] = pdf
    return by_name


def load_rows(datasets):
    by_doc = defaultdict(lambda: {"meta": None, "rows": []})
    for ds in datasets:
        rows_file = ds / "grid_rows.jsonl"
        if not rows_file.exists():
            continue
        with rows_file.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                sha = row.get("document_sha256")
                if not sha:
                    continue
                entry = by_doc[sha]
                if entry["meta"] is None:
                    entry["meta"] = {
                        "artifact": row.get("source_artifact"),
                        "vendor": row.get("vendor"),
                        "part": row.get("scope_as_printed"),
                        "dataset": ds.name,
                    }
                entry["rows"].append(
                    {
                        "symbol": row.get("symbol"),
                        "value": row.get("value"),
                        "unit": row.get("unit"),
                        "qualifier": row.get("quantity_qualifier"),
                        "condition": row.get("condition_verbatim") or row.get("table_condition"),
                    }
                )
    return by_doc


def pdf_text(path):
    doc = pymupdf.open(path)
    parts = []
    total = 0
    for i, page in enumerate(doc):
        if i >= MAX_PAGES or total >= MAX_CHARS:
            break
        text = page.get_text("text")
        parts.append(text)
        total += len(text)
    doc.close()
    return "\n".join(parts)[:MAX_CHARS]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="results/lora-v1-pairs/power-tables-v1.jsonl")
    ap.add_argument("--pdf-root", action="append", default=[])
    args = ap.parse_args()

    roots = [Path(p) for p in args.pdf_root] or DEFAULT_PDF_ROOTS
    by_name = index_pdfs(roots)
    by_doc = load_rows(DEFAULT_DATASETS)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    written = unresolved = empty = 0
    with out.open("w") as f:
        for sha, entry in sorted(by_doc.items()):
            rows = [r for r in entry["rows"] if r.get("symbol") and r.get("value") is not None]
            if not rows:
                empty += 1
                continue
            artifact = entry["meta"]["artifact"]
            pdf = by_name.get(artifact)
            if pdf is None:
                unresolved += 1
                continue
            if sha256(pdf) != sha:
                unresolved += 1
                continue
            text = pdf_text(pdf)
            if len(text.strip()) < 200:
                unresolved += 1
                continue
            user = (
                f"Part: {entry['meta']['part']}\nVendor: {entry['meta']['vendor']}\n\n"
                f"Datasheet pages:\n{text}"
            )
            rec = {
                "messages": [
                    {"role": "system", "content": SYSTEM},
                    {"role": "user", "content": user},
                    {"role": "assistant", "content": json.dumps(rows, ensure_ascii=False)},
                ],
                "meta": {**entry["meta"], "document_sha256": sha, "n_rows": len(rows)},
            }
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            written += 1

    print(
        json.dumps(
            {
                "distinct_documents": len(by_doc),
                "pairs_written": written,
                "pdf_unresolved_or_sha_mismatch": unresolved,
                "docs_without_rows": empty,
                "out": str(out),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
