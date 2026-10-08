#!/usr/bin/env python3
"""Batch catch-up: submit the corpus to OpenRouter's batch API at 50% cost.

Builds request JSONL from substrate text (front slice), submits in chunks,
polls, retrieves results for local verification. Local burn continues
independently; batch results get the same deterministic verifier.
"""

import json
import os
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, "/Users/samkim/Harnessv1")
sys.path.insert(0, "/Users/samkim/Harnessv1/scripts")

from burn_aisle import STUDENT_PROMPT, load_substrate

KEY = [l.split("=", 1)[1].strip() for l in open("/Users/samkim/Harnessv1/.env")
       if l.startswith("OPENROUTER_API_KEY")][0]
BASE = "https://openrouter.ai/api/v1"
MODEL = "deepseek/deepseek-v4.1-flash:batch"
BATCH_DIR = Path("/Volumes/M5_4TB/extract-results/cloud-batch")
CHUNK = 2500
MAX_CHARS = 28000


def api(path, data=None, method=None, is_json=True, timeout=120):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(data).encode() if data is not None else None,
        headers={"Authorization": f"Bearer {KEY}",
                 "Content-Type": "application/json"},
        method=method or ("POST" if data is not None else "GET"))
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        return json.loads(raw) if raw else {}


def build_requests(docs):
    out = []
    for stem in docs:
        sub = load_substrate(stem)
        if not sub:
            continue
        text = "\n".join(p.get("text", "") for p in sub.get("pages", []))[:MAX_CHARS]
        if len(text.strip()) < 300:
            continue
        out.append({
            "custom_id": stem,
            "method": "POST", "url": "/v1/chat/completions",
            "body": {"model": MODEL, "temperature": 0, "max_tokens": 16000,
                     "reasoning": {"effort": "low"},
                     "messages": [
                         {"role": "system", "content": STUDENT_PROMPT},
                         {"role": "user", "content":
                          f"Part: {sub.get('filename', stem).rsplit('.', 1)[0]}\n\n"
                          f"Datasheet pages:\n{text}"}]},
        })
    return out


def submit_chunk(requests, idx):
    jsonl = "\n".join(json.dumps(r) for r in requests)
    import io
    boundary = "----harnessbatch"
    body = (f"--{boundary}\r\n"
            f"Content-Disposition: form-data; name=\"file\"; filename=\"batch-{idx}.jsonl\"\r\n"
            f"Content-Type: application/jsonl\r\n\r\n{jsonl}\r\n--{boundary}--\r\n").encode()
    req = urllib.request.Request(
        BASE + "/files", data=body, method="POST",
        headers={"Authorization": f"Bearer {KEY}",
                 "Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=300) as r:
        file_id = json.loads(r.read())["id"]
    batch = api("/batch", {"file_id": file_id, "endpoint": "/v1/chat/completions"})
    return batch.get("id"), len(requests)


def main(limit=None):
    BATCH_DIR.mkdir(parents=True, exist_ok=True)
    aisles = {
        "power": "/Volumes/M5_4TB/exports/power-datasheet-pairs/*/pdf/*.pdf",
        "mcu": "/Volumes/macbookM4-4TB/datasheet-corpus/mcu/**/*.pdf",
        "vault": "/Volumes/M5_4TB/vault/landing/**/*.pdf",
    }
    done_stems = {p.stem for p in BATCH_DIR.glob("done/*.json")}
    pending = {p.stem for p in BATCH_DIR.glob("pending/*.json")}
    all_docs = []
    for pattern in aisles.values():
        all_docs += [p.stem for p in Path("/").glob(pattern.lstrip("/"))] if False else \
                   [str(p) for p in Path("/Volumes").glob(pattern.replace("/Volumes/", ""))]
    all_stems = [Path(p).stem for p in all_docs]
    todo = [s for s in all_stems if s not in done_stems and s not in pending]
    if limit:
        todo = todo[:limit]
    print(f"docs to batch: {len(todo)} (done={len(done_stems)} pending={len(pending)})", flush=True)
    reqs = build_requests(todo)
    print(f"buildable requests: {len(reqs)}", flush=True)
    (BATCH_DIR / "pending").mkdir(exist_ok=True)
    (BATCH_DIR / "done").mkdir(exist_ok=True)
    submitted = []
    for i in range(0, len(reqs), CHUNK):
        chunk = reqs[i:i + CHUNK]
        try:
            bid, n = submit_chunk(chunk, i // CHUNK)
            submitted.append(bid)
            (BATCH_DIR / "pending" / f"batch-{bid}.json").write_text(
                json.dumps({"batch_id": bid, "n": n, "submitted_at": time.time()}))
            print(f"chunk {i//CHUNK}: batch {bid} ({n} requests)", flush=True)
        except Exception as e:
            print(f"chunk {i//CHUNK} FAILED: {str(e)[:160]}", flush=True)
        time.sleep(3)
    (BATCH_DIR / "submitted.json").write_text(json.dumps(submitted, indent=2))
    print(f"submitted {len(submitted)} batches; poll with poll_batches()", flush=True)


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else None)
