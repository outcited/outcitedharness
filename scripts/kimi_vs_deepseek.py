#!/usr/bin/env python3
"""Kimi vs DeepSeek: same pin-table vision gold, same prompt, same scoring.

Renders the 144-page pin gold at 150 DPI (matching the original vision
baseline), extracts via the named endpoint, scores identically to
pin_baseline.py. Outputs side-by-side with the DeepSeek vision scoreboard.
"""

import base64
import io
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, "/Users/samkim/Harnessv1")
sys.path.insert(0, "/Users/samkim/Harnessv1/scripts")

from pin_baseline import build_sha_index
from vision_worker_v2 import render_regions, PROMPT

GOLD = Path("/Volumes/M5_4TB/exports/canon-gate/pin-gold-v1")
OUT = Path("/Volumes/M5_4TB/extract-results/kimi-vs-deepseek-v1.json")

ENDPOINT = os.environ.get("KIMI_ENDPOINT", "")
KEY = os.environ.get("KIMI_API_KEY", "")
MODEL = os.environ.get("KIMI_MODEL", "kimi-for-coding")


def kimi_extract(page_png_bytes, timeout=300):
    body = json.dumps({
        "model": MODEL, "temperature": 1, "max_completion_tokens": 80000,
        "prompt_cache_key": "pin-baseline-v1",
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": PROMPT},
            {"type": "image_url", "image_url": {
                "url": f"data:image/png;base64,{base64.b64encode(page_png_bytes).decode()}"}},
        ]}],
    }).encode()
    req = urllib.request.Request(ENDPOINT, data=body,
                                 headers={"Authorization": f"Bearer {KEY}",
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        msg = json.loads(r.read())["choices"][0]["message"]
        c = msg.get("content") or msg.get("reasoning_content") or ""
    c = re.sub(r"```(?:json)?", "", c).strip()
    m = re.search(r"\[.*\]", c, re.DOTALL)
    if not m:
        return []
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        cut = m.group(0).rfind("}")
        if cut > 0:
            try:
                return json.loads(m.group(0)[: cut + 1] + "]")
            except json.JSONDecodeError:
                return []
    return []


def main(limit=None):
    labels = [json.loads(l) for l in (GOLD / "holdout-pins40-vision-labels-v1.jsonl").open()]
    if limit:
        labels = labels[:limit]
    print("building sha index...", flush=True)
    sha_idx = build_sha_index()
    print(f"index: {len(sha_idx)}", flush=True)
    import concurrent.futures, threading
    from pin_baseline import score
    lock = threading.Lock()
    state = {"r_n": [0, 0], "p_n": [0, 0], "nm_n": [0, 0], "halluc": 0}
    results = [None] * len(labels)

    def run_one(i_label):
        i, label = i_label
        sub = sha_idx.get(label.get("document_sha256"))
        if not sub:
            results[i] = {"skip": True}
            return
        path = _pdf_path(label.get("document_sha256"))
        if not path:
            results[i] = {"skip": True}
            return
        try:
            doc = pymupdf.open(path)
            pn = min(label.get("page_1based", 1), len(doc))
            pix = doc[pn - 1].get_pixmap(dpi=150)
            png = pix.tobytes("png")
            doc.close()
            t0 = time.time()
            tables = kimi_extract(png)
            rows = []
            for t in tables:
                if isinstance(t, dict):
                    for r in t.get("rows", []):
                        if isinstance(r, list):
                            rows.append({"pin": r[0] if r else None,
                                         "name": r[1] if len(r) > 1 else None})
                        elif isinstance(r, dict) and r.get("cells"):
                            cells = r["cells"]
                            rows.append({"pin": list(cells.values())[0] if cells else None,
                                         "name": list(cells.values())[1] if len(cells) > 1 else None})
            s = score(label.get("rows", []), rows)
            label_has = bool(label.get("has_pin_table"))
            got_rows = bool(rows)
            with lock:
                if s["recall"] is not None:
                    state["r_n"][0] += s["matched"]
                    state["r_n"][1] += s["labeled"]
                state["p_n"][0] += s["matched"]
                state["p_n"][1] += s["matched"] + s["extra_rows"]
                if s["name_exact"]:
                    state["nm_n"][0] += round(s["name_exact"] * s["matched"])
                    state["nm_n"][1] += s["matched"]
                if got_rows and not label_has:
                    state["halluc"] += 1
            results[i] = {"i": i, "recall": s["recall"], "extra": s["extra_rows"],
                          "has_label": label_has, "got_rows": got_rows,
                          "secs": round(time.time() - t0, 1)}
            print(f"[{i}] r={s['recall']} extra={s['extra_rows']} "
                  f"{'HALLUC' if got_rows and not label_has else ''} {results[i]['secs']}s", flush=True)
        except Exception as e:
            results[i] = {"i": i, "error": str(e)[:80]}
            print(f"[{i}] ERROR {str(e)[:60]}", flush=True)

    with concurrent.futures.ThreadPoolExecutor(6) as ex:
        list(ex.map(run_one, enumerate(labels)))

    summary = {
        "model": MODEL,
        "docs": len([r for r in results if r and not r.get("skip")]),
        "recall": round(state["r_n"][0] / max(1, state["r_n"][1]), 4),
        "precision": round(state["p_n"][0] / max(1, state["p_n"][1]), 4),
        "name_exact": round(state["nm_n"][0] / max(1, state["nm_n"][1]), 4),
        "hallucinated_no_table_docs": state["halluc"],
        "vs_deepseek_vision": {"recall": 0.773, "precision": 0.68,
                               "name_exact": 0.60, "hallucinated": 37},
    }
    OUT.write_text(json.dumps({"summary": summary, "results": [r for r in results if r]}, indent=2))
    print(json.dumps(summary, indent=2))


def _pdf_path(sha):
    import sqlite3
    con = sqlite3.connect("/Volumes/M5_4TB/extract-results/pipeline.db")
    row = con.execute(
        "SELECT source_path FROM jobs j WHERE j.corpus_key = 'pdf:' || ? LIMIT 1",
        (Path(_fn_for_sha(sha)).stem if _fn_for_sha(sha) else "",)).fetchone()
    con.close()
    return row[0] if row else None


_FN_CACHE = {}


def _fn_for_sha(sha):
    if sha in _FN_CACHE:
        return _FN_CACHE[sha]
    import json as _j
    from pathlib import Path as _P
    for root in [_P("/Volumes/M5_4TB/extract-results/collected"),
                 _P("/Volumes/M5_4TB/extract-results/runs")]:
        if not root.exists():
            continue
        # cheap: use a persistent index file
        idx = _P("/tmp/sha2fn.json")
        if idx.exists():
            m = _j.loads(idx.read_text())
            _FN_CACHE.update(m)
            return m.get(sha)
    return None


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else None)
