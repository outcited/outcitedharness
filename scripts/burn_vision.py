#!/usr/bin/env python3
"""burn_vision.py — queue-runner for image-only/scanned docs (vision lane).

Words-first law: this runner receives ONLY docs the text lane already refused
(skip:vision_only_shape / image_only). Renders low-text pages, sends to the
MiMo volume-vision lane, validates deterministically, emits rows.

MODEL_SETTINGS.md law (measured 2026-10-06, MiMo cloud):
- max_tokens >= 16000 (hidden reasoning burns budget; empty content otherwise)
- reasoning_effort "low" (cannot disable thinking; minimal=400; thinking:false
  and chat_template_kwargs are WORSE here — do not copy the local recipe)
- NO JSON schema in the prompt (model echoes it); prose keys instead
- normalize point forms; unwrap defensive schema echoes

Verdict law: rows emit verdict="vision_row" — they are NOT fold-eligible
until gold/QA; served-store admission is a separate gate.
"""
import argparse, base64, json, os, sys, time, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, "/Users/samkim/Harnessv1")
ENDPOINT = "https://token-plan-sgp.xiaomimimo.com/v1/chat/completions"
MODEL = "mimo-v2.6-flash"
DPI = 200
MAX_PAGES = 3
TIMEOUT = 240

PROMPT = ("This is a scanned/page-image from an electronics document with no usable "
          "text layer. List what is printed as JSON array; each element: "
          "kind (title|table|note|spec), field (short name), value, unit, "
          "verbatim (exact printed text you can see), page_area (top|middle|bottom). "
          "Only include text you actually see. Return [] if unreadable. "
          "No explanations, JSON array only.")


def api_key():
    for line in open("/Users/samkim/Harnessv1/.env"):
        if line.startswith("MIMO_API_KEY="):
            return line.strip().split("=", 1)[1]
    raise SystemExit("MIMO_API_KEY missing")


def call_mimo(b64_png, key):
    body = json.dumps({
        "model": MODEL, "temperature": 0, "max_tokens": 16000,
        "reasoning_effort": "low",
        "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64_png}"}},
            {"type": "text", "text": PROMPT},
        ]}],
    }).encode()
    req = urllib.request.Request(ENDPOINT, data=body, headers={
        "Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        res = json.loads(r.read())
    content = res["choices"][0]["message"].get("content") or ""
    # defensive unwrap: schema echo / fences
    s = content.find("[")
    e = content.rfind("]")
    rows = []
    if s >= 0 and e > s:
        try:
            rows = json.loads(content[s:e + 1])
        except json.JSONDecodeError:
            rows = []
    return rows, round(time.time() - t0, 1), len(content)


def doc_pages(path):
    import pymupdf
    doc = pymupdf.open(path)
    # lowest-text pages first (vision lane only on flagged pages)
    scored = []
    for i in range(min(doc.page_count, 20)):
        t = doc[i].get_text() or ""
        scored.append((len(t.strip()), i))
    scored.sort()
    pages = [i for _, i in scored[:MAX_PAGES]]
    out = []
    for i in pages:
        pix = doc[i].get_pixmap(dpi=DPI)
        out.append((i + 1, base64.b64encode(pix.tobytes("png")).decode()))
    n = doc.page_count
    doc.close()
    return out, n


def one(doc, key):
    rows_out = []
    try:
        pages, page_count = doc_pages(doc["source_path"])
    except Exception as exc:
        return {"source_path": doc["source_path"], "status": f"error:{type(exc).__name__}", "rows": []}
    for page_1based, b64 in pages:
        for attempt in (1, 2):
            try:
                rows, lat, clen = call_mimo(b64, key)
                for r in rows if isinstance(rows, list) else []:
                    if not isinstance(r, dict):
                        continue
                    rows_out.append({
                        "page_1based": page_1based, "kind": r.get("kind"),
                        "field": r.get("field"), "value": r.get("value"),
                        "unit": r.get("unit"), "verbatim": r.get("verbatim"),
                        "page_area": r.get("page_area"),
                        "pdf_sha": doc.get("sha256"),
                        "lane": "vision_mimo", "model": MODEL,
                        "verdict": "vision_row",
                        "latency_s": lat, "content_len": clen,
                    })
                break
            except Exception:
                if attempt == 2:
                    rows_out.append({"page_1based": page_1based, "pdf_sha": doc.get("sha256"),
                                     "lane": "vision_mimo", "verdict": "error",
                                     "source_path": doc["source_path"]})
    return {"source_path": doc["source_path"], "sha256": doc.get("sha256"),
            "page_count": page_count, "status": "rows" if rows_out else "no_printed_values",
            "rows": rows_out}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--queue", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    key = api_key()
    q = json.loads(args.queue.read_text())
    docs = q.get("documents") or q
    if args.limit:
        docs = docs[: args.limit]
    frows = (args.out_dir / "vision.jsonl").open("a")
    fledger = (args.out_dir / "ledger.jsonl").open("a")
    done_shas = set()
    led = args.out_dir / "ledger.jsonl"
    if led.exists():
        for line in led.open():
            try:
                r = json.loads(line)
                if r.get("sha256") and r.get("status") not in ("error",):
                    done_shas.add(r["sha256"])
            except json.JSONDecodeError:
                pass
    docs = [d for d in docs if d.get("sha256") not in done_shas]
    print(f"vision queue: {len(docs)} docs, workers={args.workers}", flush=True)
    t0 = time.time()
    n_rows = n_docs = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(one, d, key): d for d in docs}
        for i, fut in enumerate(as_completed(futs), 1):
            res = fut.result()
            for r in res.pop("rows"):
                r["source_path"] = res["source_path"]
                frows.write(json.dumps(r, sort_keys=True) + "\n")
                n_rows += 1
            fledger.write(json.dumps({**res, "rows": len(res.get("rows", []))},
                                     sort_keys=True) + "\n")
            n_docs += 1
            if i % 10 == 0:
                print(f"{i}/{len(docs)} docs, {n_rows} rows, {time.time()-t0:.0f}s", flush=True)
    frows.close(); fledger.close()
    print(json.dumps({"docs": n_docs, "rows": n_rows,
                      "wall_s": round(time.time() - t0, 1)}, indent=1), flush=True)


if __name__ == "__main__":
    main()
