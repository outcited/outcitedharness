import os
#!/usr/bin/env python3
"""Vision-pass worker v2: crop-don't-page.

Renders TABLE REGIONS (not full pages) at 200 DPI via region_detect, sends
to V4.1 vision, writes results tagged with page + region bbox + dpi.
Priority: needs_ocr docs first, then routed pages. Source PDFs resolved via
the pipeline DB.
"""

import base64
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, "/Users/samkim/Harnessv1")

from harness.pipeline import store
from harness.pipeline.region_detect import render_regions

ROUTES = Path(os.environ.get("ROUTES_DIR", "/Volumes/M5_4TB/extract-results/routes"))
OUT = Path("/Volumes/M5_4TB/extract-results/vision-v2")
V41 = "http://100.116.221.82:8888/v1/chat/completions"
MODEL = "deepseek-v4.1-flash"
MAX_REGIONS_PER_DOC = 10
DPI = 200

PROMPT = """You are reading a table region cropped from an electronic datasheet page.
Extract every table in this region as JSON. For each table:
{"table_title": "<printed title if visible>",
 "columns": ["<header>", ...],
 "rows": [{"cells": {"<column>": "<value>"}, "bbox": [x1, y1, x2, y2]}]}
Each row's bbox is its location within THIS image in pixels (origin top-left).
Copy cell values exactly as printed. If the region contains no table, output [].
Output ONLY the JSON array."""


def v41_image(b64_png, timeout=300):
    last_err = None
    for attempt in range(2):
        body = json.dumps({
            "model": MODEL, "temperature": 0, "max_tokens": 3000,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": PROMPT},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64_png}"}},
            ]}],
        }).encode()
        req = urllib.request.Request(V41, data=body, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                c = json.loads(r.read())["choices"][0]["message"]["content"] or ""
            break
        except Exception as e:
            last_err = e
            continue
    else:
        return [{"error": f"vision_timeout: {str(last_err)[:60]}"}]
    m = re.search(r"\[.*\]", c, re.DOTALL)
    if not m:
        return []
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        cut = m.group(0).rfind("}")
        if cut > 0:
            try:
                return json.loads(m.group(0)[: cut + 1] + "]")
            except json.JSONDecodeError:
                return []
        return []


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
    pages = route["route"]["vision_pages"][:6]
    if not pages:
        return None, "no_vision_pages"
    try:
        rendered = render_regions(pdf, pages, dpi=DPI)
    except Exception as e:
        return None, f"render_error:{str(e)[:60]}"
    from harness.pipeline.crop_verify import verify_tables
    regions_used = 0
    results = []
    for pn, region_list in sorted(rendered.items()):
        for r, png in region_list:
            if regions_used >= MAX_REGIONS_PER_DOC:
                break
            regions_used += 1
            tables = v41_image(base64.b64encode(png).decode())
            region_result = {
                "page": pn,
                "region_bbox": [round(x, 1) for x in r["bbox"]],
                "region_source": r["source"],
                "dpi": DPI,
                "tables": tables,
            }
            try:
                region_result["crop_verdicts"] = verify_tables(pdf, pn, region_result)
            except Exception as e:
                region_result["crop_verdicts"] = [{"error": str(e)[:80]}]
            results.append(region_result)
    rec = {
        "sha256": route.get("sha256"),
        "filename": route["filename"],
        "needs_ocr": route.get("needs_ocr"),
        "extractor": "vision_worker",
        "extractor_version": "2.0.0",
        "pages_processed": list(rendered.keys()),
        "provenance": {"model": MODEL, "dpi": DPI, "mode": "crop-don't-page",
                       "renderer": "pymupdf+region_detect"},
        "page_regions": results,
    }
    (OUT / f"{stem}.json").write_text(json.dumps(rec, ensure_ascii=False))
    return regions_used, None


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    con = store.connect()
    done = {p.stem for p in OUT.glob("*.json")}
    while True:
        routes = sorted(ROUTES.glob("*.json"))
        ocr_first, flagged = [], []
        for rf in routes:
            if rf.stem in done:
                continue
            r = json.loads(rf.read_text())
            (ocr_first if r.get("needs_ocr") else flagged).append(r)
        batch = (ocr_first + flagged)[:6]
        if not batch:
            print("no vision work; sleeping", flush=True)
            time.sleep(120)
            continue
        for r in batch:
            n, err = process(r, con)
            print(f"{r['filename']}: regions={n} err={err}", flush=True)
            if err != "no_source_path":
                done.add(Path(r["filename"]).stem)
        time.sleep(5)


if __name__ == "__main__":
    main()
