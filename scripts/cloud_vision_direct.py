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


_MAX_IMG_PX = 1600


def _shrink_b64(b64: str) -> str:
    """Downscale oversized images before upload.

    Fullpage 600-DPI renders reach 4+ MB; the vision endpoint stalls for
    minutes on them (internally the model retokenizes to ~1k tokens anyway).
    Capping the long side at ~1600 px keeps every readable tick/label while
    making each request small, fast and reliable."""
    try:
        import io
        from PIL import Image
        img = Image.open(io.BytesIO(base64.b64decode(b64)))
        w, h = img.size
        if max(w, h) <= _MAX_IMG_PX:
            return b64
        scale = _MAX_IMG_PX / max(w, h)
        img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))))
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        return base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return b64


def _call_msgs(url: str, model: str, key: str | None, messages, max_tokens: int, timeout: int) -> dict:
    """Send pre-built messages (from a prepared request body) to the endpoint."""
    msgs = messages
    try:
        shrunk = []
        for m in messages:
            content = m.get("content")
            if isinstance(content, list):
                parts = []
                for p in content:
                    if isinstance(p, dict) and p.get("type") == "image_url":
                        iu = dict(p)
                        iu["image_url"] = {
                            "url": _shrink_b64((p.get("image_url") or {}).get("url", ""))
                        }
                        parts.append(iu)
                    else:
                        parts.append(p)
                m = {**m, "content": parts}
            shrunk.append(m)
        msgs = shrunk
    except Exception:
        msgs = messages
    body = {"model": model, "messages": msgs, "max_tokens": max_tokens,
            "reasoning": {"enabled": False}}
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


def _call(url: str, model: str, key: str | None, png: Path, max_tokens: int, timeout: int) -> dict:
    b64 = _shrink_b64(base64.b64encode(png.read_bytes()).decode())
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
    ap.add_argument("--renders", type=Path, required=False)
    ap.add_argument("--meta", type=Path, required=False)
    ap.add_argument("--requests-dir", type=Path, required=False,
                    help="read work from requests-*.jsonl (images embedded) — "
                         "no PDF reads, no render lookups; preferred for volume")
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

    if args.requests_dir:
        # Work units straight from the prepared request files: each row carries
        # custom_id + body with the embedded image. No PDF hashing, no render
        # filename lookup — immune to the NFS head-of-line stall.
        items = []
        for rf in sorted(args.requests_dir.glob("requests-*.jsonl")):
            for line in rf.read_text().splitlines():
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                items.append(row)
    else:
        if not (args.renders and args.meta):
            raise SystemExit("need --requests-dir OR (--renders AND --meta)")
        metas = [json.loads(l) for l in args.meta.read_text().splitlines() if l.strip()]
        items = [{"custom_id": m["custom_id"], "_meta": m} for m in metas]
    if args.limit:
        items = items[: args.limit]
    args.out.parent.mkdir(parents=True, exist_ok=True)

    done = set()
    if args.out.exists():
        for l in args.out.read_text().splitlines():
            try:
                done.add(json.loads(l)["custom_id"])
            except Exception:
                pass
    items = [it for it in items if it["custom_id"] not in done]
    print(f"provider={args.provider} url={url} queue: {len(items)} regions (skipped {len(done)} done)", flush=True)

    def work(it):
        if args.requests_dir:
            body = it.get("body") or {}
            msgs = body.get("messages") or []
            r = _call_msgs(url, model, key, msgs, args.max_tokens, args.timeout)
            base = {"custom_id": it["custom_id"]}
        else:
            png = _render_path(args.renders, it["_meta"])
            if png is None:
                return {**it["_meta"], "verdict": "held", "reason": "render_missing"}
            r = _call(url, model, key, png, args.max_tokens, args.timeout)
            base = it["_meta"]
        if "error" in r:
            return {**base, "verdict": "held", "reason": r["error"], "detail": r.get("body")}
        raw = (r["content"] or "").strip()
        # models often wrap JSON in markdown fences — strip to the outer object
        if raw.startswith("```"):
            raw = raw.strip("`").lstrip("json").strip()
        try:
            payload = json.loads(raw)
            verdict = "ok"
        except Exception:
            payload, verdict = None, "held"
        return {**base, "verdict": verdict, "model": model, "provider": args.provider,
                "payload": payload, "raw": raw[:2000], "usage": r["usage"]}

    t0 = time.time()
    n = 0
    from concurrent.futures import as_completed
    with ThreadPoolExecutor(max_workers=args.workers) as pool, args.out.open("a") as fh:
        futures = [pool.submit(work, it) for it in items]
        for fut in as_completed(futures):
            row = fut.result()
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            n += 1
            if n % 50 == 0:
                fh.flush()
                print(f"  {n}/{len(items)} in {time.time()-t0:.0f}s", flush=True)
    print(f"done: {n} regions in {time.time()-t0:.0f}s -> {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())