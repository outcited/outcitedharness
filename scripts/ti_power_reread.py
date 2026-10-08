#!/usr/bin/env python3
"""TI P0 reread: read operating values from Recommended Operating Conditions
or Electrical Characteristics only.

CR power-reread-p0-20260917: the P0 register withheld 272 fields + 2
verify-live because the only evidence was a regex, never a document row. This
reads each field from a specified-operation row and records the verbatim row
line. The Absolute Maximum Ratings table is never read for a design-in value
(the gate rejects `absolute_max_as_operating`; those rejections are the
point). A field that cannot be found verbatim gets a `not_found_verbatim`
disposition -- never an inference.

    python3 scripts/ti_power_reread.py \
        --queue /Volumes/M5_4TB/exports/cr_requests/power-reread-p0-20260914.json \
        --pdf-root /tmp/ti-reread-pdfs \
        --out /tmp/reread-rows.jsonl --dispositions-out /tmp/reread-disp.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

import pymupdf

HEADING_SIZE = 11.0
TOC_PAGE_LIMIT = 3  # first pages are front matter / TOC; real tables follow

_SECNUM = r"(?:^\d+(?:\.\d+)*\s+)?"
ROC = re.compile(_SECNUM + r"recommended\s+operating\s+conditions", re.I)
EC = re.compile(_SECNUM + r"electrical\s+characteristics", re.I)
ABSMAX = re.compile(_SECNUM + r"absolute\s+maximum\s+ratings", re.I)
OTHER_SPEC = re.compile(
    _SECNUM +
    r"(?:dissipation\s+ratings|thermal\s+information|esd\s+ratings|"
    r"switching\s+characteristics|typical\s+characteristics|"
    r"timing\s+requirements|recommended\s+timing|detailed\s+description|"
    r"specifications?|pin\s+configuration|features|applications|description|"
    r"absolute\s+maximum\s+ratings|recommended\s+operating\s+conditions|"
    r"electrical\s+characteristics)\b",
    re.I,
)


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _heading_spans(doc) -> list[dict]:
    """Bold 11pt section headings with page + y. Body headings only: the TOC
    lists the same names at body weight, so the weight filter is what keeps
    the TOC out."""
    out = []
    for pno in range(doc.page_count):
        page = doc[pno]
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                spans = line.get("spans") or []
                if not spans:
                    continue
                text = "".join(s["text"] for s in spans).strip()
                if not text:
                    continue
                if abs(spans[0]["size"] - HEADING_SIZE) < 0.3 and "Bold" in spans[0]["font"]:
                    out.append({"page": pno, "y": line["bbox"][1], "text": text})
    out.sort(key=lambda h: (h["page"], h["y"]))
    return out


def _classify(text: str) -> str:
    if ABSMAX.match(text):
        return "absmax"
    if ROC.match(text):
        return "roc"
    if EC.match(text):
        return "ec"
    return "other"


def sections(doc) -> list[dict]:
    """Bound each spec section: from its heading to the next heading on the
    same page or the following ones. Absolute Maximum Ratings becomes its own
    section so it can be excluded explicitly."""
    heads = _heading_spans(doc)
    out = []
    for i, h in enumerate(heads):
        if h["page"] < TOC_PAGE_LIMIT:
            continue
        kind = _classify(h["text"])
        if kind == "other" and not OTHER_SPEC.match(h["text"]):
            continue
        nxt = heads[i + 1] if i + 1 < len(heads) else None
        out.append({"kind": kind, "text": h["text"], "page": h["page"], "y0": h["y"],
                    "page_end": nxt["page"] if nxt else h["page"], "y1": nxt["y"] if nxt else 10 ** 9})
    return out


def _row_text(page) -> str:
    return page.get_text("text")


def _section_text(doc, sec: dict) -> str:
    parts = []
    for pno in range(sec["page"], sec["page_end"] + 1):
        t = _row_text(doc[pno])
        if pno == sec["page"]:
            # drop everything before the heading by locating its text
            idx = t.find(sec["text"])
            t = t[idx + len(sec["text"]):] if idx >= 0 else t
        if pno == sec["page_end"] and sec["y1"] < 10 ** 8:
            # cut at the next heading's text occurrence on the end page
            nxt = t.find(sec.get("next_text", "")) if sec.get("next_text") else -1
            if nxt > 0:
                t = t[:nxt]
        parts.append(t)
    return "\n".join(parts)


def _lines(text: str) -> list[str]:
    return [l.strip() for l in text.splitlines() if l.strip()]


_NUM = re.compile(r"^[–\-−]?\d+(?:\.\d+)?$")


def _words(doc, sec: dict) -> list[dict]:
    """All words in the section's y-band, with page + coordinates."""
    out = []
    for pno in range(sec["page"], sec["page_end"] + 1):
        for w in doc[pno].get_text("words"):
            x0, y0, x1, y1, t = w[0], w[1], w[2], w[3], w[4]
            if pno == sec["page"] and y0 < sec["y0"]:
                continue
            if pno == sec["page_end"] and sec["y1"] < 10 ** 8 and y0 >= sec["y1"]:
                continue
            out.append({"page": pno, "x0": x0, "y": y0, "t": t})
    return out


def _table_rows(doc, sec: dict) -> list[dict]:
    """Coordinate-aware spec-table rows. TI extracts each row as: row-label
    words in the left columns, then MIN / NOM / MAX / UNIT cells in fixed x
    bands. Words are grouped by y (within 4pt) into visual rows; the label is
    everything left of the first numeric column, and the numeric cells are
    read by x-position so a negative sign or a dash column never shifts the
    other cells."""
    words = _words(doc, sec)
    if not words:
        return []
    # y-bucket into rows
    words.sort(key=lambda w: (w["page"], w["y"], w["x0"]))
    rows = []
    for w in words:
        if rows and rows[-1]["page"] == w["page"] and abs(w["y"] - rows[-1]["y"]) <= 4.0:
            rows[-1]["words"].append(w)
        else:
            rows.append({"page": w["page"], "y": w["y"], "words": [w]})
    # find the header band: the row containing MIN and MAX
    header_x = None
    hdr_i = None
    for i, r in enumerate(rows):
        toks = {w["t"].upper() for w in r["words"]}
        if "MIN" in toks and "MAX" in toks:
            hdr_i, header_x = i, {w["t"].upper(): w["x0"] for w in r["words"]}
            break
    if header_x is None:
        return []
    min_x = header_x.get("MIN", header_x.get("MAX"))
    max_x = header_x.get("MAX", min_x)
    nom_x = header_x.get("NOM")
    unit_x = header_x.get("UNIT")
    # column centres: assign each numeric cell to the nearest named column
    cols = [("MIN", min_x)]
    if nom_x is not None:
        cols.append(("NOM", nom_x))
    if max_x is not None:
        cols.append(("MAX", max_x))
    cols.sort(key=lambda c: c[1])
    bounds = []
    for i, (name, x) in enumerate(cols):
        lo = (cols[i - 1][1] + x) / 2 if i > 0 else -1e9
        hi = (x + cols[i + 1][1]) / 2 if i + 1 < len(cols) else 1e9
        bounds.append((name, lo, hi))
    data = []

    def cell_for(w):
        for name, lo, hi in bounds:
            if lo <= w["x0"] < hi:
                return name
        return None

    for r in rows[hdr_i + 1:]:
        nums = [w for w in r["words"] if _NUM.match(w["t"])]
        labels = [w for w in r["words"] if not _NUM.match(w["t"]) and w["x0"] < min_x - 5]
        if not nums and not labels:
            continue
        # a row that is only a label (continuation of the previous label) is
        # folded into it; a row with numbers is a data row.
        if not nums:
            if data and labels:
                data[-1]["label"] += " " + " ".join(w["t"] for w in labels)
            continue
        cells = {}
        for w in nums:
            c = cell_for(w)
            if c and c not in cells:
                cells[c] = w["t"]
        unit = None
        # The unit is the rightmost non-numeric token on the row, sitting to
        # the right of the last numeric column. The UNIT header x is a hint,
        # not a requirement -- some tables omit the header cell.
        right_of = (max(w["x0"] for w in nums)) if nums else 0
        utok = [w["t"] for w in sorted(r["words"], key=lambda z: z["x0"])
                if w["x0"] > right_of + 2 and not _NUM.match(w["t"])]
        if utok:
            unit = " ".join(utok)
        elif unit_x is not None:
            us = [w["t"] for w in r["words"] if w["x0"] >= unit_x - 5 and not _NUM.match(w["t"])]
            unit = " ".join(us) or None
        data.append({"label": " ".join(w["t"] for w in labels).strip(),
                     "cells": cells, "unit": unit,
                     "nums": [w["t"] for w in sorted(nums, key=lambda z: z["x0"])]})
    return [d for d in data if d["label"] or d["cells"]]


VIN_LABEL = re.compile(
    r"\b(?:supply\s+voltage|input\s+voltage|voltage\s+range|input\s+supply|"
    r"bus\s+voltage|operating\s+voltage|v\s*in\b|vin\b|v5in\b|vdd(?:io)?\b|vcc\b|vbat\b)\b",
    re.I,
)
VIN_EXCLUDE = re.compile(
    r"\b(?:bootstrap|bst|drvh|drvl|ll\b|switch\s+node|junction|storage|start-?up|"
    r"threshold|esd|current|frequency|resistance|capacitance|hysteresis|"
    r"input\s+level|logic\s+level|gain|offset|noise|ripple|dropout|regulation|"
    r"feedback|reference|clock|data|sclk|sdi|cs\b)\b",
    re.I,
)
IOUT_LABEL = re.compile(
    r"\b(?:output\s+current|continuous\s+load\s+current|load\s+current|"
    r"output\s+drive\s+current|drive\s+current|peak\s+output\s+current|"
    r"output\s+current\s+capability|current\s+capability|"
    r"maximum\s+continuous\s+output\s+current|i\s*out\b|iout\b|"
    r"drain\s+current|channel\s+current|output\s+source\s+current)\b",
    re.I,
)
IOUT_EXCLUDE = re.compile(
    r"\b(?:supply|quiescent|standby|sleep|shutdown|leakage|bias|sense|limit|"
    r"threshold|reference|start-?up|soft-?start|discharge|sink|source\s+to\s+"
    r"gate|short[\s-]?circuit|transition|switching|reverse|clamp|protection|"
    r"temperature|frequency|voltage|feedback|error)\b",
    re.I,
)


def _to_amps(value: float, unit: str | None) -> float:
    u = (unit or "").strip().lower().replace("µ", "u").replace("μ", "u")
    if u == "ma":
        return value / 1000.0
    if u == "ua":
        return value / 1e6
    return value


def _unit_ok(unit: str | None, field: str) -> bool:
    """The read must carry the right physical quantity. A voltage field whose
    row unit is Hz/mA/°C is a mis-resolved row, not a reading. For output
    current, mA/A are in scope; nA/uA are leakage currents, not load."""
    u = (unit or "").strip().lower().replace("µ", "u").replace("μ", "u")
    if field in ("vin_max_v", "vin_min_v"):
        return u in {"v", "mv", "kv"}
    if field == "iout_max_a":
        return u in {"a", "ma"}
    return True


def _pick_row(rows: list[dict], field: str) -> dict | None:
    cands = []
    for r in rows:
        if field in ("vin_max_v", "vin_min_v"):
            if not VIN_LABEL.search(r["label"]) or VIN_EXCLUDE.search(r["label"]):
                continue
            cands.append(r)
        elif field == "iout_max_a":
            if not IOUT_LABEL.search(r["label"]) or IOUT_EXCLUDE.search(r["label"]):
                continue
            cands.append(r)
    if not cands:
        return None
    if field in ("vin_max_v", "iout_max_a"):
        with_max = [r for r in cands if "MAX" in r["cells"]]
        if with_max:
            return max(with_max, key=lambda r: _num(r["cells"]["MAX"]))
        with_typ = [r for r in cands if "NOM" in r["cells"]]
        if with_typ:
            return max(with_typ, key=lambda r: _num(r["cells"]["NOM"]))
        return cands[0]
    if field == "vin_min_v":
        with_min = [r for r in cands if "MIN" in r["cells"]]
        if with_min:
            return min(with_min, key=lambda r: _num(r["cells"]["MIN"]))
        return cands[0]
    return cands[0]


def _num(s: str) -> float:
    try:
        return float(s.replace("–", "-").replace("−", "-"))
    except Exception:
        return float("-inf")


REG_OUT = re.compile(
    r"\b(quad|triple|dual|four|three|two|single)[- ](?:output|channel|rail|phase)s?\b"
    r"|\b(\d+)[- ](?:output|channel|rail|phase)s?\b",
    re.I,
)
WORD_N = {"single": 1, "two": 2, "dual": 2, "three": 3, "triple": 3, "four": 4, "quad": 4}


def read_field(doc, field: str, part_number: str) -> dict:
    secs = sections(doc)
    roc = [s for s in secs if s["kind"] == "roc"]
    ec = [s for s in secs if s["kind"] == "ec"]
    # never absmax: only roc + ec are searched
    ordered = roc + ec
    if field == "regulated_outputs":
        # A count of regulated outputs/rails. The vendor's own wording must
        # appear in a ROC/EC row AND name an output count as the row's
        # subject; test conditions ("single channel on") and unrelated
        # sub-blocks ("Buck 1 output voltage range") are not the count. In
        #practice this field is a device-class figure carried in the front
        # matter, not a spec-table row, so most of these dispose.
        OUT_SUBJECT = re.compile(
            r"^(?:\d+[- ]?(?:output|channel)s?|(?:single|dual|triple|quad)"
            r"[- ]?(?:output|channel)s?)\b|"
            r"\b(?:number\s+of\s+(?:outputs|channels)|(?:outputs|channels)\s+per\s+device)\b",
            re.I,
        )
        # A test condition is not a device count: reject rows that phrase the
        # channel as a condition ("1 channel on", "... = 25°C", "at ...").
        CONDITION = re.compile(
            r"\b(?:on|off|enabled|disabled|at\s|=\s|temperature|ta\b|tj\b|"
            r"voltage|current|range|swing|accuracy|load|package)\b",
            re.I,
        )
        for sec in ordered:
            text = _section_text(doc, sec)
            for line in _lines(text):
                if not OUT_SUBJECT.search(line) or CONDITION.search(line):
                    continue
                m = REG_OUT.search(line)
                if not m:
                    continue
                w = (m.group(1) or "").lower()
                val = WORD_N.get(w) or (int(m.group(2)) if m.group(2) else None)
                if val is None:
                    continue
                return {"value": val, "verbatim": line,
                        "table": "ROC" if sec["kind"] == "roc" else "EC",
                        "row_label": line}
        return {"not_found_verbatim": True, "reason": "no_regulated_output_wording_in_roc_ec"}
    for sec in ordered:
        rows = _table_rows(doc, sec)
        pick = _pick_row(rows, field)
        if pick:
            col = "MAX" if field == "vin_max_v" else ("MIN" if field == "vin_min_v" else "MAX")
            if col not in pick["cells"]:
                continue
            if not _unit_ok(pick.get("unit"), field):
                continue
            val = _num(pick["cells"][col])
            if field == "iout_max_a":
                val = _to_amps(val, pick.get("unit"))
            return {"value": val, "verbatim": _row_verbatim(pick),
                    "table": "ROC" if sec["kind"] == "roc" else "EC",
                    "row_label": pick["label"]}
    return {"not_found_verbatim": True, "reason": "no_matching_roc_ec_row"}


def _line_of(text: str, needle: str) -> str:
    for l in _lines(text):
        if needle in l:
            return l
    return needle


def _row_verbatim(row: dict) -> str:
    unit = f" {row['unit']}" if row.get("unit") else ""
    cells = row.get("cells") or {}
    parts = [f"{c}={cells[c]}" for c in ("MIN", "NOM", "MAX") if c in cells]
    return f"{row['label']} | " + " | ".join(parts) + unit


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--queue", type=Path, required=True)
    ap.add_argument("--pdf-root", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--dispositions-out", type=Path, required=True)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    entries = json.loads(args.queue.read_text())["entries"]
    if args.limit:
        entries = entries[: args.limit]
    rows, disp = [], []
    for e in entries:
        pn, field = e["part_number"], e["field"]
        pdf = args.pdf_root / f"ti-{pn}.pdf"
        if not pdf.exists():
            disp.append({"part_number": pn, "field": field, "domain": "ti.com",
                         "not_found_verbatim": True, "reason": "pdf_not_local",
                         "source_url": f"https://www.ti.com/lit/ds/symlink/{pn.lower()}.pdf"})
            continue
        doc = pymupdf.open(pdf)
        try:
            r = read_field(doc, field, pn)
        finally:
            doc.close()
        base = {"part_number": pn, "field": field, "domain": "ti.com",
                "pdf_sha": sha256_of(pdf),
                "source_url": f"https://www.ti.com/lit/ds/symlink/{pn.lower()}.pdf"}
        if r.get("not_found_verbatim"):
            disp.append({**base, "not_found_verbatim": True, "reason": r["reason"]})
        else:
            rows.append({**base, "value": r["value"], "verbatim": r["verbatim"],
                         "table": r["table"], "row_label": r["row_label"]})
    # one file: reads first, then disposition rows (CR contract: a field that
    # cannot be found verbatim gets a not_found_verbatim row, never inference)
    args.out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows + disp))
    args.dispositions_out.write_text(json.dumps(disp, ensure_ascii=False, indent=1))
    from collections import Counter
    print(f"entries: {len(entries)}  rows: {len(rows)}  dispositions: {len(disp)}")
    print("  by field:", dict(Counter(r["field"] for r in rows)))
    print("  by table:", dict(Counter(r["table"] for r in rows)))
    print("  disp reasons:", dict(Counter(_disp_key(d) for d in disp)))
    return 0


def _disp_key(d: dict) -> str:
    if "reason" in d and "field" in d and d["reason"] != "pdf_not_local":
        return d["reason"]
    return d.get("reason", "?")


if __name__ == "__main__":
    raise SystemExit(main())