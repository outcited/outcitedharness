#!/usr/bin/env python3
"""CONTEXT-FIRST controlled A/B (staged, local-TP4-only, no paid calls).

Population: clean vector, tight crop, full-page multi-figure, raster,
partial-text, difficult table, repeated/misleading labels, empty controls.
Same docs/images/model/decoding across arms; 3 interleaved repeats per
cell to separate real change from timing variance.

Metrics: figure selection, series/table recovery, numerical accuracy,
condition/identity binding, false SUPPORTED, REVIEW/missing, verified
usable output per doc, inference time; envelope build overhead reported
separately. Scoring is source-backed: table strings must exist in the
page text; plot points scored against vector ground truth.
"""

from __future__ import annotations

import importlib.util
import json
import re
import time
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[1]


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, str(ROOT / rel))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ab = _load("ab", "scripts/vision_ab_test.py")
router = _load("router", "scripts/vision_router.py")

TABLE_PROMPT = (
    "You read one printed table region from an electronics datasheet. "
    "Return ONLY a JSON object: {\"columns\": [strings], \"rows\": [{\"label\": string, "
    "\"cells\": [strings]}], \"identity\": {\"part_hint\": string or null}, "
    "\"figure_number_digitized\": string or null, \"conflicts\": [strings]}. "
    "Preserve printed values verbatim. If there is no table, return empty arrays."
)
PLOT_PROMPT = ab.BASE_PROMPT[:-1] + (
    " Also return figure_number_digitized (the printed figure number you are "
    "reading, or null) and conflicts (array: any place your reading disagrees "
    "with printed text you can see). }"
)
TRUST_LINE = (
    "\n\nCONTEXT ENVELOPE (source-derived evidence — use to locate and name; "
    "NOT unquestionable truth. Where it conflicts with the image or printed "
    "text, HOLD: return it in conflicts and prefer the image.):\n"
)

CASES = [
    # class, task, pdf, page, figure_index, crop, gt, expected_empty, force_vision
    ("clean_vector", "plot", "TPS54360.pdf", 35, 4, "tight", "curve_evidence_power_data_01/tps54360_p35.json", False, True),
    ("clean_vector", "plot", "LM5005.pdf", 25, 0, "tight", "curve_evidence_power_data_01/lm5005_p25.json", False, True),
    ("tight_crop_regression", "plot", "TPS54360.pdf", 35, 4, "tight", "curve_evidence_power_data_01/tps54360_p35.json", False, True),
    ("tight_crop_regression", "plot", "SiC46x.pdf", 16, 5, "tight", "curve_evidence_pilot/vishay_sic46x_p16.json", False, True),
    ("fullpage_multi_figure", "plot", "TPS54360.pdf", 35, 4, "full", "curve_evidence_power_data_01/tps54360_p35.json", False, True),
    ("fullpage_multi_figure", "plot", "SiC46x.pdf", 16, 5, "full", "curve_evidence_pilot/vishay_sic46x_p16.json", False, True),
    ("raster", "plot", "TPS54360.pdf", 44, None, "full", None, True, True),
    ("partial_text", "plot", "LM5005.pdf", 42, None, "full", None, True, True),
    ("difficult_table", "table", "SiC46x.pdf", 5, None, "full", None, False, True),
    ("misleading_repeated_labels", "plot", "TPS54360.pdf", 35, 5, "full", "curve_evidence_power_data_01/tps54360_p35.json", False, True),
    ("empty_control", "plot", "TPS54360.pdf", 12, None, "full", None, True, True),
]

# text-layer ground truth for the table cell (SiC46x p5 fault protections)
TABLE_EXPECTED = [
    "Valley current limit", "SiC461 (10 A)", "SiC462 (6 A)", "SiC463 (4 A)",
    "RILIM = 60 k", "10.4", "13", "15.6", "6.4", "8", "9.6", "IOCP",
    "Output OVP threshold", "VOVP",
]


def norm(s):
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


def render(pdf, page_1based, crop_kind, fig_env):
    doc = pymupdf.open(pdf)
    try:
        page = doc[page_1based - 1]
        if crop_kind == "tight" and fig_env:
            b = fig_env["bbox_pdf_points"]
            clip = pymupdf.Rect(max(0, b[0] - 22), max(0, b[1] - 22),
                                min(page.rect.width, b[2] + 22),
                                min(page.rect.height, b[3] + 48))
            pix = page.get_pixmap(matrix=pymupdf.Matrix(2.5, 2.5), clip=clip)
        else:
            pix = page.get_pixmap(matrix=pymupdf.Matrix(1.5, 1.5))
        return pix.tobytes("png")
    finally:
        doc.close()


def score_plot(resp, gt_plots, target_fig):
    if not resp or "error" in (resp or {}):
        return {"class": "REVIEW_or_missing", "error": (resp or {}).get("error")}
    series = resp.get("series") or []
    fig_no = resp.get("figure_number_digitized")
    conflicts = resp.get("conflicts") or []
    if not series:
        return {"class": "missing", "series_matched": 0, "figure_no": fig_no,
                "conflicts": conflicts}
    # figure selection: match by printed figure number when present
    gt_titles = {p.get("_figure_index"): (p.get("title") or "") for p in gt_plots} if gt_plots else {}
    s = ab.score(resp, gt_plots) if gt_plots else {"note": "no gt"}
    sel_ok = None
    if fig_no is not None and gt_plots:
        want = None
        for p in gt_plots:
            m = re.search(r"(?:figure|fig\.?)\s*(\d+(?:-\d+)?)", p.get("title") or "", re.I)
            if m and m.group(1) == str(fig_no):
                want = p.get("_figure_index")
        sel_ok = want == target_fig if want is not None else None
    rmse = s.get("rmse_norm")
    usable = bool(series) and (rmse is not None and rmse <= 0.05 or not gt_plots)
    false_supported = sum(1 for a in (s.get("assignments") or [])
                          if a.get("rmse_norm") is not None and a["rmse_norm"] > 0.05)
    return {"class": "ok" if usable else ("wrong" if series else "missing"),
            "rmse_norm": rmse, "series_matched": s.get("series_matched"),
            "series_gt": s.get("series_gt"), "points": s.get("points"),
            "name_hits": s.get("name_hits"), "figure_selection_ok": sel_ok,
            "figure_no": fig_no, "conflicts": conflicts,
            "false_supported_series": false_supported, "usable": usable}


def score_table(resp, page_text):
    if not resp or "error" in (resp or {}):
        return {"class": "REVIEW_or_missing", "error": (resp or {}).get("error")}
    npt = norm(page_text)
    strings = [c for c in (resp.get("columns") or []) if isinstance(c, str)]
    for r in (resp.get("rows") or []):
        if isinstance(r, dict):
            if isinstance(r.get("label"), str):
                strings.append(r["label"])
            strings += [c for c in (r.get("cells") or []) if isinstance(c, str)]
    found = [s for s in strings if norm(s) and norm(s) in npt]
    fabricated = [s for s in strings if norm(s) and norm(s) not in npt]
    expected_hits = [e for e in TABLE_EXPECTED if norm(e) in npt and
                     any(norm(e) in norm(s) or norm(s) in norm(e) for s in found)]
    precision = len(found) / max(1, len(strings))
    usable = len(expected_hits) >= 6
    return {"class": "ok" if usable else "wrong",
            "strings_returned": len(strings), "strings_in_source": len(found),
            "fabricated_strings": fabricated[:5],
            "source_precision": round(precision, 3),
            "expected_hits": len(expected_hits), "expected_total": len(TABLE_EXPECTED),
            "usable": usable, "conflicts": resp.get("conflicts") or []}


def main() -> int:
    pdfs = ROOT / "scratch/power-data-01/pdfs"
    results = []
    envelope_overhead = []
    import os
    only = os.environ.get("AB_ONLY_CLASSES")
    for case in CASES:
        cls, task, pdf_name, page, fig, crop, gt_rel, expected_empty, force_vision = case
        if only and cls not in only.split(","):
            continue
        pdf = pdfs / pdf_name
        # envelope build overhead measured separately
        t0 = time.time()
        env = router.route(pdf, page, fig, pdf_name.split(".")[0], crop,
                           text_layer_chars=_text_len(pdf, page))
        build_dt = time.time() - t0
        envelope_overhead.append({"case": cls, "build_s": round(build_dt, 3)})
        fig_env = None
        if fig is not None:
            for f in env.get("envelope", {}).get("figures", []):
                if f["figure_index"] == fig:
                    fig_env = f
        gt_plots = None
        if gt_rel:
            gt_rec = json.loads((ROOT / "tests/fixtures/gold" / gt_rel).read_text())
            gt_plots = [p for p in gt_rec.get("plots", [])
                        if p.get("_figure_index") == fig]
        # deterministic route first (no vision at all) — unless the cell
        # exists specifically to measure the vision arms (regression test)
        if env["decision"] == "deterministic_no_vision" and not force_vision:
            det = env["deterministic"]
            if gt_plots:
                s = ab.score({"series": det["series"]}, gt_plots)
                rec = {"case": cls, "arm": "D_deterministic", "rep": 0,
                       "route": env["decision"], "score": {
                           "class": "ok", "rmse_norm": s.get("rmse_norm"),
                           "series_matched": s.get("series_matched"),
                           "series_gt": s.get("series_gt"),
                           "usable": True, "inference_s": 0.0}}
            else:
                rec = {"case": cls, "arm": "D_deterministic", "rep": 0,
                       "route": env["decision"], "score": {"class": "ok"}}
            results.append(rec)
            print(json.dumps(rec)[:220], flush=True)
            continue

        png = render(pdf, page, crop, fig_env)
        page_text = _page_text(pdf, page)
        env_json = json.dumps(env.get("envelope", env), ensure_ascii=False)[:6000]
        for rep, order in enumerate((("A_baseline", "B_envelope"),
                                     ("B_envelope", "A_baseline"),
                                     ("A_baseline", "B_envelope"))):
            for arm in order:
                base = TABLE_PROMPT if task == "table" else PLOT_PROMPT
                prompt = base if arm == "A_baseline" else base + TRUST_LINE + env_json
                resp, dt = ab.call_tp4_vision(png, prompt)
                if task == "table":
                    sc = score_table(resp, page_text)
                else:
                    sc = score_plot(resp, gt_plots, fig)
                if expected_empty:
                    series_n = len((resp or {}).get("series") or [])
                    sc["expected_empty"] = True
                    sc["class"] = ("ok" if series_n == 0
                                   else "false_result")
                    sc["usable"] = series_n == 0
                    sc["false_supported_series"] = series_n
                sc["inference_s"] = round(dt, 1)
                rec = {"case": cls, "task": task, "arm": arm, "rep": rep,
                       "route": env["decision"], "score": sc}
                results.append(rec)
                print(json.dumps(rec, default=str)[:260], flush=True)

    out = ROOT / "scratch/m5-soak/context-ab-results.json"
    out.write_text(json.dumps({"results": results,
                               "envelope_build_overhead": envelope_overhead},
                              indent=1, default=str))
    _summary(results)
    return 0


def _text_len(pdf, page_1based):
    doc = pymupdf.open(pdf)
    try:
        return len(doc[page_1based - 1].get_text() or "")
    finally:
        doc.close()


def _page_text(pdf, page_1based):
    doc = pymupdf.open(pdf)
    try:
        return doc[page_1based - 1].get_text() or ""
    finally:
        doc.close()


def _summary(results):
    from collections import defaultdict
    agg = defaultdict(lambda: {"n": 0, "usable": 0, "false_supported": 0,
                               "missing": 0, "wrong": 0, "rmse": [],
                               "sel_ok": 0, "sel_n": 0, "lat": []})
    for r in results:
        key = (r["case"], r["arm"])
        a = agg[key]
        s = r["score"]
        a["n"] += 1
        cls = s.get("class")
        if s.get("usable"):
            a["usable"] += 1
        if cls == "missing":
            a["missing"] += 1
        if cls == "wrong":
            a["wrong"] += 1
        a["false_supported"] += s.get("false_supported_series", 0)
        if s.get("rmse_norm") is not None:
            a["rmse"].append(s["rmse_norm"])
        if s.get("figure_selection_ok") is not None:
            a["sel_n"] += 1
            a["sel_ok"] += 1 if s["figure_selection_ok"] else 0
        if s.get("inference_s") is not None:
            a["lat"].append(s["inference_s"])
    print("\n=== SUMMARY (per case/arm) ===")
    for (case, arm), a in sorted(agg.items()):
        rmse = sum(a["rmse"]) / len(a["rmse"]) if a["rmse"] else None
        lat = sum(a["lat"]) / len(a["lat"]) if a["lat"] else 0
        print(f"{case:26s} {arm:12s} n={a['n']} usable={a['usable']} "
              f"missing={a['missing']} wrong={a['wrong']} "
              f"false_sup={a['false_supported']} rmse={rmse} "
              f"figsel={a['sel_ok']}/{a['sel_n']} lat={lat:.1f}s")


if __name__ == "__main__":
    raise SystemExit(main())
