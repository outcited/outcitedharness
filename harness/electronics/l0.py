"""L0 substrate: what every document is, and where things are in it.

One pass, deterministic, no model: classify the document (datasheet,
errata, PCN, BOM, reference design, reference/user manual, app note,
brochure, ...), map its sections to extraction lanes via the TOC, and flag
the pages that text cannot reach (text-poor, image/drawing-heavy) so the
vision backlog falls out of the substrate instead of guesswork.

The record is the recon unit the factory runs on: per class, the census
answers 'what sections exist, which lanes read them, how many pages need
eyes'.
"""

from __future__ import annotations

import re
from typing import Any


L0_SCHEMA = "harness.electronics-l0.v1"

_CLASS_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("errata", re.compile(r"\berrata\b|\badvisories\b|known\s+exceptions.{0,40}advisories|silicon\s+errata", re.I)),
    ("pcn", re.compile(r"product\s+change\s+notification|\bPCN\b\s*(number|date|\:)|change\s+notification", re.I)),
    ("bom", re.compile(r"bill\s+of\s+materials|\bBOM\b\s*[:)]|assembly\s+drawings?\s+and\s+bill", re.I)),
    ("reference_design", re.compile(r"evaluation\s+(board|module|kit)|\bEVM\b|reference\s+design|evaluation\s+users?\s+guide", re.I)),
    ("reference_manual", re.compile(r"\breference\s+manual\b|register\s+map|bit\s+field\s+(description|table)", re.I)),
    ("user_manual", re.compile(r"\busers?\s+(manual|guide)\b|programming\s+manual|software\s+designers?\s+guide", re.I)),
    ("application_note", re.compile(r"application\s+note|\bAN\s*[:#]?\s*\d{2,6}\b|design\s+guide|application\s+report", re.I)),
    ("datasheet", re.compile(r"\b(data\s*sheet|datasheet)\b|electrical\s+characteristics|absolute\s+maximum\s+ratings|recommended\s+operating\s+conditions", re.I)),
    ("brochure", re.compile(r"\bbrochure\b|\bflyer\b|\bnewsletter\b", re.I)),
    ("white_paper", re.compile(r"\bwhite\s+paper\b|\btechnical\s+brief\b", re.I)),
    ("fact_sheet", re.compile(r"\bfact\s*sheet\b|\bproduct\s+brief\b|\bselector\s+guide\b", re.I)),
    ("package_drawing", re.compile(r"package\s+(?:drawing|outline|dimensions?)|mechanical\s+(?:data|drawing)|land\s*pattern|\bPOD\b\s*[:#]", re.I)),
    ("bonding_diagram", re.compile(r"bond(?:ing)?\s+diagram|die\s+(?:pad|attach)\s+layout", re.I)),
    ("pinout_diagram", re.compile(r"pin(?:out)?\s+diagram|pin\s+assignment\s+diagram|terminal\s+assignment\s+diagram", re.I)),
    ("design_note", re.compile(r"\bdesign\s+note\b|\bDN\s*[:#]\s*\d", re.I)),
    ("conformance_report", re.compile(r"conformance\s+(?:report|declaration)|declaration\s+of\s+conform", re.I)),
    ("customer_drawing", re.compile(r"customer\s+drawing|\bCD\s*[:#]\s*\d{4,}", re.I)),
    ("eval_document", re.compile(r"evaluation\s+(?:board|kit|module).{0,40}(?:schematic|bom|bill|gerber|test|user|guide|procedure)|\bEVM\b.{0,40}(?:schematic|bom|gerber|test|user|guide)", re.I)),
    ("simulation_model", re.compile(r"\b(P-?SPICE|TINA-?TI|SIMetrix|LTspice|IBIS)\b|\.(zip|lib|mod|ibs)\b|model\s+(?:file|kit|package)", re.I)),
)

_FILENAME_HINTS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("errata", re.compile(r"errata|sprz\d|slaz\d|_es_|-es\d|errataclarif", re.I)),
    ("pcn", re.compile(r"pcn|change.?notification", re.I)),
    ("bom", re.compile(r"bom|bill.?of.?materials", re.I)),
    ("reference_design", re.compile(r"evm|eval|dev.?kit|refdesign|reference.?design|tidu|tidm", re.I)),
    ("reference_manual", re.compile(r"\brm\d{4}\b|reference.?manual", re.I)),
    ("user_manual", re.compile(r"\bum\d{4}\b|users?.?(manual|guide)", re.I)),
    ("application_note", re.compile(r"\ban\d{5,6}\b|app.?note|application.?note|slva|sluu|slyt", re.I)),
    ("datasheet", re.compile(r"datasheet|data.?sheet|\bds\d{6,7}\b|\bds7000\d+|slas|slfs|slyt", re.I)),
)

_MIN_TEXT_CHARS = 120
_HEADING_Y_MAX_LINES = 40


def classify_document(front_text: str, filename: str, toc_titles: str) -> str:
    evidence = f"{front_text[:4000]}\n{toc_titles[:2000]}"
    for label, pattern in _CLASS_RULES:
        if pattern.search(evidence):
            return label
    for label, pattern in _FILENAME_HINTS:
        if pattern.search(filename):
            return label
    return "unknown"


def l0_record(document: Any, *, source_path: str) -> dict[str, Any]:
    """What is this document and where are the extractable parts."""

    from harness.electronics.page_index import classify_section

    page_count = int(document.page_count)
    front_text = document[0].get_text() if page_count else ""
    toc = document.get_toc() or []
    toc_titles = " ".join(str(item[1]) for item in toc if len(item) >= 2)
    doc_class = classify_document(front_text, source_path.split("/")[-1], toc_titles)

    sections: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    lane_counts: dict[str, int] = {}
    for _, title, page in toc:
        if not title or page < 1 or page > page_count:
            continue
        key = (" ".join(str(title).split()), int(page))
        if key in seen:
            continue
        seen.add(key)
        lanes = classify_section(str(title))
        for lane in lanes:
            lane_counts[lane] = lane_counts.get(lane, 0) + 1
        sections.append({"title": key[0][:120], "page_1based": int(page), "lanes": lanes})

    needs_ocr_pages: list[int] = []
    vision_candidate_pages: list[int] = []
    total_chars = 0
    for index in range(page_count):
        page = document[index]
        text = page.get_text() or ""
        total_chars += len(text)
        chars = len(text.strip())
        images = 0
        try:
            images = len(page.get_images(full=True))
        except Exception:
            images = 0
        drawings = 0
        if chars < _MIN_TEXT_CHARS:
            try:
                drawings = len(page.get_drawings())
            except Exception:
                drawings = 0
        if chars < 40 and (images or drawings):
            needs_ocr_pages.append(index + 1)
        elif chars < _MIN_TEXT_CHARS and (images + drawings) >= 2:
            vision_candidate_pages.append(index + 1)

    avg_chars = (total_chars // page_count) if page_count else 0
    return {
        "schema": L0_SCHEMA,
        "source_path": source_path,
        "doc_class": doc_class,
        "page_count": page_count,
        "toc_sections": len(sections),
        "sections": sections if len(sections) <= 400 else sections[:400],
        "lane_counts": lane_counts,
        "needs_ocr_pages": needs_ocr_pages[:200],
        "needs_ocr_count": len(needs_ocr_pages),
        "vision_candidate_pages": vision_candidate_pages[:400],
        "vision_candidate_count": len(vision_candidate_pages),
        "avg_page_chars": avg_chars,
        "text_quality": "good" if avg_chars >= 800 else ("thin" if avg_chars >= 200 else "scanned"),
    }


__all__ = ["L0_SCHEMA", "classify_document", "l0_record"]
