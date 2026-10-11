#!/usr/bin/env python3
"""Render provenance evidence: page renders and row-band crops.

`C13` says an evidence snapshot is a **content-addressed page render at 144
DPI** plus a **row-band crop** carrying a `crop_sha256`. This produces both, in
the layout `dw-evidence` reads, so hashes are comparable across the two.

    {root}/pages/{sha[..2]}/{page_render_sha256}.png
    {root}/crops/{sha[..2]}/{crop_sha256}.png

**Provenance must break loudly.** If the pixels change, the hash changes and
the claim becomes unverifiable — not "probably still fine"
(`PROVENANCE.md` §4.1). The renderer identity is recorded per render, because a
different engine is different pixels and therefore a different artifact (`C2`).

The PNG encoder is in here deliberately and tested for determinism: a
different encoder is different bytes and a different identity for the same
picture.

Usage::

    python3 scripts/build_evidence.py --limit 200
    python3 scripts/build_evidence.py --out /path/to/store
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import pathlib
import struct
import sys
import zlib

import fitz

REPO = pathlib.Path(__file__).resolve().parent.parent
CLAIMS = REPO / "tests" / "fixtures" / "gold" / "claim_provenance_v2.jsonl"
OUT_INDEX = REPO / "tests" / "fixtures" / "gold" / "evidence_index_v1.jsonl"
STORE_DEFAULT = pathlib.Path("/Users/samkim/mnt/yosemiteraid5/Public/m5-opencode/evidence")
VAULT = pathlib.Path("/Volumes/M5_4TB/vault/cas")

PROVENANCE_DPI = 144          # C13
BAND_PAD_Y_PT = 6.0           # vertical padding so the row header is in frame
BAND_PAD_X_PT = 36.0          # horizontal padding: a row band, not a word crop
BAND_MIN_HEIGHT_PT = 18.0     # a one-line quote still gets a readable band


# --- deterministic PNG -------------------------------------------------------

def _chunk(tag: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + tag
        + data
        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def encode_png(width: int, height: int, rgba: bytes) -> bytes:
    """8-bit RGBA PNG, stored deflate, deterministic."""
    raw = b"".join(
        b"\x00" + rgba[y * width * 4 : (y + 1) * width * 4] for y in range(height)
    )
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(raw, 6))
        + _chunk(b"IEND", b"")
    )


def crop_rgba(
    rgba: bytes, width: int, height: int, bbox: tuple[int, int, int, int]
) -> tuple[bytes, int, int]:
    """Cut a pixel rectangle out of a flat RGBA buffer, clamped to the page."""
    x0, y0, x1, y1 = bbox
    x0 = max(0, min(x0, width))
    x1 = max(0, min(x1, width))
    y0 = max(0, min(y0, height))
    y1 = max(0, min(y1, height))
    if x1 <= x0 or y1 <= y0:
        return b"", 0, 0
    cw, ch = x1 - x0, y1 - y0
    out = bytearray(cw * ch * 4)
    for row in range(ch):
        src = ((y0 + row) * width + x0) * 4
        dst = row * cw * 4
        out[dst : dst + cw * 4] = rgba[src : src + cw * 4]
    return bytes(out), cw, ch


# --- pipeline ----------------------------------------------------------------

def load_claims(limit: int | None) -> list[dict]:
    rows = []
    with CLAIMS.open() as handle:
        for line in handle:
            if not line.strip():
                continue
            rows.append(json.loads(line))
            if limit and len(rows) >= limit:
                break
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--out", type=pathlib.Path, default=STORE_DEFAULT)
    ap.add_argument("--index", type=pathlib.Path, default=OUT_INDEX)
    args = ap.parse_args()

    rows = load_claims(args.limit)
    print(f"claims in: {len(rows)}")
    (args.out / "pages").mkdir(parents=True, exist_ok=True)
    (args.out / "crops").mkdir(parents=True, exist_ok=True)

    # One render per (doc, page), cached as (png, rgba, w, h, rects_in_pt).
    render_cache: dict[tuple[str, int], tuple[bytes, bytes, int, int, list]] = {}
    index: list[dict] = []
    stats = collections.Counter()
    engine = f"pymupdf-{fitz.version[0]}"

    for n, r in enumerate(rows, 1):
        sha, page_no = r["doc_sha256"], r.get("page")
        if not page_no:
            stats["no_page"] += 1
            continue
        key = (sha, int(page_no))

        if key not in render_cache:
            path = VAULT / sha[:2] / f"{sha}.pdf"
            try:
                doc = fitz.open(path)
                page = doc[int(page_no) - 1]
            except Exception:
                stats["doc_unreadable"] += 1
                continue

            zoom = PROVENANCE_DPI / 72.0
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
            rgba = bytes(pix.samples)
            w, h = pix.width, pix.height
            # C13 says ROW-BAND crop: the row header must be in frame, not just
            # the number. The band spans the union of the row header and the
            # value, so a reviewer can see which row the value came from.
            quote = r.get("quote") or ""
            row_header = r.get("row_header") or ""
            rects = [
                [float(v) for v in (rc.x0, rc.y0, rc.x1, rc.y1)]
                for rc in (page.search_for(quote) if quote else [])
            ]
            row_rects = [
                [float(v) for v in (rc.x0, rc.y0, rc.x1, rc.y1)]
                for rc in (page.search_for(row_header) if row_header else [])
            ]
            # Prefer the row-header rect closest in y to the quote, so a
            # repeated header does not drag the band across pages of table.
            if rects and row_rects:
                qy = sum((rc[1] + rc[3]) / 2 for rc in rects) / len(rects)
                row_rects.sort(key=lambda rc: abs((rc[1] + rc[3]) / 2 - qy))
                rects = [rects[0], row_rects[0]]
            elif row_rects:
                rects = row_rects
            doc.close()
            render_cache[key] = (encode_png(w, h, rgba), rgba, w, h, rects)

        page_png, rgba, w, h, rects = render_cache[key]
        page_sha = hashlib.sha256(page_png).hexdigest()
        page_path = args.out / "pages" / page_sha[:2] / f"{page_sha}.png"
        if not page_path.exists():
            page_path.parent.mkdir(parents=True, exist_ok=True)
            page_path.write_bytes(page_png)

        crop_sha = None
        crop_bbox = None
        crop_size = None
        if rects:
            zoom = PROVENANCE_DPI / 72.0
            x0 = min(rc[0] for rc in rects)
            y0 = min(rc[1] for rc in rects)
            x1 = max(rc[2] for rc in rects)
            y1 = max(rc[3] for rc in rects)
            # A row band is a horizontal band: widen to the row, not the word.
            # 36pt either side pulls in the neighbouring cells without
            # swallowing the whole page.
            x0 = max(0.0, x0 - BAND_PAD_X_PT)
            x1 = min(w / zoom, x1 + BAND_PAD_X_PT)
            y0 = max(0.0, y0 - BAND_PAD_Y_PT)
            y1 = min(h / zoom, y1 + BAND_PAD_Y_PT)
            if (y1 - y0) * zoom < BAND_MIN_HEIGHT_PT:
                y1 = min(h / zoom, y0 + BAND_MIN_HEIGHT_PT / zoom)
            px = tuple(int(round(v * zoom)) for v in (x0, y0, x1, y1))
            band, cw, ch = crop_rgba(rgba, w, h, px)
            if band:
                band_png = encode_png(cw, ch, band)
                crop_sha = hashlib.sha256(band_png).hexdigest()
                crop_path = args.out / "crops" / crop_sha[:2] / f"{crop_sha}.png"
                if not crop_path.exists():
                    crop_path.parent.mkdir(parents=True, exist_ok=True)
                    crop_path.write_bytes(band_png)
                crop_bbox = list(px)
                crop_size = [cw, ch]

        index.append({
            "doc_sha256": sha,
            "opn": r.get("opn"),
            "symbol": r.get("symbol"),
            "quote": r.get("quote"),
            "page_1based": int(page_no),
            "page_render_sha256": page_sha,
            "crop_sha256": crop_sha,
            "crop_bbox_px": crop_bbox,
            "crop_size_px": crop_size,
            "dpi": PROVENANCE_DPI,
            "width_px": w,
            "height_px": h,
            "engine": engine,
            "quote_rects_pt": rects,
        })
        stats["with_crop" if crop_sha else "page_only"] += 1

        if n % 200 == 0:
            print(f"  ... {n}/{len(rows)} claims", file=sys.stderr)

    args.index.parent.mkdir(parents=True, exist_ok=True)
    with args.index.open("w") as handle:
        for row in index:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    print(f"\nwrote {args.index}")
    print(f"  evidence rows   {len(index)}")
    for k in ("with_crop", "page_only", "no_page", "doc_unreadable"):
        if stats[k]:
            print(f"  {k:14s} {stats[k]:7d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
