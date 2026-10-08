#!/usr/bin/env python3
"""toc-census: which section titles does no lane read?

Deterministic one-pass over a document sample: every TOC title is run
through the page-index lane classifier; unmatched titles are the unlaned
surface — the answer to 'what else is in these PDFs that we do not
extract'. Reports frequency so new-lane candidates are ranked by corpus
coverage, not anecdotes.
"""

from __future__ import annotations

import argparse
import json
import random
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pymupdf

import sys
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harness.electronics.page_index import classify_section  # noqa: E402


def toc_titles(path: str) -> list[str]:
    try:
        doc = pymupdf.open(path)
        titles = [str(t) for _, t, p in (doc.get_toc() or []) if t]
        doc.close()
        return titles
    except Exception:
        return []


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--delivery", type=Path, action="append", default=[],
                    help="corpus-delivery manifest JSONL (repeated)")
    ap.add_argument("--sample", type=int, default=2400)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    paths: list[str] = []
    for manifest in args.delivery:
        root = manifest.parent
        for line in manifest.read_text().splitlines():
            row = json.loads(line)
            ref = row.get("vault_ref")
            if ref and ref.endswith(".pdf"):
                paths.append(str(root / ref))
    random.seed(1)
    random.shuffle(paths)
    paths = paths[: args.sample]

    laned: Counter[str] = Counter()
    unlaned: Counter[str] = Counter()
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for titles in pool.map(toc_titles, paths):
            for title in titles:
                short = " ".join(title.split())[:60]
                if classify_section(title):
                    laned[short] += 1
                else:
                    unlaned[short] += 1
    report = {
        "docs_scanned": len(paths),
        "titles_laned": sum(laned.values()),
        "titles_unlaned": sum(unlaned.values()),
        "unlaned_top": unlaned.most_common(60),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=1))
    print(json.dumps({k: v for k, v in report.items() if k != "unlaned_top"}, indent=1))
    for title, count in report["unlaned_top"][:35]:
        print(f"{count:6d}  {title}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
