#!/usr/bin/env python3
"""EOL/OBSOLETE watermark detection endpoint (CR am-mu30g8mr-a3a0, 2026-09-15).

Vendor datasheets carry lifecycle watermarks as IMAGE stamps (diagonal
banners), invisible to the text layer. Detection therefore runs an image pass
on rendered pages (per the spec: text-layer search alone is insufficient by
construction); the text layer is scanned only as recorded corroboration.

Verdict schema (per spec):
    pdf_sha256, watermark_present, watermark_text_verbatim, pages_stamped,
    part_scope_named, method, endpoint_version
plus honesty fields: pages_sampled, text_layer_eol_keywords (corroboration,
never the verdict), per_call receipts appended to a JSONL.

Rules implemented:
 1. Verbatim stamp text; no normalisation.
 2. part_scope_named comes from part-number tokens IN the stamp text, matched
    with exact word boundaries (FAN5340 can never mark FAN53540).
 3. Image-based detection: every verdict is grounded in a rendered page.
 4. Absence of a detected stamp == watermark_present: false, which is NOT
    evidence the part is active (lifecycle ground truth is Samson's alone,
    per the crawler's standing rule).

Vision lane: Anthropic Sonnet (the harness teacher lane; keys in the shared
credentials env). ds_v4_vision/glm5v_turbo lanes in models.yaml have no keys
on this box. Batch submission follows the harness cloud rules; single-doc
smoke uses the synchronous path.

    python3 scripts/eol_watermark.py --pdf <path> [--pdf ...] [--jsonl-out out.jsonl]
    python3 scripts/eol_watermark.py --batch-dir <dir> --jsonl-out out.jsonl
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

import fitz

ENDPOINT_VERSION = "eol-watermark-v1"
RENDER_DPI = 220
MAX_SYNC_PAGES = 6  # cost guard per document

EOL_TEXT_KEYWORDS = re.compile(
    r"\b(obsolete|discontinued|end\s+of\s+life|eol\b|not\s+recommended\s+for\s+new\s+designs?|nrnd|last\s+time\s+buy)", re.I,
)
# A lifecycle stamp names the part(s) it covers. Tokens that look like vendor
# part numbers (letters+digits, at least one of each), exact word boundaries.
PART_TOKEN = re.compile(r"\b([A-Z][A-Z0-9]*\d[A-Z0-9]*)\b")

PROMPT = """You are inspecting a rendered datasheet page for VENDOR LIFECYCLE WATERMARK STAMPS: large overlay text, usually diagonal/rotated banners such as "OBSOLETE", "DISCONTINUED", "NOT RECOMMENDED FOR NEW DESIGNS", "END OF LIFE", printed OVER the page content, often gray/red outline text.

Transcribe any watermark stamp EXACTLY as printed, character for character, in reading order. Preserve wording, do not fix or normalise anything.

Do NOT transcribe body text, tables, headings, footers, logos, or part-number tables -- ONLY overlay watermark stamps.

Respond with JSON only:
{"watermark_present": true|false, "watermark_text_verbatim": "<exact transcription>"|null, "confidence": "high"|"low"}"""


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_key() -> str:
    env = Path("/Volumes/M5_4TB/.credentials/api_keys.env")
    for line in env.read_text().splitlines():
        if line.startswith("ANTHROPIC_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("ANTHROPIC_API_KEY missing from credentials env")


def _anthropic_image_call(png: bytes, key: str) -> dict:
    body = {
        "model": "claude-sonnet-5",
        "max_tokens": 1024,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image", "source": {
                    "type": "base64", "media_type": "image/png",
                    "data": base64.b64encode(png).decode("ascii")}},
                {"type": "text", "text": PROMPT},
            ],
        }],
    }
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(body).encode(),
        headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=240) as r:
        out = json.load(r)
    text = "".join(b.get("text", "") for b in out.get("content", []))
    usage = out.get("usage", {})
    m = re.search(r"\{.*\}", text, re.S)
    parsed = json.loads(m.group(0)) if m else {"watermark_present": False,
                                               "watermark_text_verbatim": None,
                                               "confidence": "unparsed"}
    parsed["_usage"] = usage
    parsed["_raw_len"] = len(text)
    return parsed


def sample_pages(n_pages: int, max_pages: int = MAX_SYNC_PAGES) -> list[int]:
    if n_pages <= max_pages:
        return list(range(1, n_pages + 1))
    # first two, middle, last two -- stamps repeat; sampling is recorded
    mid = n_pages // 2
    picks = sorted({1, 2, mid, mid + 1, n_pages - 1, n_pages})
    return picks[:max_pages]


def detect(pdf_path: Path, key: str, receipt_path: Path | None = None,
           max_pages: int = MAX_SYNC_PAGES) -> dict:
    doc = fitz.open(pdf_path)
    n = doc.page_count
    pages = sample_pages(n, max_pages)
    stamped: dict[int, str] = {}
    text_kw: set[str] = set()
    for pno in pages:
        pg = doc[pno - 1]
        # recorded corroboration only -- never the verdict
        for kw in set(EOL_TEXT_KEYWORDS.findall(pg.get_text())):
            text_kw.add(kw.lower())
        png = pg.get_pixmap(dpi=RENDER_DPI).tobytes("png")
        t0 = time.time()
        verdict = _anthropic_image_call(png, key)
        if receipt_path is not None:
            with receipt_path.open("a") as fh:
                fh.write(json.dumps({
                    "work_id": pdf_path.stem, "page": pno, "model": "claude-sonnet-5",
                    "finish": "ok", "parse_ok": verdict.get("confidence") != "unparsed",
                    "watermark": verdict.get("watermark_present"), "confidence": verdict.get("confidence"),
                    "in_tokens": verdict["_usage"].get("input_tokens"),
                    "out_tokens": verdict["_usage"].get("output_tokens"),
                    "image_sha256": hashlib.sha256(png).hexdigest(),
                    "latency_s": round(time.time() - t0, 1), "ts": time.time(),
                }) + "\n")
        if verdict.get("watermark_present") and verdict.get("watermark_text_verbatim"):
            stamped[pno] = verdict["watermark_text_verbatim"]
    doc.close()

    verbatim = None
    if stamped:
        # verbatim assembly: unique stamp texts in first-seen page order
        seen, parts = [], []
        for pno in sorted(stamped):
            t = stamped[pno].strip()
            if t not in seen:
                seen.append(t)
        verbatim = " | ".join(seen)
        for tok in PART_TOKEN.findall(verbatim.upper()):
            parts.append(tok)
    return {
        "pdf_sha256": sha256_of(pdf_path),
        "watermark_present": bool(stamped),
        "watermark_text_verbatim": verbatim,
        "pages_stamped": sorted(stamped),
        "part_scope_named": sorted(set(parts)) if stamped else [],
        "method": "visual-llm",
        "endpoint_version": ENDPOINT_VERSION,
        "pages_sampled": pages,
        "page_count": n,
        "text_layer_eol_keywords": sorted(text_kw),
        "note": "absence of a detected stamp is NOT evidence the part is active",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pdf", action="append", type=Path, default=[])
    ap.add_argument("--batch-dir", action="append", type=Path, default=[])
    ap.add_argument("--jsonl-out", type=Path, required=True)
    ap.add_argument("--receipts", type=Path, default=Path("/tmp/eol-watermark-receipts.jsonl"))
    ap.add_argument("--max-pages", type=int, default=MAX_SYNC_PAGES)
    args = ap.parse_args()

    pdfs = list(args.pdf)
    for d in args.batch_dir or []:
        pdfs += sorted(p for p in d.iterdir() if p.suffix.lower() == ".pdf")
    if not pdfs:
        ap.error("no PDFs given")
    key = _load_key()
    out_fh = args.jsonl_out.open("w")
    for pdf in pdfs:
        row = detect(pdf, key, args.receipts, args.max_pages)
        out_fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        out_fh.flush()
        print(f"{pdf.name}: present={row['watermark_present']} pages={row['pages_stamped']} "
              f"text_kw={row['text_layer_eol_keywords']}")
    out_fh.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())