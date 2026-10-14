#!/usr/bin/env python3
"""Submit the pin-table batch queue to OpenRouter DeepSeek batch (70% off).

Reads the prefiltered queue (pin-batch-queue.jsonl), builds chat requests over
substrate text (capped), and creates batches in chunks. Records every batch id
into batch-ids.json for the retriever. Refuses to exceed --max-requests.

Usage:
    python3 scripts/pin_batch_submit.py \
        --queue results/pin-tables-20261014/pin-batch-queue.jsonl \
        --requests-dir results/pin-tables-20261014/batches \
        [--chunk 200] [--max-requests 25000] [--limit N]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import OrderedDict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.extract_pin_tables_tp4 import PIN_PROMPT, PIPELINE_DB, MAX_CHARS  # noqa: E402

OR_BATCHES = "https://openrouter.ai/api/v1/batches"
MODEL = "deepseek/deepseek-v4.1-flash"

_tls = threading.local()


def _con():
    if getattr(_tls, "con", None) is None:
        _tls.con = sqlite3.connect(f"file:{PIPELINE_DB}?mode=ro", uri=True, timeout=60)
        _tls.con.row_factory = sqlite3.Row
    return _tls.con


def _key() -> str:
    import os
    env = os.environ.get("OPENROUTER_API_KEY")
    if env:
        return env
    for line in (ROOT / ".env").read_text().splitlines():
        if line.startswith("OPENROUTER_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("OPENROUTER_API_KEY missing")


def substrate(sha: str) -> str | None:
    row = _con().execute(
        "SELECT output FROM results WHERE corpus_key=? AND kind_hint='substrate'"
        " ORDER BY id DESC LIMIT 1", (f"pdf:{sha}",)).fetchone()
    if not row:
        return None
    try:
        rec = json.loads(row["output"])
    except Exception:
        return None
    return "\n".join(p.get("text") or "" for p in rec.get("pages") or [])


def make_request(item: dict) -> OrderedDict | None:
    sha = item["sha"]
    text = substrate(sha)
    if not text:
        return None
    return OrderedDict([
        ("custom_id", f"pin-batch-{sha}"),
        ("method", "POST"),
        ("url", "/v1/chat/completions"),
        ("body", {
            "model": MODEL,
            "messages": [{"role": "user",
                          "content": PIN_PROMPT + "\n\n<datasheet>\n"
                          + text[:MAX_CHARS] + "\n</datasheet>"}],
            "max_tokens": 3000,
            "reasoning": {"enabled": False},
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
    ap.add_argument("--max-requests", type=int, default=25000)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    args.requests_dir.mkdir(parents=True, exist_ok=True)
    key = _key()

    items = [json.loads(l) for l in args.queue.open() if l.strip()]
    if args.limit:
        items = items[: args.limit]
    if len(items) > args.max_requests:
        items = items[: args.max_requests]
    print(f"submitting {len(items)} requests in chunks of {args.chunk}", flush=True)

    ids_path = args.requests_dir / "batch-ids.json"
    ids = json.loads(ids_path.read_text()) if ids_path.exists() else {}

    done_custom = set()
    for stem, rec in ids.items():
        done_custom.update(rec.get("custom_ids", []))
    items = [i for i in items if f"pin-batch-{i['sha']}" not in done_custom]
    print(f"after resume-skip: {len(items)}", flush=True)

    n = 0
    for start in range(0, len(items), args.chunk):
        chunk_items = items[start:start + args.chunk]
        reqs = []
        for it in chunk_items:
            r = make_request(it)
            if r is not None:
                reqs.append(r)
        if not reqs:
            continue
        stem = f"chunk-{(start // args.chunk) + 1:04d}"
        try:
            b = create_batch(key, reqs)
        except urllib.error.HTTPError as e:
            print(f"{stem}: HTTP {e.code}: {e.read()[:200]}", flush=True)
            break
        ids[stem] = {"batch_id": b.get("id"), "status": b.get("status"),
                     "requests": len(reqs),
                     "custom_ids": [r["custom_id"] for r in reqs]}
        ids_path.write_text(json.dumps(ids, indent=1))
        n += len(reqs)
        print(f"{stem}: batch {b.get('id')} status={b.get('status')} "
              f"requests={len(reqs)} (total {n})", flush=True)
        time.sleep(2)
    print(f"submitted {n} requests across batches -> {ids_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())