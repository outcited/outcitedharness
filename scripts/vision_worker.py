#!/usr/bin/env python3
"""Vision-pass worker: routes -> rendered pages -> V4.1 vision -> results.

Priority: needs_ocr docs first (no text layer at all), then vision-flagged
pages (pinouts, ballouts, packages). Source PDFs resolved via the pipeline
DB (corpus_key -> source_path). Results carry page-level provenance.
"""

import base64
import io
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, "/Users/samkim/Harnessv1")
from harness.pipeline import store

import pymupdf

ROUTES = Path("/Volumes/M5_4TB/extract-results/routes")
OUT = Path("/Volumes/M5_4TB/extract-results/vision")
V41 = "http://100.116.221.82:8888/v1/chat/completions"
MODEL = "deepseek-v4.1-flash"
MAX_PAGES_PER_DOC = 8
DPI = 150

PROMPT = """You are reading a page image from an electronic component datasheet.
Extract every table you can see as JSON rows. For each table:
{"table_title": "<printed title>", "columns": ["<header>", ...],
 "rows": [["<cell>", ...], ...]}
Also extract any pin/ball designators visible as a table if present.
Output ONLY a JSON array of tables. If the page has no tables, output []."""


def render_pages(pdf_path, page_numbers):
    doc = pymupdf.open(pdf_path)
    out = {}
    for pn in page_numbers:
        if pn > len(doc):
            continue
        pix = doc[pn - 1].get_pixmap(dpi=DPI)
        out[pn] = base64.b64encode(pix.tobytes("png")).decode()
    doc.close()
    return out


def v41_page(b64, timeout=180):
    body = json.dumps({
        "model": MODEL, "temperature": 0, "max_tokens": 3000,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": PROMPT},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
        ]}],
    }).encode()
    req = urllib.request.Request(V41, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        c = json.loads(r.read())["choices"][0]["message"]["content"] or ""
    m = re.search(r"\[.*\]", c, re.DOTALL)
    return json.loads(m.group(0)) if m else []


def resolve_path(stem, con):
    row = con.execute("SELECT source_path FROM jobs WHERE corpus_key=? LIMIT 1",
                      (f"pdf:{stem}",)).fetchone()
    if row and row["source_path"] and Path(row["source_path"]).exists():
        return row["source_path"]
    return None


def process(route, con):
    stem = Path(route["filename"]).stem
    pdf = resolve_path(stem, con)
    if not pdf:
        return None, "no_source_path"
    pages = route["route"]["vision_pages"][:MAX_PAGES_PER_DOC]
    if not pages:
        return None, "no_vision_pages"
    rendered = render_pages(pdf, pages)
    tables = []
    for pn, b64 in rendered.items():
        try:
            page_tables = v41_page(b64)
        except Exception as e:
            tables.append({"page": pn, "error": str(e)[:120]})
            continue
        tables.append({"page": pn, "tables": page_tables})
    rec = {
        "sha256": route.get("sha256"),
        "filename": route["filename"],
        "needs_ocr": route.get("needs_ocr"),
        "extractor": "vision_worker",
        "extractor_version": "1.0.0",
        "pages_processed": list(rendered.keys()),
        "provenance": {"model": MODEL, "dpi": DPI, "renderer": "pymupdf"},
        "page_tables": tables,
    }
    (OUT / f"{stem}.json").write_text(json.dumps(rec, ensure_ascii=False))
    return len(rendered), None


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    con = store.connect()
    done = {p.stem for p in OUT.glob("*.json")}
    while True:
        routes = sorted(ROUTES.glob("*.json"))
        ocr_first = []
        flagged = []
        for rf in routes:
            if rf.stem in done:
                continue
            r = json.loads(rf.read_text())
            if r.get("needs_ocr"):
                ocr_first.append(r)
            elif r["route"].get("vision_pages"):
                flagged.append(r)
        batch = (ocr_first + flagged)[:4]
        if not batch:
            print("no vision work; sleeping", flush=True)
            time.sleep(120)
            continue
        for r in batch:
            n, err = process(r, con)
            print(f"{r['filename']}: pages={n} err={err}", flush=True)
            if err != "no_source_path":
                done.add(Path(r["filename"]).stem)
        time.sleep(5)


if __name__ == "__main__":
    main()
