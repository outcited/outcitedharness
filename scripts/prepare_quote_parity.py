#!/usr/bin/env python3
"""Prepare parity cases for the `quote_verify` replacement gate.

`dw_truth::verify_claims` and `harness/pipeline/quote_verify.py::verify_claims`
both answer the same question — *is this claim's evidence real?* — and they
both need two inputs: a set of claims and the document text they came from.

This builds that pairing once, from the corpus we already have:

  claim_provenance_v2.jsonl   86,736 claims with quote + doc_sha256 + page
  vault/cas/{sha[..2]}/{sha}.pdf   the source documents

One case per document: its full page text and its claims. Both engines then
read the same case file, so neither can be looking at different evidence.

Output::

    tests/fixtures/gold/quote_parity/cases/{doc_sha256}.json
    tests/fixtures/gold/quote_parity/quote_parity_cases.jsonl

Usage::

    python3 scripts/prepare_quote_parity.py --docs 40
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib

import fitz

REPO = pathlib.Path(__file__).resolve().parent.parent
CLAIMS = REPO / "tests" / "fixtures" / "gold" / "claim_provenance_v2.jsonl"
OUT_DIR = REPO / "tests" / "fixtures" / "gold" / "quote_parity"
CASES = OUT_DIR / "cases"
INDEX = OUT_DIR / "quote_parity_cases.jsonl"
VAULT = pathlib.Path("/Volumes/M5_4TB/vault/cas")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--docs", type=int, default=40,
                    help="how many source documents to prepare (claims follow)")
    args = ap.parse_args()

    by_doc: dict[str, list[dict]] = collections.defaultdict(list)
    with CLAIMS.open() as handle:
        for line in handle:
            if line.strip():
                row = json.loads(line)
                by_doc[row["doc_sha256"]].append(row)

    # Largest claim groups first: the most evidence per document extracted.
    ranked = sorted(by_doc.items(), key=lambda kv: -len(kv[1]))
    print(f"corpus: {len(by_doc)} docs, {sum(len(v) for v in by_doc.values())} claims")

    CASES.mkdir(parents=True, exist_ok=True)
    index = []
    prepared = skipped = 0

    for sha, group in ranked:
        if prepared >= args.docs:
            break
        path = VAULT / sha[:2] / f"{sha}.pdf"
        try:
            doc = fitz.open(path)
            text = "\n".join(p.get_text() for p in doc)
            doc.close()
        except Exception:
            skipped += 1
            continue

        claims = []
        for r in group:
            claims.append({
                "field": f"{r.get('opn')}:{r.get('symbol')}",
                "claim": {
                    "value": r.get("value_text") or r.get("value"),
                    "quote": r.get("quote") or "",
                    "table": r.get("table") or "",
                    "column_header": r.get("column_header"),
                    "row_header": r.get("row_header"),
                    "qualifier": r.get("qualifier"),
                },
            })

        case = {"doc_sha256": sha, "doc_text": text, "claims": claims}
        case_path = CASES / f"{sha}.json"
        case_path.write_text(json.dumps(case, ensure_ascii=False))
        index.append({
            "doc_sha256": sha,
            "case_path": str(case_path.relative_to(OUT_DIR)),
            "claim_count": len(claims),
            "text_chars": len(text),
        })
        prepared += 1

    with INDEX.open("w") as handle:
        for row in index:
            handle.write(json.dumps(row, sort_keys=True) + "\n")

    print(f"wrote {INDEX}")
    print(f"  cases prepared  {prepared}")
    print(f"  docs skipped    {skipped}")
    print(f"  claims covered  {sum(r['claim_count'] for r in index)}")
    print(f"  text chars      {sum(r['text_chars'] for r in index):,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
