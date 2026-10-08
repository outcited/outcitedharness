#!/usr/bin/env python3
"""Vision reading-strategy ablation on gold pages (all free compute).

Tests 6 strategies against the gold scorer, each on the same labeled pages:
  baseline_200dpi   — current pipeline (one call, 200 DPI, whole plot)
  highdpi_600       — same but 600 DPI render
  consensus_3x      — 3 independent reads at 200 DPI, point-level median
  decomposed        — axis/legend/series split into sub-calls, reassembled
  decomposed_600    — decomposed at 600 DPI
  ensemble_2model   — MiMo + DeepSeek on same image, point-level agreement

Every result goes through the same scorer (axis-label key match, series
count exact, point error vs gold). Winner becomes the production strategy.
"""

from __future__ import annotations

import argparse
import base64
import json
import statistics
import sys
import time
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harness.electronics.typical_curves import plot_regions_for_page


def render_at(page, bbox, dpi):
    clip = pymupdf.Rect(*bbox) & page.rect
    pm = page.get_pixmap(matrix=pymupdf.Matrix(dpi / 72.0, dpi / 72.0), clip=clip)
    p = Path(f"/tmp/ablation-{dpi}-{time.time_ns()}.png")
    pm.save(str(p))
    return p


def _call(client, image_path, prompt):
    result = client.extract(
        capability="typical_characteristics", image_path=image_path, prompt=prompt
    )
    return result["result"]


def _call_raw(base_url, model, key, image_path, prompt):
    import httpx
    payload = image_path.read_bytes()
    import hashlib
    body = {
        "model": model,
        "max_tokens": 16000,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {
                "url": "data:image/png;base64," + base64.b64encode(payload).decode()
            }},
        ]}],
    }
    if "deepseek" in base_url:
        body["thinking"] = {"type": "disabled"}
    else:
        body["reasoning_effort"] = "low"
    with httpx.Client(timeout=900) as c:
        r = c.post(f"{base_url}/chat/completions", json=body,
                    headers={"Authorization": f"Bearer {key}"})
    r.raise_for_status()
    text = r.json()["choices"][0]["message"]["content"]
    from harness.electronics.local_model import parse_json_response
    from harness.electronics.cloud_vision import _normalize_curve_points
    parsed = parse_json_response(text)
    if isinstance(parsed, dict):
        inner = parsed.get("properties")
        if isinstance(inner, dict) and ("axes" in inner or "series" in inner):
            parsed = inner
    _normalize_curve_points(parsed)
    return parsed


FULL_PROMPT = (
    "Digitize this datasheet plot. Return ONLY a JSON object with keys: "
    "title (string or null), axes (x and y each with label, unit, min, max), "
    "series (list of objects with name, condition, points as [{x,y}] objects). "
    "Points are digitized (x, y) in printed axis units at visually "
    "identifiable features only. Copy plot title, axis labels and units, "
    "and legend names verbatim from the image. Do not invent samples, do not "
    "extrapolate past the plotted range, do not read values from another plot. "
    "JSON only, no prose."
)

AXIS_PROMPT = (
    "Read ONLY the axis information from this plot image. Return a JSON "
    "object: {\"axes\": {\"x\": {\"label\": ..., \"unit\": ..., \"min\": ..., "
    "\"max\": ...}, \"y\": {\"label\": ..., \"unit\": ..., \"min\": ..., "
    "\"max\": ...}}, \"title\": ...}. Copy labels verbatim. JSON only."
)

LEGEND_PROMPT = (
    "Read ONLY the legend from this plot image. Return a JSON object: "
    "{\"series\": [{\"name\": ..., \"condition\": ...}]}. List every legend "
    "entry. Copy names verbatim. JSON only."
)

def series_prompt(name, axes_ctx):
    return (
        f"This plot has x-axis: {json.dumps(axes_ctx.get('x', {}))} and "
        f"y-axis: {json.dumps(axes_ctx.get('y', {}))}. "
        f"Digitize ONLY the series named \"{name}\" from this plot. "
        "Return a JSON object: {\"points\": [{\"x\": ..., \"y\": ...}]}. "
        "Points in printed axis units at identifiable features. JSON only."
    )


def consensus_median(reads):
    """Point-level median across N reads. Matches series by name."""
    if not reads:
        return {}
    base = reads[0]
    if len(reads) == 1:
        return base
    for si, series in enumerate(base.get("series", [])):
        all_pts = []
        for read in reads:
            try:
                match = read["series"][si]
                all_pts.append(match.get("points", []))
            except (IndexError, KeyError):
                all_pts.append([])
        if not all_pts or not all_pts[0]:
            continue
        medians = []
        n = min(len(p) for p in all_pts)
        for pi in range(n):
            xs = [p[pi]["x"] for p in all_pts if len(p) > pi]
            ys = [p[pi]["y"] for p in all_pts if len(p) > pi]
            medians.append({"x": statistics.median(xs), "y": statistics.median(ys)})
        series["points"] = medians
    return base


def decomposed_read(base_url, model, key, image_path):
    axes = _call_raw(base_url, model, key, image_path, AXIS_PROMPT)
    legend = _call_raw(base_url, model, key, image_path, LEGEND_PROMPT)
    combined = {
        "title": axes.get("title"),
        "axes": axes.get("axes", {}),
        "series": [],
    }
    for s in legend.get("series", []):
        pts = _call_raw(base_url, model, key, image_path,
                        series_prompt(s.get("name", ""), axes.get("axes", {})))
        combined["series"].append({
            "name": s.get("name"),
            "condition": s.get("condition"),
            "points": pts.get("points", []),
        })
    return combined


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold-dir", type=Path, default=ROOT / "tests/fixtures/gold/typical_curves")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--mimo-key-env", default="MIMO_API_KEY")
    ap.add_argument("--ds-key-env", default="DEEPSEEK_API_KEY")
    args = ap.parse_args()

    import os
    mimo_key = os.environ.get(args.mimo_key_env) or ""
    ds_key = os.environ.get(args.ds_key_env) or ""
    if not mimo_key:
        for line in (ROOT / ".env").read_text().splitlines():
            if line.startswith(f"{args.mimo_key_env}="):
                mimo_key = line.split("=", 1)[1].strip()
    if not ds_key:
        for line in (ROOT / ".env").read_text().splitlines():
            if line.startswith(f"{args.ds_key_env}="):
                ds_key = line.split("=", 1)[1].strip()

    MIMO = "https://token-plan-sgp.xiaomimimo.com/v1"
    DS = "https://api.deepseek.com/v1"

    sys.path.insert(0, str(ROOT / "scripts"))
    from score_typical_curves_gold import load_gold, _pair, _normalize_plot, score

    gold = load_gold(args.gold_dir)

    jobs = []
    for gf in sorted(args.gold_dir.glob("*.json")):
        if gf.name.startswith("_"):
            continue
        record = json.loads(gf.read_text())
        artifact = record.get("source_artifact", "")
        page_num = record.get("page_1based", 1) - 1
        pdf_path = None
        for candidate in [
            f"/Volumes/macbookM4-4TB/datasheet-corpus/mcu/ti.com/{artifact}",
        ]:
            if Path(candidate).exists():
                pdf_path = candidate
                break
        if not pdf_path:
            print(f"  WARN: could not find {artifact}")
            continue
        doc = pymupdf.open(pdf_path)
        if page_num >= doc.page_count:
            doc.close(); continue
        page = doc[page_num]
        regions = plot_regions_for_page(page)
        for fi, region in enumerate(regions):
            imgs = {}
            for dpi in (200, 600):
                clip = pymupdf.Rect(*region["bbox"]) & page.rect
                pm = page.get_pixmap(matrix=pymupdf.Matrix(dpi/72.0, dpi/72.0), clip=clip)
                img_path = Path(f"/tmp/ablation-gold-{artifact}-{page_num+1}-f{fi}-{dpi}.png")
                pm.save(str(img_path))
                imgs[dpi] = img_path
            jobs.append({
                "gold_file": gf.name,
                "images": imgs,
                "fig_index": fi,
                "source_artifact": artifact,
                "page_1based": record.get("page_1based"),
            })
        doc.close()  # safe: pixmaps already extracted

    print(f"gold pages: {len(gold)} | figure jobs: {len(jobs)}")
    strategies = [
        "baseline_200dpi", "highdpi_600", "consensus_3x",
        "decomposed_200", "decomposed_600", "ensemble_2model",
    ]
    all_scores = {}
    args.out.parent.mkdir(parents=True, exist_ok=True)

    for strategy in strategies:
        rows = []
        t_start = time.time()
        for job in jobs:
            imgs = job["images"]
            try:
                if strategy == "baseline_200dpi":
                    payload = _call_raw(MIMO, "mimo-v2.6-flash", mimo_key, imgs[200], FULL_PROMPT)
                elif strategy == "highdpi_600":
                    payload = _call_raw(MIMO, "mimo-v2.6-flash", mimo_key, imgs[600], FULL_PROMPT)
                elif strategy == "consensus_3x":
                    reads = [_call_raw(MIMO, "mimo-v2.6-flash", mimo_key, imgs[200], FULL_PROMPT) for _ in range(3)]
                    payload = consensus_median(reads)
                elif strategy == "decomposed_200":
                    payload = decomposed_read(MIMO, "mimo-v2.6-flash", mimo_key, imgs[200])
                elif strategy == "decomposed_600":
                    payload = decomposed_read(MIMO, "mimo-v2.6-flash", mimo_key, imgs[600])
                elif strategy == "ensemble_2model":
                    a = _call_raw(MIMO, "mimo-v2.6-flash", mimo_key, imgs[200], FULL_PROMPT)
                    b = _call_raw(DS, "deepseek-flash", ds_key, imgs[200], FULL_PROMPT)
                    payload = consensus_median([a, b])
            except Exception as e:
                print(f"  {strategy} {job['gold_file']} fig{job['fig_index']}: ERR {type(e).__name__} {str(e)[:80]}")
                continue
            rows.append({
                "payload": payload,
                "source_path": f"/gold/ti.com/{job['source_artifact']}",
                "page_1based": job["page_1based"],
                "figure_index": job["fig_index"],
            })
        elapsed = time.time() - t_start
        preds = []
        for r in rows:
            p = _normalize_plot(r["payload"])
            p["source_path"] = r["source_path"]
            p["page_1based"] = r["page_1based"]
            p["figure_index"] = r["figure_index"]
            preds.append(p)
        pairs = _pair(gold, preds)
        result = score(pairs)
        result["elapsed_s"] = round(elapsed, 1)
        result["calls_made"] = len(rows)
        all_scores[strategy] = result
        print(f"\n{strategy}: {json.dumps(result, indent=1)}")

    args.out.write_text(json.dumps(all_scores, indent=1))
    print(f"\n{'='*60}\nRESULTS SAVED: {args.out}")
    best = min(all_scores, key=lambda s: all_scores[s].get("point_error_pct_mean") or 999)
    print(f"BEST STRATEGY: {best} (point error {all_scores[best].get('point_error_pct_mean'):.2f}% mean)")


if __name__ == "__main__":
    main()
