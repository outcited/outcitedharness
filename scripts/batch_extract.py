#!/usr/bin/env python3
"""batch_extract.py — OpenRouter batch DSV1 extraction with page context.

The proven engine: 533 docs/h, $0.0026/doc, 79 claims/doc, 0 failures.
Chunks of 400 (the established pattern from vision_batch_submit.py).
"""
import argparse, hashlib, json, os, time, urllib.request
from pathlib import Path
import pymupdf

PROMPT = (
    'Extract ALL engineering specifications from the page context below. '
    'Return a JSON array; each element: '
    '{"field": "...", "value": ..., "unit": "...", '
    '"page_1based": N, "verbatim": "exact quote from page", '
    '"value_role": "typ|min|max|null"}. '
    'JSON only.'
)

def get_key():
    for line in open(Path(__file__).resolve().parents[1] / ".env"):
        if line.startswith("OPENROUTER_API_KEY="):
            return line.strip().split("=", 1)[1]
    raise SystemExit("missing OPENROUTER_API_KEY")

def build_request(doc, key_placeholder=None):
    src = doc["source_path"]
    short_id = hashlib.sha256(src.encode()).hexdigest()[:16]
    try:
        pdf = pymupdf.open(src)
    except Exception:
        return None, short_id, src
    scored = []
    for i in range(min(pdf.page_count, 60)):
        t = pdf[i].get_text() or ""
        score = sum(t.count(k) for k in (
            "Min", "Max", "Typ", "Unit", "VDD", "MHz", "kB",
            "mA", "GPIO", "Flash", "SRAM"))
        if t.count("....") > 5:
            score = 0
        scored.append((score, i))
    scored.sort(reverse=True)
    pages = [i for s, i in scored[:3] if s > 5]
    context = ""
    for pi in pages:
        text = pdf[pi].get_text() or ""
        context += "\n=== PAGE " + str(pi + 1) + " ===\n" + text[:6000] + "\n"
    pdf.close()
    if not context.strip():
        return None, short_id, src
    req = {
        "custom_id": short_id,
        "method": "POST",
        "url": "/v1/chat/completions",
        "body": {
            "model": "deepseek/deepseek-v4.1-flash:batch",
            "temperature": 0,
            "max_tokens": 32000,
            "reasoning": {"effort": "low"},
            "messages": [{"role": "user",
                         "content": PROMPT + "\n\n" + context[:45000]}]
        }
    }
    return req, short_id, src

def submit_chunk(chunk, key):
    body = json.dumps({
        "model": "deepseek/deepseek-v4.1-flash:batch",
        "endpoint": "/v1/chat/completions",
        "requests": chunk
    }).encode()
    req = urllib.request.Request("https://openrouter.ai/api/v1/batches",
        data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + key})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--queue", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--chunk", type=int, default=400)
    ap.add_argument("--skip", type=int, default=0,
                    help="skip first N docs (already done)")
    args = ap.parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    key = get_key()

    docs = json.loads(Path(args.queue).read_text()).get("documents", [])
    docs = docs[args.skip:]
    print(f"batch-extract: {len(docs)} docs, chunks of {args.chunk}", flush=True)

    # Build all requests (context extraction is local CPU)
    id_map = {}
    requests = []
    t0 = time.time()
    for n, d in enumerate(docs, 1):
        req, short_id, src = build_request(d)
        id_map[short_id] = src
        if req:
            requests.append(req)
        if n % 200 == 0:
            print(f"  built {n}/{len(docs)} ({time.time()-t0:.0f}s)", flush=True)

    print(f"requests built: {len(requests)} (skipped {len(docs)-len(requests)} empty)", flush=True)
    json.dump(id_map, open(out_dir / "id-map.json", "w"))

    # Submit in chunks
    batch_ids = []
    for i in range(0, len(requests), args.chunk):
        chunk = requests[i:i + args.chunk]
        try:
            batch = submit_chunk(chunk, key)
            bid = batch.get("id", "?")
            batch_ids.append(bid)
            n = batch.get("request_counts", {}).get("total", len(chunk))
            print(f"  chunk {i//args.chunk}: {bid} ({n} requests) — submitted", flush=True)
            json.dump(batch_ids, open(out_dir / "batch-ids.json", "w"))
        except Exception as e:
            detail = ""
            if hasattr(e, "read"):
                try:
                    detail = e.read().decode()[:200]
                except Exception:
                    pass
            print(f"  chunk {i//args.chunk} FAILED: {str(e)[:100]} {detail}", flush=True)
        time.sleep(2)  # proven gap between submissions

    total_cost = len(requests) * 0.0026
    print(f"\nsubmitted {len(batch_ids)} batches, {len(requests)} total requests", flush=True)
    print(f"estimated cost: ${total_cost:.2f}", flush=True)
    print(f"estimated time: {len(requests) / 533:.1f} hours", flush=True)
    print(f"batch IDs saved to: {out_dir / 'batch-ids.json'}", flush=True)

if __name__ == "__main__":
    main()
