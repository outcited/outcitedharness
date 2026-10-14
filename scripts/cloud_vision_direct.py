#!/usr/bin/env python3
"""Direct (synchronous) cloud vision runner for the L0 plot backlog.

The OpenRouter *batch* API rejects base64/data-URI images (only public http(s)
URLs are allowed), and every prior batch submission failed 100% for that
reason. This runner uses the working path instead: direct
`/chat/completions` with `reasoning: {enabled: false}` so DeepSeek v4.1 Flash
returns JSON content (not buried in reasoning).

Inputs: the renders + figure-meta.jsonl produced by `or_batch_vision.py prepare`
(one PNG per plot region, provenance per custom_id). Output rows have the same
shape as extract_typical_curves (value/quote/page/sha) plus the render sha.

Every answer is a model answer under the factory law: verified or held, never
trusted, always paired.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OR_URL = "https://openrouter.ai/api/v1/chat/completions"
TP4_URL = "http://100.116.221.82:8888/v1/chat/completions"
MODEL = "deepseek/deepseek-v4.1-flash"

PROMPT = (
    "You digitize a single printed plot region from an electronics datasheet. "
    "Return ONLY a JSON object, no prose, with keys: "
    "x_label (string, printed axis label), y_label (string), "
    "series (array of {name: string, points: [{x: number, y: number}]}), "
    "curve_type (string, e.g. efficiency_vs_load, transfer_vgs_id, bode, unknown). "
    "Preserve printed units verbatim in the label strings. "
    "If the region contains no plot, return {\"x_label\":\"\",\"y_label\":\"\",\"series\":[],\"curve_type\":\"none\"}. "
    "Do not infer values that are not printed."
)


def _key() -> str:
    env = os.environ.get("OPENROUTER_API_KEY")
    if env:
        return env
    for line in (ROOT / ".env").read_text().splitlines():
        if line.startswith("OPENROUTER_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("OPENROUTER_API_KEY missing")


_SHA_CACHE: dict[str, str | None] = {}


def _doc_sha16(path: str) -> str | None:
    """sha256(path)[:16], cached — one full read per DOCUMENT, not per region.

    The naive per-region hash re-read multi-MB PDFs over NFS for every one of
    tens of thousands of regions (the 800-in-6-hours stall of 2026-10-14)."""
    if path not in _SHA_CACHE:
        import hashlib
        try:
            _SHA_CACHE[path] = hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]
        except OSError:
            _SHA_CACHE[path] = None
    return _SHA_CACHE[path]


def _render_path(render_dir: Path, meta: dict) -> Path | None:
    """Map a figure-meta row to its render PNG filename.

    prepare names renders `{doc_sha16}-p{page}-f{fig}.png`; meta carries
    source_path + page + figure_index, so derive the sha16 from the file
    (cached per document)."""
    sha16 = _doc_sha16(meta["source_path"])
    if sha16 is None:
        return None
    cand = render_dir / f"{sha16}-p{meta['page_1based']}-f{meta['figure_index']}.png"
    return cand if cand.exists() else None


def _call(url: str, model: str, key: str | None, png: Path, max_tokens: int, timeout: int) -> dict:
    b64 = base64.b64encode(png.read_bytes()).decode()
    body = {
        "model": model,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": PROMPT},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
        ]}],
        "max_tokens": max_tokens,
        "reasoning": {"enabled": False},
    }
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
    try:
        d = json.load(urllib.request.urlopen(req, timeout=timeout))
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}", "body": e.read()[:200].decode(errors="replace")}
    except Exception as e:
        return {"error": type(e).__name__}
    m = d["choices"][0]["message"]
    return {"content": m.get("content") or "", "usage": d.get("usage") or {}}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--renders", type=Path, required=True)
    ap.add_argument("--meta", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--max-tokens", type=int, default=1200)
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--base-url", default=TP4_URL,
                    help="endpoint (default: local TP4); use OR_URL for OpenRouter")
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--provider", choices=["tp4", "openrouter"], default="tp4",
                    help="tp4 = local, no key; openrouter = cloud, needs OPENROUTER_API_KEY")
    args = ap.parse_args()

    url = TP4_URL if args.provider == "tp4" else OR_URL
    key = None if args.provider == "tp4" else _key()
    model = args.model if args.provider == "tp4" else MODEL
    metas = [json.loads(l) for l in args.meta.read_text().splitlines() if l.strip()]
    if args.limit:
        metas = metas[:args.limit]
    args.out.parent.mkdir(parents=True, exist_ok=True)

    done = set()
    if args.out.exists():
        for l in args.out.read_text().splitlines():
            try:
                done.add(json.loads(l)["custom_id"])
            except Exception:
                pass
    metas = [m for m in metas if m["custom_id"] not in done]
    print(f"provider={args.provider} url={url} queue: {len(metas)} regions (skipped {len(done)} done)", flush=True)

    def work(meta):
        png = _render_path(args.renders, meta)
        if png is None:
            return {**meta, "verdict": "held", "reason": "render_missing"}
        r = None
        for attempt in range(3):
            r = _call(url, model, key, png, args.max_tokens, args.timeout)
            if "error" not in r:
                break
            # transient TP4 500s (image-load race) — brief backoff then retry
            if r.get("error") == "HTTP 500" and attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            break
        if "error" in r:
            return {**meta, "verdict": "held", "reason": r["error"], "detail": r.get("body")}
        raw = r["content"].strip()
        try:
            payload = json.loads(raw)
            verdict = "ok"
        except Exception:
            payload, verdict = None, "held"
        return {**meta, "verdict": verdict, "model": model, "provider": args.provider,
                "payload": payload, "raw": raw[:2000], "usage": r["usage"]}

    t0 = time.time()
    n = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool, args.out.open("a") as fh:
        for row in pool.map(work, metas):
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            n += 1
            if n % 50 == 0:
                fh.flush()
                print(f"  {n}/{len(metas)} in {time.time()-t0:.0f}s", flush=True)
    print(f"done: {n} regions in {time.time()-t0:.0f}s -> {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())