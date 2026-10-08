#!/usr/bin/env python3
"""mcu-apps-short-20260918: page-1 APPLICATIONS from HELD datasheets (vault).

CR am-mu6hh4s6-78a4 contract: bytes-first from /Volumes/M5_4TB/vault (never
fetch); verbatim application strings, one per bullet; family datasheet OK for
family pages (scope recorded); single-part datasheet on a multi-part page is
scope: single_part; refuse product/board/kit/part-number strings into
refused_not_application with reasons; >=3 distinct = pass, fewer = raw;
datasheets only (manuals/user guides are not read for this lane).

Row: {node_id, part_number_used, pdf_sha, source_url, page,
      section_heading_verbatim, applications, status, notes}
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

import fitz

sys.path.insert(0, str(Path(__file__).resolve().parent))
from extract_power_descriptions import _page_lines  # noqa: E402

VAULT = Path("/Volumes/M5_4TB/vault")
QUEUE = Path("/Volumes/M5_4TB/exports/cr_requests/mcu-apps-short-queue-20260918.json")

# page-label -> (doc sha256 in vault, scope, note). Selected from the vault
# catalog: family datasheets preferred over member datasheets; manuals and
# user guides refused for this lane.
DOC_MAP = {
    "SAMD21":   ("18ca1ccab5aa", "family_member_ds", "ATSAMD21E18A datasheet (SAMD21 family content)"),
    "MCX C":    ("30d553d2a0f5", "family_member_ds", "MCXC141 datasheet"),
    "PIC32MX3": ("370a810479dc", "family", "PIC32MX330/350/370/430/450/470 Family DS60001185H"),
    "PIC32MX2": ("fe22cdee28cb", "family_member_ds", "PIC32MX250F128B member DS (page has 1 part)"),
    "PIC32MX4": ("370a810479dc", "family", "same family DS covers MX470"),
    "SAMC20":   ("0ce570f4a02a", "family", "SAMC20/C21 Family DS60001479D"),
    "SAML10":   ("f8a80be500b2", "family_member_ds", "ATSAML10E16A member DS (page has 1 part)"),
    "nRF7000":  ("36536bf4e052", "family", "nRF7000 Product Specification v1.1"),
    "RA0E3":    ("4f45a2786ac4", "family", "RA0E3 Group Datasheet"),
    "PIC32MX":  ("2413ac5834b1", "family_subset", "PIC32MX5XX/6XX/7XX Family DS covers part of the 112-part page"),
}

APPS_HEADING = re.compile(r"^\s*(target\s+applications?|applications?)\s*:?\s*$", re.I)
SECTION_STOP = re.compile(
    r"^\s*(?:features?|key\s+features|description|general\s+description|package\s+type|"
    r"ordering\s+information|device\s+summary|summary|overview|specifications?|"
    r"electrical\s+characteristics|absolute\s+maximum|typical\s+applications?|"
    r"applications?\s+information|pin\s+(configuration|assignments?)|"
    r"revision\s+history|table\s+of\s+contents|1\s+applications)\b", re.I)

# refuse list per CR rule 4
REFUSE = re.compile(
    r"\b(evaluation|eva\s?kit|dev\s?kit|starter\s+kit|demo|board|boards|dk\d|"
    r"module|shield|adapter|accessory|tool|programmer|debugger|emulator)\b", re.I)
PARTNUM = re.compile(r"\b[A-Z]{2,}[A-Z0-9]*\d[A-Z0-9]*\b")
BULLET = re.compile(r"^\s*(?:[•▪◦\u2022\u25cf\u2023\u2043*]|[-–—]|\d+[.)])\s*", re.I)
GD_STYLE = re.compile(r"especially\s+in\s+areas\s+such\s+as", re.I)


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def cas_path(sha: str) -> Path:
    return VAULT / "cas" / sha[:2] / f"{sha}.pdf"


SPEC_TOKEN = re.compile(
    r"\b(?:dBm|GHz|mA\s*@|RX\s|output\s+power|supply\s+voltage|temperature\s+range|"
    r"QFN\d|package|IEEE\s|host\s+interface|antenna\s+port|coexistence)\b", re.I)


def find_apps(doc) -> dict:
    """Locate an Applications section on the first pages and collect its
    verbatim strings. Three layouts handled:
      - heading + bullet block (Microchip/NXP style)
      - heading + same-column stacked items (Nordic PS sidebar: the left
        column is Key characteristics and must never leak in)
      - heading + numbered/bare list (Renesas style)
    """
    for pno in range(min(4, doc.page_count)):
        lines = _page_lines(doc[pno])
        for i, ln in enumerate(lines):
            if not APPS_HEADING.match(ln["text"]):
                continue
            items, refused = [], []
            # Layout 1: same-column stacked items (any bullet shape), gap-bounded
            col = [l for l in lines[i + 1:]
                   if abs(l["x"] - ln["x"]) <= 40.0 and 0 < l["y"] - ln["y"] <= 200]
            prev_y = None
            for l in col:
                if prev_y is not None and l["y"] - prev_y > 30.0:
                    break
                t = BULLET.sub("", l["text"]).strip()
                if not t or SECTION_STOP.match(t):
                    break
                if SPEC_TOKEN.search(t):
                    prev_y = l["y"]
                    continue
                if REFUSE.search(t) or (PARTNUM.search(t) and len(t.split()) <= 4):
                    refused.append({"string": t, "reason": "product_or_board_name"})
                elif len(t) >= 3:
                    items.append(t)
                prev_y = l["y"]
            if len(items) >= 2:
                return {"page": pno + 1, "heading": ln["text"], "items": items,
                        "refused": refused}
            # Layout 2: bullet block below the heading across columns
            items, refused, j = [], [], i + 1
            while j < len(lines) and len(items) < 24:
                t = lines[j]["text"]
                if SECTION_STOP.match(t) and j > i + 1:
                    break
                m = BULLET.match(t)
                if not m and items and len(t) > 4 and not t.isupper():
                    items[-1] += " " + t
                    j += 1
                    continue
                if not m:
                    if items:
                        break
                    j += 1
                    continue
                item = BULLET.sub("", t).strip()
                if not item:
                    j += 1
                    continue
                if REFUSE.search(item) or (PARTNUM.search(item) and len(item.split()) <= 4):
                    refused.append({"string": item, "reason": "product_or_board_name"})
                elif len(item) >= 3:
                    items.append(item)
                j += 1
            if items or refused:
                return {"page": pno + 1, "heading": ln["text"], "items": items,
                        "refused": refused}
    return {"page": None, "heading": None, "items": [], "refused": []}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--queue", type=Path, default=QUEUE)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    pages = json.loads(args.queue.read_text())["pages"]
    rows = []
    for r in pages:
        label = r["label"]
        base = {"node_id": r["node_id"], "source_url": r.get("source_url")}
        entry = DOC_MAP.get(label)
        if entry is None:
            rows.append({**base, "page_label": label, "part_number_used": None,
                         "pdf_sha": None, "page": None, "section_heading_verbatim": None,
                         "applications": [], "status": "none",
                         "notes": "not_held (no datasheet in vault; manuals/RMs not read for this lane)"})
            continue
        sha12, scope, note = entry
        # resolve full sha from catalog
        full = None
        for line in (VAULT / "catalog/documents.jsonl").open():
            if sha12 in line:
                full = json.loads(line)["sha256"]
                break
        if full is None or not cas_path(full).exists():
            rows.append({**base, "page_label": label, "part_number_used": None,
                         "pdf_sha": None, "page": None, "section_heading_verbatim": None,
                         "applications": [], "status": "none",
                         "notes": "catalog_sha_missing_in_cas"})
            continue
        doc = fitz.open(cas_path(full))
        got = find_apps(doc)
        doc.close()
        distinct = list(dict.fromkeys(got["items"]))
        status = "pass" if len(distinct) >= 3 else "raw"
        rows.append({
            **base, "page_label": label,
            "part_number_used": None,
            "pdf_sha": full, "page": got["page"],
            "section_heading_verbatim": got["heading"],
            "applications": distinct,
            "status": status if distinct else "none",
            "scope": scope,
            **({"refused_not_application": got["refused"]} if got["refused"] else {}),
            "notes": note if distinct else (note + "; no applications section found"),
        })
    args.out.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in rows))
    for x in rows:
        print(f"{x['page_label']:10} {x['status']:5} n={len(x['applications'])} "
              f"p{x.get('page')} :: {[a[:28] for a in x['applications'][:4]]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())