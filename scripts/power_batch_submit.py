#!/usr/bin/env python3
"""Submit power extraction batch to OpenRouter DeepSeek (adapted from proven pin_batch_submit).

Reads batch-queue.jsonl (stem pairs), extracts substrate text, submits to
OpenRouter batch API in chunks. Proven API interaction from pin batch lane.

Usage: python3 scripts/power_batch_submit.py \
    --queue results/production-power-lane/batch-queue.jsonl \
    --requests-dir results/production-power-lane/batches [--chunk 200]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from collections import OrderedDict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OR_BATCHES = "https://openrouter.ai/api/v1/batches"
MODEL = "deepseek/deepseek-v4.1-flash"
MAX_CHARS = 28000

POWER_PROMPT = (
    "You are a datasheet parametric extraction engine. Given datasheet pages, "
    "extract the parametric table rows for the requested part. Every value MUST carry a quote "
    "copied character-for-character from the text, plus the ROW and COLUMN headers that govern "
    "the cell — if you cannot quote it or name its row/column, emit null. "
    'Output ONLY JSON: [{"symbol": str, "value": number, "unit": str, "qualifier": str|null, '
    '"condition": str|null, "quote": "<verbatim span>", "row_header": "<row label>", '
    '"column_header": "<column label>"}]'
)

SUBSTRATES = ROOT / "results" / "substrates"


def _key() -> str:
    env = os.environ.get("OPENROUTER_API_KEY")
    if env:
        return env
    for line in (ROOT / ".env").read_text().splitlines():
        if line.startswith("OPENROUTER_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("OPENROUTER_API_KEY missing")


def substrate(stem: str) -> str | None:
    try:
        p = SUBSTRATES / f"{stem}.json"
        d = json.loads(p.read_text())
        text = ""
        for page in d.get("pages", []):
            text += (page.get("text", "") or "") + "\n"
        return text[:MAX_CHARS] if text.strip() else None
    except Exception:
        return None


def make_request(item: dict) -> OrderedDict | None:
    stem = item.get("stem") or item.get("sha")
    text = substrate(stem)
    if not text:
        return None
    return OrderedDict([
        ("custom_id", f"power-batch-{stem}"),
        ("method", "POST"),
        ("url", "/v1/chat/completions"),
        ("body", {
            "model": MODEL,
            "messages": [{"role": "user",
                          "content": POWER_PROMPT + "\n\n<datasheet>\n"
                          + text + "\n</datasheet>"}],
            "max_tokens": 6000,
            "temperature": 0,
        }),
    ])


def create_batch(key: str, reqs: list) -> dict:
    payload = OrderedDict([
        ("endpoint", "/v1/chat/completions"),
        ("model", MODEL),
        ("completion_window", "24h"),
        ("requests", reqs),
    ])
    req = urllib.request.Request(
        OR_BATCHES, data=json.dumps(payload).encode(),
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=300))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--queue", type=Path, required=True)
    ap.add_argument("--requests-dir", type=Path, required=True)
    ap.add_argument("--chunk", type=int, default=200)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    key = _key()
    args.requests_dir = args.requests_dir
    args.requests_dir.mkdir(parents=True, exist_ok=True)

    items = [json.loads(l) for l in args.queue.read_text().splitlines() if l.strip()]
    if args.limit:
        items = items[:args.limit]
    print(f"queue: {len(items)} items, chunk={args.chunk}")

    batch_ids = []
    submitted = 0
    for i in range(0, len(items), args.chunk):
        chunk_items = items[i:i + args.chunk]
        reqs = [r for r in (make_request(item) for item in chunk_items) if r]
        if not reqs:
            continue
        resp = create_batch(key, reqs)
        batch_ids.append(resp["id"])
        submitted += len(reqs)
        counts = resp.get("request_counts", {})
        print(f"  batch {resp['id']}: {len(reqs)} requests, status={resp['status']}, counts={counts}")

    ids_path = args.requests_dir / "batch-ids.json"
    ids_path.write_text(json.dumps(batch_ids, indent=2))
    print(f"submitted {submitted} requests across {len(batch_ids)} batches -> {ids_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
