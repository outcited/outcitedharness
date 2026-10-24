#!/usr/bin/env python3
"""PRD-CONTEXT-FIRST-EXTRACTION — A/B vision test.

Does the free context envelope improve vision digitization? Ground truth
is objective: the vector-extracted curve fixtures (deterministic geometry,
human-reviewable). Both arms see the SAME model, SAME crop, SAME base
prompt — arm B additionally receives the envelope (caption, plot type,
axis labels/units/ticks, legend names, printed conditions, identity).

Scoring: greedy nearest-curve assignment (name-independent digitization
quality) + name-match rate + out-of-range rate + y-span-normalized RMSE.
"""

from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import re
import time
import urllib.request
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "ve", str(ROOT / "scripts/vision_envelope.py"))
ve = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ve)

TP4 = "http://100.116.221.82:8888/v1/chat/completions"
MODEL = "deepseek-v4.1-flash"

BASE_PROMPT = (
    "You digitize a single printed plot region from an electronics datasheet. "
    "Return ONLY a JSON object, no prose, with keys: "
    "x_label (string, printed axis label), y_label (string), "
    "series (array of {name: string, points: [{x: number, y: number}]}), "
    "curve_type (string, e.g. efficiency_vs_load, transfer_vgs_id, bode, unknown). "
    "Preserve printed units verbatim in the label strings. "
    "If the region contains no plot, return {\"x_label\":\"\",\"y_label\":\"\",\"series\":[],\"curve_type\":\"none\"}. "
    "Do not infer values that are not printed."
)


def render_crop(pdf: Path, page_1based: int, bbox, pad_bottom=48.0, pad=22.0):
    doc = pymupdf.open(pdf)
    try:
        page = doc[page_1based - 1]
        clip = pymupdf.Rect(
            max(0, bbox[0] - pad), max(0, bbox[1] - pad),
            min(page.rect.width, bbox[2] + pad),
            min(page.rect.height, bbox[3] + pad_bottom))
        pix = page.get_pixmap(matrix=pymupdf.Matrix(2.5, 2.5), clip=clip)
        return pix.tobytes("png")
    finally:
        doc.close()


def call_tp4_vision(png: bytes, prompt: str) -> tuple[dict | None, float]:
    b64 = base64.b64encode(png).decode()
    body = {
        "model": MODEL, "temperature": 0, "max_tokens": 6000,
        "chat_template_kwargs": {"enable_thinking": False},
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image_url",
                 "image_url": {"url": f"data:image/png;base64,{b64}"}},
                {"type": "text", "text": prompt},
            ],
        }],
    }
    req = urllib.request.Request(
        TP4, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            d = json.loads(r.read())
    except Exception as exc:
        return {"error": str(exc)[:200]}, time.time() - t0
    dt = time.time() - t0
    c = d["choices"][0]["message"].get("content") or ""
    m = re.search(r"\{.*\}", c, re.DOTALL)
    if not m:
        return {"error": "no_json", "raw": c[:200]}, dt
    try:
        return json.loads(m.group(0)), dt
    except json.JSONDecodeError:
        return {"error": "bad_json", "raw": c[:200]}, dt


def _xy(p):
    if isinstance(p, dict):
        return float(p["x"]), float(p["y"])
    return float(p[0]), float(p[1])


def score(vision: dict, gt_plots: list[dict]) -> dict:
    vs = vision.get("series") or []
    if not gt_plots:
        return {"note": "no gt"}
    gt = gt_plots[0]["series"]
    if not vs:
        return {"rmse_norm": None, "series_matched": 0,
                "points": 0, "oor": 0, "name_hits": 0,
                "note": "no series returned"}
    # normalize names
    def canon(s):
        return re.sub(r"[^a-z0-9]", "", (s or "").lower())
    used = set()
    assignments = []
    for v in vs:
        best, best_rmse = None, None
        for gi, g in enumerate(gt):
            if gi in used:
                continue
            xs = [_xy(p)[0] for p in g["points"]]
            ys = [_xy(p)[1] for p in g["points"]]
            errs, oor = [], 0
            for p in v.get("points") or []:
                x, y = _xy(p)
                if x < min(xs) or x > max(xs):
                    oor += 1
                    continue
                # interp GT at x
                gy = ys[-1]
                for (x0, y0), (x1, y1) in zip(list(zip(xs, ys)), list(zip(xs, ys))[1:]):
                    if x0 <= x <= x1 and x1 != x0:
                        gy = y0 + (y1 - y0) * (x - x0) / (x1 - x0)
                        break
                errs.append(abs(gy - y))
            if errs:
                rmse = (sum(e * e for e in errs) / len(errs)) ** 0.5
                if best_rmse is None or rmse < best_rmse:
                    best_rmse, best = rmse, gi
        if best is not None:
            used.add(best)
            g = gt[best]
            ys = [_xy(p)[1] for p in g["points"]]
            span = (max(ys) - min(ys)) or 1.0
            xs = [_xy(p)[0] for p in g["points"]]
            errs, oor = [], 0
            for p in v.get("points") or []:
                x, y = _xy(p)
                if x < min(xs) or x > max(xs):
                    oor += 1
                    continue
                gy = ys[-1]
                for (x0, y0), (x1, y1) in zip(list(zip(xs, ys)), list(zip(xs, ys))[1:]):
                    if x0 <= x <= x1 and x1 != x0:
                        gy = y0 + (y1 - y0) * (x - x0) / (x1 - x0)
                        break
                errs.append(abs(gy - y) / span)
            assignments.append({
                "gt_series": g.get("series_name"),
                "vision_name": v.get("name"),
                "name_match": canon(v.get("name")) in canon(g.get("series_name"))
                or canon(g.get("series_name")) in canon(v.get("name")),
                "pts": len(errs), "oor": oor,
                "rmse_norm": (sum(e * e for e in errs) / len(errs)) ** 0.5 if errs else None,
            })
    pts = sum(a["pts"] for a in assignments)
    w = sum((a["rmse_norm"] or 0) * a["pts"] for a in assignments)
    return {
        "series_matched": len(assignments),
        "series_gt": len(gt),
        "points": pts,
        "oor": sum(a["oor"] for a in assignments),
        "name_hits": sum(1 for a in assignments if a["name_match"]),
        "rmse_norm": round(w / pts, 4) if pts else None,
        "assignments": assignments,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cases", type=Path, required=True,
                    help="JSON list of {pdf,page,figure_index,part,gt}")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    cases = json.loads(args.cases.read_text())
    results = []
    for case in cases:
        pdf = Path(case["pdf"])
        env = ve.build_envelope(pdf, case["page"], case["part"])
        fig_env = next((f for f in env["figures"]
                        if f["figure_index"] == case["figure_index"]), None)
        gt_rec = json.loads(Path(case["gt"]).read_text())
        gt_plots = [p for p in gt_rec.get("plots", [])
                    if p.get("_figure_index") == case["figure_index"]]
        png = render_crop(pdf, case["page"], tuple(fig_env["bbox"])
                          if fig_env else (60, 60, 550, 400))
        arm_a_prompt = BASE_PROMPT
        envelope_block = json.dumps(fig_env, ensure_ascii=False, indent=1) \
            if fig_env else "{}"
        arm_b_prompt = (BASE_PROMPT
                        + "\n\nCONTEXT ENVELOPE from the deterministic "
                          "text/geometry layer (trust it; use it to locate "
                          "and name everything):\n"
                        + envelope_block
                        + f"\nDevice identity: {case['part']} (page "
                          f"{case['page']} of its datasheet). "
                          "Digitize with this context.")
        for arm, prompt in (("A_blind", arm_a_prompt), ("B_envelope", arm_b_prompt)):
            vision, dt = call_tp4_vision(png, prompt)
            if vision and "error" not in vision:
                s = score(vision, gt_plots)
            else:
                s = {"error": (vision or {}).get("error", "no response")}
            rec = {"case": Path(case["gt"]).stem, "figure_index": case["figure_index"],
                   "arm": arm, "latency_s": round(dt, 1), "score": s,
                   "vision_labels": {
                       "x": (vision or {}).get("x_label"),
                       "y": (vision or {}).get("y_label")}}
            results.append(rec)
            print(json.dumps(rec, ensure_ascii=False)[:400], flush=True)
    args.out.write_text(json.dumps(results, indent=1, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
