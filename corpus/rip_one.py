#!/usr/bin/env python3
"""rip_one.py — practice rip of ONE document: per-page lane index, measured.

Prototype of the universal document index pass:
  open once -> TOC section map -> per-page: cached text (or fresh), lane tags,
  gated find_tables (only where pin signals fire) -> compact record.

Emits: {sha, part, pages: [{p, sec, lanes[], tables, chars, ex}]} + timing.
usage: rip_one.py <pdf-path> [out.json]
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import time
from pathlib import Path

import pymupdf

ROOT = Path("/Users/samkim/Harnessv1")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from harness.electronics.page_index import LANE_PATTERNS  # lane regexes (sanctioned)

# lanes from page_index's own vocabulary
LANES = {name: list(pats) for name, pats in LANE_PATTERNS.items()}
BALL_MAP = re.compile(r"\b(ball|BGA|CBGA)\b", re.I)

T_START = time.time()


def log(msg: str) -> None:
    print(f"[{time.time() - T_START:7.2f}s] {msg}", flush=True)


pdf_path = Path(sys.argv[1])
out_path = Path(sys.argv[2]) if len(sys.argv) > 2 else None

t0 = time.time()
data = pdf_path.read_bytes()
sha = hashlib.sha256(data).hexdigest()
t_read = time.time() - t0
log(f"read {len(data)/1e6:.1f} MB, sha {sha[:12]} in {t_read:.2f}s")

t0 = time.time()
doc = pymupdf.open(stream=data, filetype="pdf")
n_pages = doc.page_count
t_open = time.time() - t0
log(f"open: {n_pages} pages in {t_open:.2f}s")

# --- TOC section map -------------------------------------------------------
t0 = time.time()
toc = doc.get_toc(simple=True)  # [[level, title, page], ...]
t_toc = time.time() - t0
log(f"TOC: {len(toc)} entries in {t_toc:.2f}s")

# section map: page -> deepest section title up to that page
sections: list[tuple[int, str]] = []
sec_by_page: dict[int, str] = {}
if toc:
    stack: list[tuple[int, str]] = []
    entries = sorted(toc, key=lambda e: e[2])
    cursor = 1
    for lvl, title, page in entries:
        while stack and stack[-1][0] >= lvl:
            stack.pop()
        stack.append((lvl, title.strip()))
        for p in range(cursor, min(page, n_pages + 1)):
            sec_by_page[p] = " > ".join(t for _, t in stack[-3:])
        cursor = max(cursor, page)
    for p in range(cursor, n_pages + 1):
        sec_by_page[p] = sec_by_page.get(p, "")

# --- per-page rip ----------------------------------------------------------
pages_out = []
lanes_total: dict[str, int] = {}
tables_pages = 0
t_scan = t_tables = 0.0
t0 = time.time()
for pno in range(n_pages):
    page = doc[pno]
    t1 = time.time()
    text = page.get_text("text")
    t_scan += time.time() - t1

    lanes = []
    excerpt = None
    for name, pats in LANES.items():
        for pat in pats:
            m = pat.search(text)
            if m:
                lanes.append(name)
                lanes_total[name] = lanes_total.get(name, 0) + 1
                if excerpt is None:
                    s = max(0, m.start() - 40)
                    excerpt = " ".join(text[s:m.end() + 60].split())[:120]
                break

    # gated expensive call: find_tables only where pin/ball signals fire
    n_tables = 0
    if any(l in ("pin_or_ball", "pin_semantics") for l in lanes) or BALL_MAP.search(text):
        t1 = time.time()
        try:
            n_tables = len(page.find_tables().tables)
        except Exception:
            n_tables = -1
        t_tables += time.time() - t1
        tables_pages += 1

    pages_out.append({
        "p": pno + 1,
        "sec": sec_by_page.get(pno + 1, "")[-60:],
        "lanes": lanes,
        "t": n_tables,
        "c": len(text),
        **({"ex": excerpt} if excerpt else {}),
    })
t_scan_total = time.time() - t0
log(f"pages: {n_pages} ripped in {t_scan_total:.2f}s (text {t_scan:.1f}s, gated find_tables {t_tables:.1f}s on {tables_pages} pages)")

t0 = time.time()
doc.close()
t_close = time.time() - t0

record = {
    "sha": sha,
    "source": str(pdf_path),
    "pages": n_pages,
    "lanes_total": lanes_total,
    "pages_rip": pages_out,
}
blob = json.dumps(record, ensure_ascii=False)
if out_path:
    out_path.write_text(blob)
log(f"close {t_close:.2f}s | record {len(blob)/1024:.0f} KB ({len(blob)/n_pages:.0f} B/page)"
    + (f" -> {out_path}" if out_path else ""))
log(f"lanes found: {json.dumps(lanes_total)}")
log(f"TOTAL WALL: {time.time() - T_START:.2f}s")
