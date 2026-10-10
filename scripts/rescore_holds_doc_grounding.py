#!/usr/bin/env python3
"""Rescore hold_ungrounded_axis rows with DOCUMENT-level grounding.

Page-level grounding fails on raster figures whose axis labels are baked
into the image (no text-layer counterpart on that page). But the labels
(tPW, VDROP, etc.) are printed elsewhere in the same document — timing
tables, EC tables. Document-level grounding is still T1: every label
verified as printed in the same sha-pinned document.

No API calls. Reuses existing payloads. Flips verdict to "extracted"
when every previously-ungrounded label grounds at document scope.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PAGE_CAP = 3000


def _doc_words(pdf_path: str) -> tuple[set[str], str]:
    try:
        doc = pymupdf.open(pdf_path)
    except Exception:
        return set(), ""
    normalized: list[str] = []
    try:
        for i, page in enumerate(doc):
            if i >= PAGE_CAP:
                break
            for w in page.get_text("words"):
                n = re.sub(r"[^a-z0-9]", "", str(w[4]).lower())
                if n:
                    normalized.append(n)
    finally:
        doc.close()
    return set(normalized), "".join(normalized)


def _present(key: str, haystack: set[str], passage: str) -> bool:
    return not key or key in haystack or key in passage


def _label_key(text) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


def rescore_rows(args: tuple[str, list[dict]]) -> list[dict]:
    pdf_path, rows = args
    haystack, passage = _doc_words(pdf_path)
    out = []
    if not haystack:
        for r in rows:
            out.append({**r, "rescore": "doc_unreadable"})
        return out
    for r in rows:
        payload = r.get("payload") or {}
        axes = payload.get("axes") or {}
        missing = []
        for side in ("x", "y"):
            label = (axes.get(side) or {}).get("label")
            if label and not _present(_label_key(label), haystack, passage):
                missing.append(f"axis_{side}:{label}")
        for i, s in enumerate(payload.get("series") or []):
            name = (s or {}).get("name")
            if name and not _present(_label_key(name), haystack, passage):
                missing.append(f"series[{i}]:{name}")
        row = dict(r)
        if not missing:
            row["verdict"] = "extracted"
            row["rescore"] = "doc_grounded"
            row["grounding_scope"] = "document"
            row["ungrounded_labels"] = []
        else:
            row["rescore"] = "still_ungrounded"
            row["ungrounded_labels"] = missing
        out.append(row)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--holds", type=Path, required=True,
                    help="JSON list of hold rows (verdict hold_ungrounded_axis)")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()

    holds = json.loads(args.holds.read_text())
    holds = [h for h in holds if h.get("verdict") == "hold_ungrounded_axis"]

    by_doc: dict[str, list[dict]] = {}
    for h in holds:
        by_doc.setdefault(h["source_path"], []).append(h)

    tally: Counter[str] = Counter()
    rescued = []
    items = [(p, rows) for p, rows in by_doc.items() if Path(p).exists()]
    tally["docs_missing"] = len(by_doc) - len(items)

    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for doc_rows in pool.map(rescore_rows, items):
            for r in doc_rows:
                tally[r["rescore"]] += 1
                if r["rescore"] == "doc_grounded":
                    rescued.append(r)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rescued)
    )
    print(json.dumps({"holds_in": len(holds), "docs": len(by_doc),
                      "rescued": len(rescued), **dict(sorted(tally.items()))}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
