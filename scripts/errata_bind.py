#!/usr/bin/env python3
"""errata_bind.py — bind errata issues to parts/silicon/base-docs via TP4.

Turns verbatim errata rows into delta claims: issue -> {part_family,
silicon_rev, base_doc_hint}. Runs on the local DeepSeek V4.1 TP4 endpoint
(dgx2:8888) under the measured settings law (chat_template_kwargs
{"thinking": false}, temp 0) and the 2026-10-20 concurrency ruling (8).

Fail-closed: no answer -> status no_binding, never fabricated.
"""
import argparse, json, os, sys, time, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

EP = "http://192.168.4.45:8888/v1/chat/completions"
MODEL = "deepseek-v4.1-flash"
MAX_CHARS = 180_000
TIMEOUT = 300

PROMPT = ("You bind silicon errata documents to the parts they affect. From the "
          "errata text, list every distinct affected device/part family and silicon "
          "revision as a JSON array; each element an object with keys: "
          "part_family (part number or family, verbatim from text), "
          "silicon_rev (e.g. A0, B1, 'all', or null if unstated), "
          "base_doc_hint (title/part-number of the datasheet or manual this errata "
          "amends, null if unstated). Only what is printed. [] if nothing clear. "
          "JSON array only, no explanations.")


def call(text):
    body = json.dumps({
        "model": MODEL, "temperature": 0, "max_tokens": 6000,
        "chat_template_kwargs": {"thinking": False},
        "messages": [{"role": "user", "content": PROMPT + "\n\nErrata text:\n" + text[:MAX_CHARS]}],
    }).encode()
    req = urllib.request.Request(EP, data=body, headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        res = json.loads(r.read())
    content = res["choices"][0]["message"].get("content") or ""
    s, e = content.find("["), content.rfind("]")
    rows = []
    if s >= 0 and e > s:
        try:
            rows = json.loads(content[s:e + 1])
        except json.JSONDecodeError:
            rows = []
    return rows if isinstance(rows, list) else [], round(time.time() - t0, 1)


def one(doc):
    import pymupdf
    try:
        d = pymupdf.open(doc["source_path"])
        text = " ".join((p.get_text() or "") for p in d)
        d.close()
    except Exception as exc:
        return {"sha256": doc.get("sha256"), "source_path": doc["source_path"],
                "status": f"error:{type(exc).__name__}", "bindings": []}
    if not text.strip():
        return {"sha256": doc.get("sha256"), "source_path": doc["source_path"],
                "status": "no_text", "bindings": []}
    for attempt in (1, 2):
        try:
            rows, lat = call(text)
            out = []
            for r in rows:
                if isinstance(r, dict) and r.get("part_family"):
                    out.append({"part_family": str(r.get("part_family"))[:96],
                                "silicon_rev": r.get("silicon_rev"),
                                "base_doc_hint": (str(r["base_doc_hint"])[:160]
                                                   if r.get("base_doc_hint") else None)})
            return {"sha256": doc.get("sha256"), "source_path": doc["source_path"],
                    "status": "bound" if out else "no_binding",
                    "bindings": out, "latency_s": lat}
        except Exception:
            if attempt == 2:
                return {"sha256": doc.get("sha256"), "source_path": doc["source_path"],
                        "status": "error:call", "bindings": []}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--queue", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--workers", type=int, default=8)  # 2026-10-20 ruling
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    q = json.loads(args.queue.read_text())
    docs = q.get("documents") or q
    if args.limit:
        docs = docs[: args.limit]
    led = args.out_dir / "ledger.jsonl"
    done = set()
    if led.exists():
        for line in led.open():
            try:
                r = json.loads(line)
                if r.get("sha256") and r.get("status") != "error:call":
                    done.add(r["sha256"])
            except json.JSONDecodeError:
                pass
    docs = [d for d in docs if d.get("sha256") not in done]
    print(f"bind queue: {len(docs)} docs, workers={args.workers}", flush=True)
    fb = (args.out_dir / "errata-bound.jsonl").open("a")
    fl = led.open("a")
    t0 = time.time(); n = n_b = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(one, d) for d in docs]
        for i, fut in enumerate(as_completed(futs), 1):
            r = fut.result()
            fl.write(json.dumps(r, sort_keys=True) + "\n")
            for b in r["bindings"]:
                fb.write(json.dumps({"doc_sha256": r["sha256"],
                                     "source_path": r["source_path"], **b},
                                    sort_keys=True) + "\n")
                n_b += 1
            n += 1
            if i % 25 == 0:
                print(f"{i}/{len(docs)} docs, {n_b} bindings, {time.time()-t0:.0f}s", flush=True)
    fb.close(); fl.close()
    print(json.dumps({"docs": n, "bindings": n_b, "wall_s": round(time.time() - t0, 1)}), flush=True)


if __name__ == "__main__":
    main()
