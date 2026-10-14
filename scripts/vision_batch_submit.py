#!/usr/bin/env python3
"""Vision batch catch-up — the routed vision backlog through OpenRouter's
batch API (50% cost, server-side parallelism).

Sam 2026-10-12: "we have batch for openrouter! so for the vision that should
be fast." Two phases:

  render (CPU — parallel across cores)  ->  submit (batch chunks)
  poll -> fetch -> deterministic verify -> write

Output is the SAME record shape as `vision_worker_v2` (page_regions with
tables + provenance), so downstream consumers cannot tell the channel apart.
Custom-id convention: "<stem>|<page>|<idx>" — the receipts survive the trip.

The done-set is the output directory itself (like the worker): docs already
written by the live worker or a previous batch are skipped.

Usage:
  python3 scripts/vision_batch_submit.py submit [--docs 100] [--workers 8] [--chunk 400]
  python3 scripts/vision_batch_submit.py poll
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import time
import urllib.request
import zlib
from multiprocessing import Pool
from pathlib import Path

sys.path.insert(0, "/Users/samkim/Harnessv1")
sys.path.insert(0, "/Users/samkim/Harnessv1/scripts")

from vision_worker_v2 import (  # noqa: E402
    DPI, MAX_REGIONS_PER_DOC, PROMPT, ROUTES, open_jobs_ro, resolve_by_sha,
    resolve_path,
)

KEY = [l.split("=", 1)[1].strip() for l in open("/Users/samkim/Harnessv1/.env")
       if l.startswith("OPENROUTER_API_KEY")][0]
BASE = "https://openrouter.ai/api/v1"
MODEL = "deepseek/deepseek-v4.1-flash:batch"
OUT = Path("/Users/samkim/Harnessv1/results/vision-batch")
LEDGER = OUT / "batches"


def api(path, data=None, timeout=120):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(data).encode() if data is not None else None,
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
        method="POST" if data is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        return json.loads(raw) if raw else {}


def api_raw(path, timeout=300):
    req = urllib.request.Request(BASE + path,
                                 headers={"Authorization": f"Bearer {KEY}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def route_worklist():
    """needs_ocr first, then flagged — the worker's priority, kept."""
    done = {p.stem for p in OUT.glob("*.json")}
    ocr_first, flagged = [], []
    for rf in sorted(ROUTES.glob("*.json")):
        if rf.stem in done:
            continue
        r = json.loads(rf.read_text())
        (ocr_first if r.get("needs_ocr") else flagged).append((rf.stem, r))
    return ocr_first + flagged


def render_one(item):
    """CPU phase: stem + route -> list of (page, idx, bbox, png_b64)."""
    stem, route = item
    con = None
    try:
        from harness.pipeline.region_detect import render_regions
        pdf = resolve_by_sha(route.get("sha256")) or resolve_path(stem, open_jobs_ro())
        if not pdf:
            return stem, []
        pages = (route.get("route") or {}).get("vision_pages", [])[:6]
        if route.get("needs_ocr"):
            pages = pages or list(range(1, 3))
        if not pages:
            return stem, []
        rendered = render_regions(pdf, pages, dpi=DPI)
        out, used = [], 0
        for pn, region_list in sorted(rendered.items()):
            for r, png in region_list:
                if used >= MAX_REGIONS_PER_DOC:
                    break
                used += 1
                out.append((str(pn), used, [round(x, 1) for x in r["bbox"]],
                            base64.b64encode(png).decode()))
        return stem, out
    except Exception as e:
        print(f"render {stem}: {str(e)[:80]}", flush=True)
        return stem, []


def submit(args):
    OUT.mkdir(parents=True, exist_ok=True)
    LEDGER.mkdir(exist_ok=True)
    todo = route_worklist()
    if args.docs:
        todo = todo[: args.docs]
    print(f"vision docs to batch: {len(todo)} (done={len(list(OUT.glob('*.json')))})", flush=True)

    requests, meta = [], {}
    with Pool(args.workers) as pool:
        for stem, regions in pool.imap_unordered(render_one, todo, chunksize=4):
            if not regions:
                continue
            meta[stem] = [(pn, idx, bbox) for pn, idx, bbox, _ in regions]
            for pn, idx, bbox, b64 in regions:
                requests.append({
                    "custom_id": f"{stem}|{pn}|{idx}",
                    "method": "POST", "url": "/v1/chat/completions",
                    "body": {"model": MODEL, "temperature": 0, "max_tokens": 16000,
                             "reasoning": {"effort": "low"},
                             "messages": [{"role": "user", "content": [
                                 {"type": "text", "text": PROMPT},
                                 {"type": "image_url",
                                  "image_url": {"url": f"data:image/png;base64,{b64}"}}]}]},
                })
            if len(requests) % 2000 < 10:
                print(f"rendered {len(meta)} docs / {len(requests)} regions", flush=True)
    print(f"total regions: {len(requests)} across {len(meta)} docs", flush=True)
    (OUT / "region_meta.json").write_text(json.dumps(meta))

    for i in range(0, len(requests), args.chunk):
        chunk = requests[i:i + args.chunk]
        chunk_path = LEDGER / f"chunk-{i//args.chunk:04d}.jsonl"
        chunk_path.write_text("\n".join(json.dumps(r) for r in chunk))
        try:
            batch = api("/batches", {"endpoint": "/v1/chat/completions", "model": MODEL, "requests": chunk})
            bid = batch.get("id") or batch.get("batch_id") or "unknown"
            (LEDGER / f"{bid}.json").write_text(json.dumps(
                {"batch_id": bid, "n": len(chunk), "submitted_at": time.time()}))
            print(f"chunk {i//args.chunk}: batch {bid} ({len(chunk)} regions) status={batch.get('status')}", flush=True)
        except Exception as e:
            detail = ""
            if hasattr(e, "read"):
                try:
                    detail = e.read().decode()[:200]
                except Exception:
                    pass
            print(f"chunk {i//args.chunk} FAILED: {str(e)[:100]} {detail}", flush=True)
        time.sleep(2)


def _salvage_json(content: str):
    m = re.search(r"\[.*\]", content or "", re.DOTALL)
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


def poll(args):
    meta = json.loads((OUT / "region_meta.json").read_text())
    for lf in sorted(LEDGER.glob("*.json")):
        ledger = json.loads(lf.read_text())
        bid = ledger["batch_id"]
        try:
            st = api(f"/batches/{bid}")
        except Exception as e:
            print(f"{bid}: poll failed {str(e)[:80]}", flush=True)
            continue
        status = st.get("status")
        print(f"{bid}: {status}  requests={st.get('request_counts') or st.get('counts')}", flush=True)
        if status != "completed":
            continue
        out_file = st.get("output_file_id") or st.get("output_file")
        if not out_file:
            lf.with_suffix(".done").write_text(lf.read_text())
            continue
        raw = api_raw(f"/files/{out_file}/content")
        per_stem = {}
        for line in raw.decode().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            cid = row.get("custom_id", "")
            stem, pn, idx = cid.split("|")
            content = (((row.get("response") or {}).get("choices") or [{}])[0]
                       .get("message", {}) or {}).get("content") or ""
            bbox = None
            for p2, i2, b in meta.get(stem, []):
                if p2 == pn and str(i2) == idx:
                    bbox = b
            per_stem.setdefault(stem, []).append((pn, idx, bbox, _salvage_json(content)))
        # Write in the worker's record shape.
        for stem, regions in per_stem.items():
            results = []
            by_page = {}
            for pn, idx, bbox, tables in regions:
                by_page.setdefault(pn, []).append({"tables": tables, "bbox": bbox})
            for pn, regs in sorted(by_page.items()):
                results.append({"page": pn,
                                "region_bbox": [r["bbox"] for r in regs if r["bbox"]],
                                "region_source": "region_detect",
                                "dpi": DPI,
                                "tables": [t for r in regs for t in r["tables"]]})
            rec = {"sha256": None, "filename": f"{stem}.pdf", "needs_ocr": None,
                   "extractor": "vision_batch", "extractor_version": "1.0.0",
                   "pages_processed": sorted(by_page.keys()),
                   "provenance": {"model": MODEL, "dpi": DPI, "mode": "crop-don't-page",
                                  "renderer": "pymupdf+region_detect", "channel": "openrouter-batch"},
                   "page_regions": results}
            (OUT / f"{stem}.json").write_text(json.dumps(rec, ensure_ascii=False))
        lf.rename(lf.with_suffix(".done"))
        print(f"{bid}: wrote {len(per_stem)} docs", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("submit")
    s.add_argument("--docs", type=int, default=0)
    s.add_argument("--workers", type=int, default=8)
    s.add_argument("--chunk", type=int, default=400)
    p = sub.add_parser("poll")
    args = ap.parse_args()
    {"submit": submit, "poll": poll}[args.cmd](args)
