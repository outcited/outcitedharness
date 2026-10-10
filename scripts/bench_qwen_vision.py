#!/usr/bin/env python3
"""Benchmark Qwen vision models on the typical-curves golden set.

Same harness, same gold, same scorer as the DeepSeek/MiMo DPI experiments
(results/learning-experiments-20261008). Champions to beat:
  DeepSeek @ 300 DPI: 0.72% mean point error
  MiMo Pro @ 600 DPI: 0.81%

Gold PDFs re-downloaded from ti.com (newer revision, plots verified on same
pages; ground-truth data values revision-stable). sha noted in results.
"""

from __future__ import annotations

import base64
import hashlib
import json
import sys
import time
from pathlib import Path

import httpx
import pymupdf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from harness.electronics.typical_curves import plot_regions_for_page
from harness.electronics.local_model import parse_json_response
from harness.electronics.cloud_vision import _normalize_curve_points
from score_typical_curves_gold import load_gold, _pair, _normalize_plot, score
from learning_experiments import FULL_PROMPT, render_at

QWEN_BASE = "https://token-plan.maas.qwencloudapi.com/compatible-mode/v1"
PDF_DIR = ROOT / "tests/fixtures/gold/typical_curves/pdfs"
GOLD_DIR = ROOT / "tests/fixtures/gold/typical_curves"
OUT = ROOT / "results/qwen-vision-bench-20261008/results.json"

EXTRA_BODY_CANDIDATES = [
    {"enable_thinking": False},
    {"reasoning_effort": "low"},
    None,
]


def call_qwen(model: str, key: str, image_path: Path, prompt: str):
    payload = base64.b64encode(image_path.read_bytes()).decode()
    last_err = None
    for extra in EXTRA_BODY_CANDIDATES:
        body = {
            "model": model,
            "max_tokens": 16000,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{payload}"}},
            ]}],
        }
        if extra:
            body.update(extra)
        try:
            with httpx.Client(timeout=900) as c:
                r = c.post(f"{QWEN_BASE}/chat/completions", json=body,
                           headers={"Authorization": f"Bearer {key}"})
            if r.status_code == 400:
                last_err = f"400: {r.text[:120]}"
                continue
            r.raise_for_status()
            text = r.json()["choices"][0]["message"]["content"]
            parsed = parse_json_response(text)
            if isinstance(parsed, dict):
                inner = parsed.get("properties")
                if isinstance(inner, dict) and ("axes" in inner or "series" in inner):
                    parsed = inner
            _normalize_curve_points(parsed)
            return parsed
        except httpx.HTTPStatusError as e:
            last_err = f"{e.response.status_code}: {e.response.text[:120]}"
            if e.response.status_code in (400,):
                continue
            raise
    raise RuntimeError(f"all body variants failed: {last_err}")


def build_jobs() -> list[dict]:
    gold = load_gold(GOLD_DIR)
    jobs = []
    for gf in sorted(GOLD_DIR.glob("*.json")):
        if gf.name.startswith("_"):
            continue
        record = json.loads(gf.read_text())
        pdf_path = PDF_DIR / record["source_artifact"]
        if not pdf_path.exists():
            print(f"MISSING PDF: {pdf_path}")
            continue
        doc = pymupdf.open(pdf_path)
        page_num = record.get("page_1based", 1) - 1
        if page_num >= doc.page_count:
            doc.close()
            continue
        page = doc[page_num]
        regions = plot_regions_for_page(page)
        for fi, region in enumerate(regions):
            images = {dpi: render_at(page, region["bbox"], dpi) for dpi in (150, 300, 600, 1200)}
            jobs.append({
                "gold_file": gf.name,
                "images": images,
                "fig_index": fi,
                "source_artifact": record["source_artifact"],
                "page_1based": record.get("page_1based"),
                "pdf_sha": hashlib.sha256(pdf_path.read_bytes()).hexdigest(),
            })
        doc.close()
    return gold, jobs


def run_model_dpi(model, key, jobs, dpi, gold):
    rows = []
    t0 = time.time()
    for job in jobs:
        try:
            payload = call_qwen(model, key, job["images"][dpi], FULL_PROMPT)
            rows.append({
                "payload": payload,
                "source_path": f"/gold/ti.com/{job['source_artifact']}",
                "page_1based": job["page_1based"],
                "figure_index": job["fig_index"],
            })
        except Exception as e:
            print(f"  {model}@{dpi} err on {job['gold_file']} fig{job['fig_index']}: {type(e).__name__} {str(e)[:80]}")
    preds = [{**_normalize_plot(r["payload"]), "source_path": r["source_path"],
              "page_1based": r["page_1based"], "figure_index": r["figure_index"]} for r in rows]
    result = score(_pair(gold, preds))
    result["elapsed_s"] = round(time.time() - t0, 1)
    result["model"] = model
    result["dpi"] = dpi
    return result


def main() -> int:
    key = json.loads((Path.home() / ".local/share/opencode/auth.json").read_text())["alibaba-token-plan"]["key"]
    gold, jobs = build_jobs()
    print(f"gold plots: {len(gold)} | figure jobs: {len(jobs)}")
    for j in jobs:
        print(f"  {j['gold_file']} fig{j['fig_index']} p{j['page_1based']} sha={j['pdf_sha'][:12]}")

    models = sys.argv[1:] or ["qwen3.8-max"]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    results = json.loads(OUT.read_text()) if OUT.exists() else {}

    for model in models:
        for dpi in (150, 300, 600, 1200):
            rk = f"{model}_dpi{dpi}"
            if rk in results:
                print(f"skip {rk} (done)")
                continue
            print(f"running {rk}...")
            r = run_model_dpi(model, key, jobs, dpi, gold)
            results[rk] = r
            OUT.write_text(json.dumps(results, indent=1))
            mean = r.get("point_error_pct_mean")
            print(f"  {rk}: plots={r.get('plots_scored')} mean={mean if mean is None else round(mean,3)}% "
                  f"max={r.get('point_error_pct_max')} pts={r.get('points_compared')} {r['elapsed_s']}s")

    print("\n=== RANKING (incl. previous champions) ===")
    board = {
        "deepseek-flash@300 (champion)": 0.72,
        "mimo-v2.6-pro@600": 0.81,
    }
    for k, v in results.items():
        if v.get("point_error_pct_mean") is not None:
            board[k] = v["point_error_pct_mean"]
    for k in sorted(board, key=board.get):
        print(f"  {k:40} {board[k]:>7.3f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
