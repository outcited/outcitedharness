#!/usr/bin/env python3
"""Table region detection for crop-don't-page vision rendering.

Three paths, in order:
  1. text-native pages: pymupdf find_tables() bboxes (free, exact)
  2. partial-text pages: column-gap heuristics over word positions
  3. image-only pages: full page (nothing to detect — the pillar map
     already flags these figure-only)
"""

import re
from pathlib import Path

import pymupdf

PAD = 20
MIN_REGION_H = 60
MIN_REGION_W = 120


def regions_for_page(page, page_text=""):
    """Return [{bbox: (x0,y0,x1,y1), source: 'tables'|'layout'|'fullpage'}]."""
    try:
        finder = page.find_tables()
        tables = list(finder.tables) if finder else []
    except Exception:
        tables = []
    out = []
    for t in tables:
        r = t.bbox
        if (r[2] - r[0]) < MIN_REGION_W or (r[3] - r[1]) < MIN_REGION_H:
            continue
        out.append({"bbox": pad_bbox(r, page.rect), "source": "tables"})
    if out:
        return out
    if len((page_text or "").strip()) > 200:
        return layout_regions(page, page_text)
    return [{"bbox": (0, 0, page.rect.width, page.rect.height), "source": "fullpage"}]


def pad_bbox(r, rect, pad=PAD):
    return (max(0, r[0] - pad), max(0, r[1] - pad),
            min(rect.width, r[2] + pad), min(rect.height, r[3] + pad))


def layout_regions(page, page_text, min_gap=30):
    """Column-gap heuristic: dense word rows split by whitespace bands become
    candidate regions. Coarse by design — better than full-page, worse than
    find_tables."""
    try:
        words = page.get_text("words")
    except Exception:
        return [{"bbox": (0, 0, page.rect.width, page.rect.height), "source": "fullpage"}]
    if len(words) < 20:
        return [{"bbox": (0, 0, page.rect.width, page.rect.height), "source": "fullpage"}]
    ys = sorted(w[1] for w in words)
    bands = []
    start = ys[0]
    prev = ys[0]
    for y in ys[1:]:
        if y - prev > min_gap:
            bands.append((start, prev))
            start = y
        prev = y
    bands.append((start, prev))
    out = []
    for y0, y1 in bands:
        if (y1 - y0) < MIN_REGION_H:
            continue
        out.append({"bbox": pad_bbox((30, y0, page.rect.width - 30, y1), page.rect),
                    "source": "layout"})
    return out or [{"bbox": (0, 0, page.rect.width, page.rect.height), "source": "fullpage"}]


def render_regions(pdf_path, page_numbers, dpi=200, max_regions_per_page=3):
    """Render table regions as PNG bytes dict: {page: [(bbox, png_bytes)]}."""
    doc = pymupdf.open(pdf_path)
    out = {}
    for pn in page_numbers:
        if pn > len(doc):
            continue
        page = doc[pn - 1]
        try:
            text = page.get_text("text")
        except Exception:
            text = ""
        regions = regions_for_page(page, text)[:max_regions_per_page]
        page_out = []
        for r in regions:
            clip = pymupdf.Rect(r["bbox"])
            pix = page.get_pixmap(dpi=dpi, clip=clip)
            page_out.append((r, pix.tobytes("png")))
        out[pn] = page_out
    doc.close()
    return out
