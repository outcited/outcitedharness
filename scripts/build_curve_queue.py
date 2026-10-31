#!/usr/bin/env python3
"""curve-queue: collect typical-characteristics pages across a document queue.

Two input modes, both deterministic, both sha-pinned, one work-item schema:

``--queue``     Document queue: TOC scan — every page under a section heading
                that classifies into the ``typical_characteristics`` lane
                becomes a work item (one per page). Documents whose plots
                live only in figure captions (no TOC entry) are skipped
                unless ``--caption-fallback`` — fill-only, never guess a page.

``--page-evidence``  Page-evidence bundle root (``page-evidence.jsonl`` from
                ``extract_datasheet_page_evidence.py``): every row whose
                ``lanes`` contain ``typical_characteristics`` becomes a work
                item. No PDF access, no re-scan — the page selection was
                already decided when the evidence was extracted; this mode
                only re-plays the recorded lane decision.
"""

from __future__ import annotations

import argparse
import hashlib
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

from harness.electronics.page_index import classify_section  # noqa: E402
from harness.electronics.typical_curves import (  # noqa: E402
    _line_anchors,
    plot_regions_for_page,
)

_CAPTION_CONTENT = re.compile(
    r"typical|characteristic|current|voltage|frequency|consumption|"
    r"\bI\s?DD\b|\bIDD\b|\bV\s?DD\b|VCC|vs\.?\s|versus",
    re.I,
)


def _caption_curve_pages(doc) -> list[int]:
    """Pages whose figure captions carry plot content (TOC-less fallback).

    Two gates, both deterministic: a caption line with plot-content words,
    and a vector plot frame on the page (the same drawings detector the
    extractor regions use). Caption-only pages (schematics, timing text,
    manual figures) measured 12% precision alone; the frame gate carries
    the precision.
    """

    pages = []
    for index in range(doc.page_count):
        try:
            page = doc[index]
            words = page.get_text("words")
        except Exception:  # pragma: no cover - PyMuPDF internals
            continue
        if len(words) > 1200:
            continue
        captions = _line_anchors(
            [
                (float(w[0]), float(w[1]), float(w[2]), float(w[3]), str(w[4]))
                for w in words
            ]
        )
        if not any(_CAPTION_CONTENT.search(c[4]) for c in captions):
            continue
        if any(r["source"] == "drawings" for r in plot_regions_for_page(page)):
            pages.append(index + 1)
    return pages


def _queue_documents(path: Path) -> list[dict]:
    payload = json.loads(path.read_text())
    if isinstance(payload, dict):
        items = payload.get("documents") or payload.get("work") or payload.get("parts")
    else:
        items = payload
    out = []
    for item in items or []:
        source = item.get("source_path") or item.get("pdf_path") or item.get("path")
        if not source:
            continue
        out.append(
            {
                "source_path": str(source),
                "document_sha256": item.get("document_sha256") or item.get("sha256"),
            }
        )
    return out


def _jsonl(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _evidence_curve_pages(root: Path) -> list[dict]:
    """Curve work items replayed from a page-evidence bundle.

    Fill-only: a page enters the queue only if its recorded ``lanes``
    already contain ``typical_characteristics``. Rows that fail integrity
    checks are tallied, never guessed into the queue.
    """

    rows = _jsonl(root / "page-evidence.jsonl")
    out = []
    for row in rows:
        if "typical_characteristics" not in (row.get("lanes") or []):
            continue
        out.append(
            {
                "source_path": str(row["source_path"]),
                "document_sha256": str(row["document_sha256"]),
                "page_1based": int(row["page_1based"]),
            }
        )
    return out


def _toc_curve_pages(item: dict, caption_fallback: bool) -> dict:
    pdf = Path(item["source_path"])
    try:
        doc = pymupdf.open(pdf)
        try:
            pages = []
            for level, title, page in doc.get_toc() or []:
                if not title or page < 1 or page > doc.page_count:
                    continue
                if "typical_characteristics" in classify_section(str(title)):
                    pages.append(page)
            if caption_fallback and not pages:
                pages = _caption_curve_pages(doc)
            return {"pages": sorted(set(pages)), "count": doc.page_count, "error": None}
        finally:
            doc.close()
    except Exception as exc:
        return {"pages": [], "count": 0, "error": type(exc).__name__}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--queue",
        type=Path,
        help="Document queue JSON ({documents|work|parts: [...]})",
    )
    group.add_argument(
        "--page-evidence",
        type=Path,
        help=(
            "Page-evidence bundle root containing page-evidence.jsonl; "
            "selects rows whose lanes include typical_characteristics"
        ),
    )
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument(
        "--caption-fallback",
        action="store_true",
        help="TOC-less docs: qualify pages via plot-content figure captions",
    )
    args = ap.parse_args()
    if args.page_evidence and (args.caption_fallback or args.workers != 8):
        ap.error(
            "--caption-fallback/--workers apply only to --queue mode "
            "(evidence mode re-plays recorded lane decisions, no scan)"
        )

    work = []
    tally: Counter[str] = Counter()
    if args.page_evidence:
        root = args.page_evidence.expanduser().resolve(strict=True)
        items = _evidence_curve_pages(root)
        tally["evidence_rows_typical_characteristics"] = len(items)
    else:
        documents = _queue_documents(args.queue)
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            for item, result in zip(
                documents,
                pool.map(
                    _toc_curve_pages,
                    documents,
                    [args.caption_fallback] * len(documents),
                ),
            ):
                if result["error"]:
                    tally[f"error:{result['error']}"] += 1
                    continue
                if not result["pages"]:
                    tally["docs_without_curve_toc"] += 1
                    continue
                tally["docs_with_curve_pages"] += 1
                for page in result["pages"]:
                    work.append((item, page))
        items = []
        for item, page in work:
            items.append(
                {
                    "source_path": item["source_path"],
                    "document_sha256": item["document_sha256"],
                    "page_1based": page,
                }
            )

    sha_cache: dict[str, str] = {}
    for item in items:
        sha = item["document_sha256"]
        if not sha:
            pdf = Path(item["source_path"])
            if pdf not in sha_cache:
                sha_cache[pdf] = hashlib.sha256(pdf.read_bytes()).hexdigest()
            sha = sha_cache[pdf]
        work.append(
            {
                "schema": "harness.electronics-local-model-work.v1",
                "work_id": "curve-"
                + hashlib.sha256(f"{sha}:{item['page_1based']}".encode()).hexdigest()[:32],
                "document_sha256": sha,
                "source_path": item["source_path"],
                "page_1based": item["page_1based"],
                "capability": "typical_characteristics",
                "partition": "factory_candidate",
            }
        )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"work": work}, ensure_ascii=False, sort_keys=True))
    print(json.dumps({"work": len(work), **dict(sorted(tally.items()))}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
