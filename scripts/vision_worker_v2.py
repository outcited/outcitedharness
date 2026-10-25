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
import sys
import time
import urllib.request
import zlib
from pathlib import Path

sys.path.insert(0, "/Users/samkim/Harnessv1")

from harness.model_result import FAILED, failed, parse_json_array
from harness.pipeline import store
from harness.pipeline.region_detect import render_regions

ROUTES = Path(os.environ.get("ROUTES_DIR", "/Volumes/M5_4TB/extract-results/routes"))
OUT = Path(os.environ.get("VISION_OUT", "/Volumes/M5_4TB/extract-results/vision-v2"))
V41 = "http://100.116.221.82:8888/v1/chat/completions"
OPENROUTER = "https://openrouter.ai/api/v1/chat/completions"
CLOUD_MODEL = "deepseek/deepseek-v4.1-flash"
# Catch-up mode (Sam, 2026-10-12): "do not wait for local and free
# resources — we are paying to catch up." VISION_TARGET=cloud routes every
# vision call to OpenRouter DeepSeek 4.1 Flash; local TP4 stays the default
# for steady state. The measured OpenRouter recipe (MODEL_SETTINGS): the
# model reasons by default — `reasoning: {effort: low}` + a budget that
# holds thinking + answer, or the content comes back empty.
VISION_TARGET = os.environ.get("VISION_TARGET", "local")
# Sharding (catch-up scale): VISION_SHARD=i/n routes each instance a slice of
# the backlog so N workers run without double-processing (the done-set is
# per-OUT-dir, shared).
SHARD = os.environ.get("VISION_SHARD", "0/1")
_SHARD_I, _SHARD_N = (int(x) for x in SHARD.split("/"))
MODEL = CLOUD_MODEL if VISION_TARGET == "cloud" else "deepseek-v4.1-flash"
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
    if VISION_TARGET == "cloud":
        key = [l.split("=", 1)[1].strip() for l in open("/Users/samkim/Harnessv1/.env")
               if l.startswith("OPENROUTER_API_KEY")][0]
        endpoint, model = OPENROUTER, CLOUD_MODEL
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {key}",
                   "HTTP-Referer": "https://localhost/harness", "X-Title": "vision-burn"}
        body = json.dumps({
            "model": model, "temperature": 0, "max_tokens": 16000,
            "reasoning": {"effort": "low"},
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": PROMPT},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64_png}"}},
            ]}],
        }).encode()
    else:
        endpoint, model = V41, MODEL
        headers = {"Content-Type": "application/json"}
        body = json.dumps({
            "model": model, "temperature": 0, "max_tokens": 3000,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": PROMPT},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64_png}"}},
            ]}],
        }).encode()
    for attempt in range(2):
        attempts = attempt + 1
        req = urllib.request.Request(endpoint, data=body, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                payload = json.loads(r.read())
            choice = payload["choices"][0]
            return parse_json_array(
                choice["message"]["content"],
                attempts=attempts,
                finish_reason=choice.get("finish_reason"),
                model=model,
            )
        except Exception as e:
            last_err = e
            continue
    return failed(f"vision_timeout: {str(last_err)[:60]}", attempts=2, model=model)


def resolve_path(stem, con):
    if con is None:
        return None
    try:
        row = con.execute("SELECT source_path FROM jobs WHERE corpus_key=? LIMIT 1",
                          (f"pdf:{stem}",)).fetchone()
    except Exception:
        return None
    if row and row["source_path"] and Path(row["source_path"]).exists():
        return row["source_path"]
    return None


_BLOB_ROOT = Path("/Volumes/M5_4TB/exports/corpus-deliveries")


def resolve_by_sha(sha):
    """Content-addressed fallback: the corpus blobs are named by sha256."""
    if not sha or len(sha) != 64:
        return None
    hits = list(_BLOB_ROOT.glob(f"*/blobs/{sha[:2]}/{sha[2:4]}/{sha}.pdf"))
    return str(hits[0]) if hits else None


def open_jobs_ro():
    """Read-only jobs lookups. The burn holds the pipeline.db write lock, and
    `store.connect()` writes (WAL + schema) — never call it from this worker.
    """
    import sqlite3
    path = os.environ.get("PIPELINE_DB", "/Volumes/M5_4TB/extract-results/pipeline.db")
    # immutable=1 is required while the burn holds the write lock (mode=ro
    # alone still fails on a locked WAL). Torn-read risk is bounded: this
    # worker only reads stable jobs rows, never the burn's write hot zone.
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True, timeout=5)
        con.row_factory = sqlite3.Row
        return con
    except Exception:
        return None


def process(route, con):
    stem = Path(route["filename"]).stem
    pdf = resolve_by_sha(route.get("sha256")) or resolve_path(stem, con)
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
    failures = []
    for pn, region_list in sorted(rendered.items()):
        for r, png in region_list:
            if regions_used >= MAX_REGIONS_PER_DOC:
                break
            regions_used += 1
            vision = v41_image(base64.b64encode(png).decode())
            region_result = {
                "page": pn,
                "region_bbox": [round(x, 1) for x in r["bbox"]],
                "region_source": r["source"],
                "dpi": DPI,
                "tables": vision.rows,
                "vision_status": vision.status,
                "vision_reason": vision.reason,
                "vision_attempts": vision.attempts,
            }
            if vision.status == FAILED:
                failures.append({"page": pn, **vision.as_receipt()})
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
        "extraction_status": "FAILED" if failures else "OK",
        "vision_failures": failures,
        "page_regions": results,
    }
    (OUT / f"{stem}.json").write_text(json.dumps(rec, ensure_ascii=False))
    return regions_used, (f"vision_failed:{len(failures)}" if failures else None)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    con = open_jobs_ro()
    done = {p.stem for p in OUT.glob("*.json")}
    while True:
        routes = sorted(ROUTES.glob("*.json"))
        ocr_first, flagged = [], []
        for rf in routes:
            if rf.stem in done:
                continue
            if zlib.crc32(rf.stem.encode()) % _SHARD_N != _SHARD_I:
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
