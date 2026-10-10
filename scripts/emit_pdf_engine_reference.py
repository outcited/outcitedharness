#!/usr/bin/env python3
"""Emit a path-segment reference fixture from the Python PDF engine.

This is the *reference* side of the D1 G7 gate (docs/DECISIONS.md).

The gold curve points in ``tests/fixtures/gold/*/`` are already in **data
space**, after axis calibration, so they cannot be compared to raw PDF geometry
directly. G7 is therefore **path-segment parity between engines**: if PDFium
reports the same segments PyMuPDF reports, the ported extractor can reproduce
the gold.

Corpus is the in-repo PDFs under ``tests/fixtures/gold/typical_curves/pdfs/``
so the gate is self-contained and does not depend on the NAS vault.

Output: ``tests/fixtures/gold/pdf_engine_segments_v1.json``

    {
      "schema": "harness.dw-pdf-path-segments.v1",
      "engine": {"name": "pymupdf", "version": "1.28.2"},
      "pages": [{
        "document": "msp430fr6005.pdf",
        "document_sha256": "...",
        "page_1based": 29,
        "page_width_pt": 612.0, "page_height_pt": 792.0,
        "drawings": [{"type": "s", "rect": [x0,y0,x1,y1],
                      "items": [["l", x0,y0,x1,y1], ...]}]
      }]
    }

Item kinds (MuPDF ``get_drawings()["items"]``)::

    ("l",  p0, p1)                    line        -> ["l", x0,y0, x1,y1]
    ("c",  p0, p1, p2, p3)            cubic       -> ["c", x0,y0, x1,y1, x2,y2, x3,y3]
    ("re", rect, orient)              rectangle   -> ["re", x0,y0, x1,y1]
    ("qu", quad)                      quad        -> ["qu", x0,y0, x1,y1]

Run from the repo root::

    python3 scripts/emit_pdf_engine_reference.py
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import fitz

REPO = Path(__file__).resolve().parent.parent
PDF_DIR = REPO / "tests" / "fixtures" / "gold" / "typical_curves" / "pdfs"
GOLD_DIR = REPO / "tests" / "fixtures" / "gold" / "typical_curves"
OUT = REPO / "tests" / "fixtures" / "gold" / "pdf_engine_segments_v1.json"
SCHEMA = "harness.dw-pdf-path-segments.v1"

KINDS = {"l", "c", "re", "qu", "cv"}


def r(v: float) -> float:
    return round(float(v), 4)


def pt(p) -> list[float]:
    return [r(p.x), r(p.y)]


def item_to_json(item) -> list | None:
    kind = item[0]
    if kind not in KINDS:
        return None
    out: list = [kind]
    for arg in item[1:]:
        if isinstance(arg, fitz.Point):
            out.extend(pt(arg))
        elif isinstance(arg, fitz.Rect):
            out.extend([r(arg.x0), r(arg.y0), r(arg.x1), r(arg.y1)])
        elif isinstance(arg, fitz.Quad):
            out.extend([r(arg.ul.x), r(arg.ul.y), r(arg.lr.x), r(arg.lr.y)])
        elif isinstance(arg, (int, float)):
            out.append(r(arg))
    return out


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def wanted_pages() -> dict[str, set[int]]:
    """Pages named by the gold curve fixtures, keyed by pdf filename."""
    out: dict[str, set[int]] = {}
    for gf in sorted(GOLD_DIR.glob("*_p*.json")):
        d = json.loads(gf.read_text())
        name = d["source_artifact"]
        out.setdefault(name, set()).add(int(d["page_1based"]))
    return out


def main() -> int:
    wanted = wanted_pages()
    if not wanted:
        print("no gold curve pages found", file=sys.stderr)
        return 1

    pages_out = []
    missing = []
    for name, pages in sorted(wanted.items()):
        path = PDF_DIR / name
        if not path.exists():
            missing.append(name)
            continue
        digest = sha256_file(path)
        doc = fitz.open(path)
        for page_1based in sorted(pages):
            page = doc[page_1based - 1]
            drawings = []
            for d in page.get_drawings():
                items = [it for it in (item_to_json(i) for i in d.get("items", [])) if it]
                drawings.append(
                    {
                        "type": d.get("type"),
                        "rect": [r(v) for v in d["rect"]],
                        "item_count": len(items),
                        "items": items,
                    }
                )
            pages_out.append(
                {
                    "document": name,
                    "document_sha256": digest,
                    "page_1based": page_1based,
                    "page_width_pt": r(page.rect.width),
                    "page_height_pt": r(page.rect.height),
                    "drawing_count": len(drawings),
                    "segment_count": sum(d["item_count"] for d in drawings),
                    "drawings": drawings,
                }
            )
        doc.close()

    payload = {
        "schema": SCHEMA,
        "engine": {"name": "pymupdf", "version": fitz.version[0]},
        "corpus": "tests/fixtures/gold/typical_curves/pdfs",
        "note": (
            "Reference side of D1 G7. Compares raw path segments, not gold "
            "curve points: the gold is in data space after axis calibration."
        ),
        "page_count": len(pages_out),
        "pages": pages_out,
    }
    OUT.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n")
    total_d = sum(p["drawing_count"] for p in pages_out)
    total_s = sum(p["segment_count"] for p in pages_out)
    print(f"wrote {OUT.relative_to(REPO)}  pages={len(pages_out)}  drawings={total_d}  segments={total_s}")
    for p in pages_out:
        print(f"  {p['document']} p{p['page_1based']}: drawings={p['drawing_count']} segments={p['segment_count']}")
    for name in missing:
        print(f"  MISSING: {name}", file=sys.stderr)
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
