"""Eval-board / reference-design BOM extraction (deterministic).

Two printed shapes, both handled:
  1. Standalone BOM sheets (TI PDS exports): one column per text line,
     header block "Designator/Quantity/Value/PartNumber/Manufacturer/
     Description/PackageReference", then N-line groups per item.
  2. EV-kit user guides with embedded BOM tables: pymupdf find_tables(),
     header row matched by normalized column names.

Every item keeps the printed part number, manufacturer, value, and
package verbatim — the companion-parts graph is built downstream by
joining item part numbers against the parts store. Nothing is guessed;
unparseable cells stay null.
"""

from __future__ import annotations

import re
from typing import Any

BOM_SCHEMA = "harness.electronics-eval-bom.v1"

_DESIGN_ID = re.compile(
    r"\b(?:TIDA?-\d{4,6}[A-Z]?|PMP\d{4,6}[A-Z]??\d*|TIDM-\d{4,6}[A-Z]?|"
    r"[A-Z][A-Z0-9]{2,15}(?:EVKIT|EVB|EK|EVM|GEVB|AGEVB|-EVK\d*))\b")

_HEADER_ALIASES = {
    "designator": ("designator", "designators", "reference", "refdes",
                   "ref des", "item"),
    "qty": ("quantity", "qty", "count"),
    "value": ("value", "val"),
    "part_number": ("partnumber", "part number", "mpn", "mfr part",
                    "mfr part number", "manufacturer part number",
                    "part no", "part#", "catalog number"),
    "manufacturer": ("manufacturer", "vendor", "mfr", "manufacturer name"),
    "description": ("description", "desc", "component description"),
    "package": ("packagereference", "package reference", "package",
                "footprint", "case", "package/case"),
}
_CANON = {}
for canon, aliases in _HEADER_ALIASES.items():
    for a in aliases:
        _CANON[a] = canon

_QTY_RE = re.compile(r"^\d{1,4}$")
_DESIG_RE = re.compile(
    r"^[A-Z]{1,4}\d{1,4}(?:\s*[,&]\s*[A-Z]{1,4}\d{1,4})*,?$|"
    r"^(?:JP|TP|MH|H)\d{1,3}(?:\s*[,&]\s*(?:JP|TP|MH|H)\d{1,3})*,?$|"
    r"^(?:PCB\d*|FID\d*)$", re.I)


def _norm_cell(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _canon_header(cell: str) -> str | None:
    return _CANON.get(re.sub(r"\s+", " ", cell).strip().lower())


def _design_id_from(text: str) -> str | None:
    m = _DESIGN_ID.search(text)
    return m.group(0) if m else None


def _items_from_table(rows: list[list]) -> tuple[list[str], list[dict]]:
    """Header-matched table rows -> (order, items)."""
    if not rows:
        return [], []
    header_idx, order = None, []
    for i, row in enumerate(rows[:6]):
        canon = [_canon_header(_norm_cell(c)) for c in row]
        hits = [c for c in canon if c]
        if len(hits) >= 3 and ("part_number" in hits or "manufacturer" in hits):
            header_idx = i
            order = canon
            break
    if header_idx is None:
        return [], []
    items = []
    for row in rows[header_idx + 1:]:
        item: dict[str, Any] = {}
        for j, cell in enumerate(row):
            if j >= len(order) or not order[j]:
                continue
            val = _norm_cell(cell)
            if val and val.lower() not in ("none", "n/a", "-", "–", "dnp"):
                item[order[j]] = val
        if item.get("part_number") or item.get("description"):
            items.append(item)
    return order, items


def _items_from_flow(lines: list[str]) -> list[dict]:
    """Column-flow state machine: header block, then N-line groups."""
    # locate header: consecutive lines whose normalized forms are aliases
    start = None
    order: list[str] = []
    for i in range(len(lines) - 2):
        probe = []
        j = i
        while j < len(lines) and len(probe) < 9:
            c = _canon_header(lines[j])
            if c is None:
                if lines[j].strip() == "":
                    j += 1
                    continue
                break
            probe.append(c)
            j += 1
        if len(probe) >= 4 and ("part_number" in probe or "manufacturer" in probe):
            start, order = j, probe
            break
    if start is None:
        return []
    width = len(order)
    items = []
    buf: list[str] = []
    for line in lines[start:]:
        s = line.strip()
        if not s:
            continue
        buf.append(s)
        if len(buf) == width:
            item = {}
            for key, val in zip(order, buf):
                if val.lower() not in ("none", "n/a", "-", "–", "dnp", "nc"):
                    item[key] = val
            # plausibility: designator or qty should look right when present
            ok = True
            if "designator" in item and not _DESIG_RE.match(item["designator"].split(",")[0].strip()):
                ok = "part_number" in item or "description" in item
            if "qty" in item and not _QTY_RE.match(item["qty"]):
                ok = False
            if ok and (item.get("part_number") or item.get("description")):
                items.append(item)
            buf = []
    return items


def bom_from_document(doc) -> dict[str, Any]:
    """Extract design_id + component items from a BOM/EV-kit document."""
    first_text = ""
    items: list[dict] = []
    method = None
    page_hits: list[int] = []
    for i in range(min(doc.page_count, 40)):
        try:
            page = doc[i]
        except Exception:
            continue
        if i == 0:
            first_text = page.get_text() or ""
        # 1. structured tables
        try:
            tables = page.find_tables()
            for t in (tables.tables if tables else []):
                order, t_items = _items_from_table(t.extract())
                if t_items and len(t_items) > len(items):
                    items, method = t_items, f"find_tables:p{i + 1}"
                    page_hits.append(i + 1)
        except Exception:
            pass
        # 2. column-flow fallback (standalone BOM sheets)
        if not items:
            text = page.get_text() or ""
            f_items = _items_from_flow(text.splitlines())
            if len(f_items) > len(items):
                items, method = f_items, f"column_flow:p{i + 1}"
                page_hits.append(i + 1)

    design_id = _design_id_from(first_text)
    board = None
    m = re.search(r"Evaluates?:\s*([A-Z0-9\-/]+)", first_text)
    if m:
        board = m.group(1)
    if not board:
        m = re.search(r"Bill of Materials", first_text)
        if m:
            head = first_text[:m.start()].splitlines()
            head = [h.strip() for h in head if h.strip()]
            board = head[-1] if head else None
    return {
        "schema": BOM_SCHEMA,
        "design_id": design_id,
        "board_name": board,
        "items": items,
        "item_count": len(items),
        "parse_method": method,
        "bom_pages": sorted(set(page_hits)),
    }


__all__ = ["BOM_SCHEMA", "bom_from_document"]
