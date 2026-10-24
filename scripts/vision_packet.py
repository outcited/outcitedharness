#!/usr/bin/env python3
"""vision_packet — assemble the vision context packet from PROVEN pieces.

MIGRATION, not greenfield. Every function here is imported from code that
already earned its determinism:

  page selection   <- harness.electronics.page_evidence.selected_pages
  page evidence    <- page_evidence.extract_profile_evidence (blocks+bbox+tables)
  crops            <- build_pinout_vision_row_dataset._expanded_clip/_render
                      (3pt padding, DPI matrix, min-16px, C13 crop_sha256)
  expected shape   <- gold_regions.pin_table_score (bga_balls / pin shapes)
  verifier         <- extract_pin_tables_tp4.verify_rows (verbatim-quote law)

Packet law (deterministic + truthful):
  - the crop is content-addressed (crop_sha256) before any model sees it
  - the text band (blocks intersecting the crop bbox) ships WITH the crop —
    vision confirms, text anchors
  - model output passes the same verbatim/count gates as text-lane output
  - nothing invents: a row that fails verification is dropped, never repaired
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import pymupdf

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)

from harness.electronics.page_evidence import extract_profile_evidence, selected_pages  # noqa: E402
from build_pinout_vision_row_dataset import _expanded_clip, _render  # noqa: E402
from extract_pin_tables_tp4 import PIN_PROMPT, verify_rows, _norm  # noqa: E402

PAGE_DPI = 144       # C13 provenance snapshot law
REGION_DPI = 600     # curves standard for extraction crops


def band_blocks(blocks: list[dict], bbox: tuple[float, ...]) -> list[str]:
    """Text lines whose blocks intersect the crop bbox — the search-context
    layer: vision reads the image WITH the words the extractor already has."""
    x0, y0, x1, y1 = bbox
    out = []
    for b in blocks:
        bx0, by0, bx1, by1 = b.get("bbox") or (0, 0, 0, 0)
        if bx0 < x1 and bx1 > x0 and by0 < y1 and by1 > y0:
            t = str(b.get("text") or "").strip()
            if t:
                out.append(t)
    return out


def build_packet(pdf_path: Path | str, sha: str, lanes: list[str] | None = None,
                 max_pages: int = 2, out_dir: Path | str = "/tmp/vision-packets") -> dict | None:
    """One document -> packet {crops[], context, expected}. Deterministic."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    data = Path(pdf_path).read_bytes()
    real_sha = hashlib.sha256(data).hexdigest()
    if sha and real_sha != sha:
        return None  # truth law: sha mismatch = no packet
    doc = pymupdf.open(stream=data, filetype="pdf")

    profile = {"document_sha256": real_sha, "source_path": str(pdf_path),
               "lane_pages": {}}
    # with no profile we take the rip-style shortcut: scan pages for lane regexes
    if not lanes:
        from harness.electronics.page_index import LANE_PATTERNS
        lane_pages: dict[str, list[int]] = {}
        for pno in range(min(doc.page_count, 400)):
            text = doc[pno].get_text("text")
            for lane, pats in LANE_PATTERNS.items():
                if lane in ("pin_or_ball", "pin_semantics"):
                    for pat in pats:
                        if pat.search(text):
                            lane_pages.setdefault(lane, []).append(pno + 1)
                            break
        profile["lane_pages"] = lane_pages

    pages = sorted(selected_pages(profile, maximum_pages_per_lane=max_pages).items())
    if not pages:
        doc.close()
        return None

    packet = {"sha": real_sha, "pages": [], "schema": "harness.vision-packet.v1"}
    evidence = list(extract_profile_evidence(doc, profile, maximum_pages_per_lane=max_pages))
    for page_1based, page_lanes in pages:
        page = doc[page_1based - 1]
        ev = next((e for e in evidence if e.get("page_1based") == page_1based), None)
        blocks = (ev or {}).get("blocks") or []
        tables = (ev or {}).get("tables") or []
        crops = []
        # crop every detected table region; else the full page at provenance DPI
        if tables:
            for t in tables[:4]:
                bbox = t.get("bbox")
                if not bbox:
                    continue
                clip = _expanded_clip(bbox, page.rect)
                dest = out_dir / f"{real_sha[:16]}.p{page_1based}.t{t.get('table_index')}.png"
                _render(page, bbox, dest, REGION_DPI)
                crops.append({
                    "crop": str(dest),
                    "crop_sha256": hashlib.sha256(dest.read_bytes()).hexdigest(),
                    "bbox": [round(v, 1) for v in bbox],
                    "dpi": REGION_DPI,
                    "band_text": band_blocks(blocks, bbox)[:80],
                    "table_rows_text": (t.get("rows") or [])[:200],
                })
        else:
            dest = out_dir / f"{real_sha[:16]}.p{page_1based}.full.png"
            _render(page, tuple(page.rect), dest, PAGE_DPI)
            crops.append({"crop": str(dest),
                          "crop_sha256": hashlib.sha256(dest.read_bytes()).hexdigest(),
                          "bbox": [round(v, 1) for v in page.rect],
                          "dpi": PAGE_DPI,
                          "band_text": band_blocks(blocks, tuple(page.rect))[:120],
                          "table_rows_text": []})
        packet["pages"].append({"page": page_1based, "lanes": sorted(page_lanes),
                                "extraction_status": (ev or {}).get("extraction_status"),
                                "crops": crops})
    doc.close()
    packet["prompt"] = PIN_PROMPT  # ball-aware contract, same as text lane
    return packet


def verify_packet_rows(payload: dict, packet: dict) -> tuple[list, int]:
    """Model rows from a packet pass the SAME verbatim gate: quote must appear
    in the band text or the page's blocks. Deterministic, fail-closed."""
    corpus = " ".join(
        " ".join(c.get("band_text") or []) + " " +
        " ".join(str(cell) for row in c.get("table_rows_text") or [] for cell in row if cell)
        for pg in packet["pages"] for c in pg["crops"])
    return verify_rows(payload, _norm(corpus))
