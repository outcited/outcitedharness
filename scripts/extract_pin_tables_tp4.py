#!/usr/bin/env python3
"""Pin-table extraction lane over substrate text (text tier, TP4 local).

The proven pin_baseline experiment shape at corpus scale: send the document's
substrate text to the extraction model with the verbatim-quote pin prompt,
keep only rows whose quote actually appears in the substrate (deterministic
verifier — a model answer is never trusted, always verified), and stream
per-part rows for staging.

Cheap local prefilter first: only documents whose text carries pin-table
signals go to the model at all.

Usage:
    python3 scripts/extract_pin_tables_tp4.py \
        --queue /tmp/full-corpus-queue-pn.json \
        --out results/pin-tables-20261014/pin-rows.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PIPELINE_DB = os.environ.get(
    "PIPELINE_DB", str(ROOT / "results/pipeline-live.db"))
TP4_URL = "http://100.116.221.82:8888/v1/chat/completions"
MODEL = "deepseek-v4.1-flash"

PIN_PROMPT = """You are a datasheet pin-table extraction engine. Given datasheet
text, extract the pin/pad definition table rows for the named package. Every row
value MUST be quoted character-for-character from the text. If the text contains
no pin definition table for this package, return an empty array — never invent.
Output ONLY JSON: {"has_pin_table": true|false, "package": "<as printed>",
"rows": [{"pin": "<designator>", "name": "<pin name>", "type": "<type or null>",
"quote": "<the verbatim table row line>"}]}"""

MAX_CHARS = 40000  # pin tables live early; cap the prompt (TP4 latency scales with it)

PIN_SIGNAL = re.compile(
    r"pin\s+(?:description|function|assignment|configuration|definition)"
    r"|pinout|pin\s+out|ball\s*(?:out|map|assignment)"
    r"|pad\s+(?:description|assignment)",
    re.IGNORECASE)


_tls = threading.local()


def _local_con():
    if getattr(_tls, "con", None) is None:
        _tls.con = sqlite3.connect(f"file:{PIPELINE_DB}?mode=ro", uri=True, timeout=60)
        _tls.con.row_factory = sqlite3.Row
    return _tls.con


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip().lower()


def load_substrate_text(sha: str) -> str | None:
    # sqlite connections are not shareable across threads — one per worker
    con = _local_con()
    row = con.execute(
        "SELECT output FROM results WHERE corpus_key=? AND kind_hint='substrate'"
        " ORDER BY id DESC LIMIT 1", (f"pdf:{sha}",)).fetchone()
    if not row:
        return None
    try:
        rec = json.loads(row["output"])
    except Exception:
        return None
    pages = rec.get("pages") or []
    return "\n".join(p.get("text") or "" for p in pages)


def call_tp4(text: str, timeout: int = 120) -> dict:
    body = {"model": MODEL,
            "messages": [{"role": "user",
                          "content": PIN_PROMPT + "\n\n<datasheet>\n" + text[:MAX_CHARS] + "\n</datasheet>"}],
            "max_tokens": 3000,
            "reasoning": {"enabled": False}}
    req = urllib.request.Request(
        TP4_URL, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    try:
        d = json.load(urllib.request.urlopen(req, timeout=timeout))
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}", "body": e.read()[:200].decode(errors="replace")}
    except Exception as e:
        return {"error": type(e).__name__}
    m = d["choices"][0]["message"]
    return {"content": m.get("content") or "", "usage": d.get("usage") or {}}


def verify_rows(payload: dict, doc_norm: str) -> list[dict]:
    kept, dropped = [], 0
    for r in payload.get("rows") or []:
        q = _norm(r.get("quote"))
        if q and q in doc_norm:
            kept.append(r)
        else:
            dropped += 1
    return kept, dropped


def work(item):
    sha = Path(item["source_path"]).stem
    try:
        text = load_substrate_text(sha)
    except Exception:
        text = None
    if not text:
        return {"part_number": item["part_number"], "sha": sha,
                "verdict": "no_substrate"}
    if not PIN_SIGNAL.search(text):
        return {"part_number": item["part_number"], "sha": sha,
                "verdict": "no_pin_signal"}
    r = call_tp4(text)
    if "error" in r:
        return {"part_number": item["part_number"], "sha": sha,
                "verdict": "held", "reason": r["error"]}
    raw = (r["content"] or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`").lstrip("json").strip()
    try:
        payload = json.loads(raw)
    except Exception:
        return {"part_number": item["part_number"], "sha": sha,
                "verdict": "held", "reason": "unparseable"}
    if not payload.get("has_pin_table"):
        return {"part_number": item["part_number"], "sha": sha,
                "verdict": "no_pin_table"}
    kept, dropped = verify_rows(payload, _norm(text))
    if not kept:
        return {"part_number": item["part_number"], "sha": sha,
                "verdict": "unverified", "dropped": dropped}
    return {"part_number": item["part_number"], "sha": sha, "verdict": "ok",
            "package": payload.get("package"), "rows": kept,
            "dropped_unverified": dropped,
            "usage": r.get("usage")}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--queue", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    docs = json.loads(args.queue.read_text())
    docs = docs.get("documents") or docs.get("parts") or docs
    if args.limit:
        docs = docs[: args.limit]
    args.out.parent.mkdir(parents=True, exist_ok=True)

    done = set()
    if args.out.exists():
        for l in args.out.read_text().splitlines():
            try:
                done.add(json.loads(l)["sha"])
            except Exception:
                pass
    docs = [d for d in docs if Path(d["source_path"]).stem not in done]
    print(f"pin lane queue: {len(docs)} docs (skipped {len(done)} done)", flush=True)

    t0 = time.time()
    n = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool, args.out.open("a") as fh:
        futs = {pool.submit(work, d): d for d in docs}
        for fut in as_completed(futs):
            row = fut.result()
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            n += 1
            if n % 100 == 0:
                fh.flush()
                print(f"  {n}/{len(docs)} in {time.time()-t0:.0f}s", flush=True)
    print(f"done: {n} docs in {time.time()-t0:.0f}s -> {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())