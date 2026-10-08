#!/usr/bin/env python3
"""Raster-recovery resolution experiment (CURVE-04 R3).

Ground truth: the TPS548C26 p.10 vector references (deterministic,
tick-fit residual ~0.01% span). The page is re-rendered as an image at
200 / 300 / 600 DPI and recovered by the experimental raster lane; series
are paired to the reference polylines by trace color order within
bbox-matched plots, and per-point y error is measured in % of the y span.

Higher DPI is NOT assumed better: the table reports plots recovered,
series paired, median/max error, OCR words, and wall time per DPI.

Also attempts TLS4125D0EPV p.18 (the held raster challenge) and records
its plots/refusals into the challenge manifest.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harness.electronics.raster_curves import extract_raster_page

PDF = Path("/Volumes/M5_4TB/data/ti_power_datasheets/dcdc_TPS548C26.pdf")
REFERENCE = (
    ROOT / "tests/fixtures/gold/curve_evidence_pilot/tps548c26_p10.json"
)
TLS = Path(
    "/Volumes/M5_4TB/exports/corpus-deliveries/datasheet-tail-20261007/"
    "blobs/14/cc/14cc7b1af8ba7af289f7bf24d7fda16449a743f3eb611c2502899e"
    "36f32e7478.pdf"
)
OUT = ROOT / "results/curve-04-raster"


def _interp_ref(points, x):
    ordered = sorted(points)
    xs = [p[0] for p in ordered]
    if x < xs[0] or x > xs[-1]:
        return None
    for a, b in zip(ordered, ordered[1:]):
        if a[0] <= x <= b[0] and b[0] != a[0]:
            t = (x - a[0]) / (b[0] - a[0])
            return a[1] + t * (b[1] - a[1])
    return ordered[-1][1]


def _pair_plots(raster_plots, ref_plots, dpi: int):
    """Match raster plots to reference plots by frame geometry, raster px
    converted to PDF points (px * 72 / dpi)."""

    scale = 72.0 / dpi
    pairs = []
    for rp in raster_plots:
        c0, r0, c1, r1 = [v * scale for v in rp["_bbox_page_px"]]
        best, best_iou = None, 0.0
        for ref in ref_plots:
            ref_box = ref.get("_bbox")
            if not ref_box:
                continue
            overlap = (
                (min(c1, ref_box[2]) - max(c0, ref_box[0]))
                * (min(r1, ref_box[3]) - max(r0, ref_box[1]))
            )
            union = (c1 - c0) * (r1 - r0) + \
                (ref_box[2] - ref_box[0]) * (ref_box[3] - ref_box[1]) - \
                overlap
            iou = max(0.0, overlap) / union if union > 0 else 0.0
            if iou > best_iou:
                best_iou, best = iou, ref
        if best is not None and best_iou > 0.2:
            pairs.append((rp, best, best_iou))
    return pairs


def run_dpi(dpi: int) -> dict:
    t0 = time.time()
    raster = extract_raster_page(PDF, 10, dpi=dpi,
                                 render_dir=OUT / "renders")
    reference = json.loads(REFERENCE.read_text())
    ref_plots = reference["plots"]
    # reference fixtures carry page-pt bboxes in _bbox when present; the
    # frozen tps file records region bboxes only per page — fall back to
    # order+span matching when _bbox is absent
    for p in ref_plots:
        p.setdefault("_bbox", None)
    if not any(p["_bbox"] for p in ref_plots):
        for i, p in enumerate(ref_plots):
            # approximate frames from CURVE-03 extraction (page pt coords)
            p["_bbox"] = [
                [95.0, 101.9, 291.0, 226.9],
                [337.0, 102.0, 540.2, 231.6],
                [95.0, 301.2, 291.0, 426.2],
                [337.0, 301.2, 540.2, 430.8],
                [95.0, 500.4, 291.0, 625.4],
                [337.0, 500.5, 540.2, 630.1],
            ][i]
    pairs = _pair_plots(raster["plots"], ref_plots, dpi)
    errors = []
    paired_series = 0
    for rp, ref, _iou in pairs:
        y_vals = [pt[1] for s in ref["series"] for pt in s["points"]]
        y_lo, y_hi = min(y_vals), max(y_vals)
        span = (y_hi - y_lo) or 1.0
        # pair raster traces to reference series by rank of mean y
        # (colors do not transfer between substrates; rank is stable for
        # well-separated curves and honest pairing is per-plot median)
        ref_sorted = sorted(
            ref["series"],
            key=lambda s: sum(p[1] for p in s["points"]) / len(s["points"]),
        )
        ras_sorted = sorted(
            rp["series"],
            key=lambda s: sum(p[1] for p in s["points"]) / len(s["points"]),
        )
        for ras, ref_s in zip(ras_sorted, ref_sorted):
            paired_series += 1
            for x, y in ras["points"]:
                ref_y = _interp_ref(ref_s["points"], x)
                if ref_y is None:
                    continue
                errors.append(abs(y - ref_y) / span * 100.0)
    errors.sort()
    median = errors[len(errors) // 2] if errors else None
    p95 = errors[int(len(errors) * 0.95)] if errors else None
    return {
        "dpi": dpi,
        "elapsed_s": round(time.time() - t0, 2),
        "extract_elapsed_s": raster["elapsed_s"],
        "reference_plots": len(ref_plots),
        "plots_recovered": len(raster["plots"]),
        "plots_paired_by_bbox": len(pairs),
        "series_paired": paired_series,
        "reference_series": sum(len(p["series"]) for p in ref_plots),
        "points_compared": len(errors),
        "y_error_pct_median": round(median, 4) if median is not None else None,
        "y_error_pct_p95": round(p95, 4) if p95 is not None else None,
        "y_error_pct_max": round(errors[-1], 4) if errors else None,
        "refusals": raster["refusals"],
    }


def run_tls(dpi: int) -> dict:
    if not TLS.exists():
        return {"dpi": dpi, "status": "document_not_held"}
    try:
        r = extract_raster_page(TLS, 18, dpi=dpi, render_dir=OUT / "renders")
        return {
            "dpi": dpi,
            "status": "extracted" if r["plots"] else "refused",
            "plots": len(r["plots"]),
            "refusals": r["refusals"][:8],
            "elapsed_s": r["elapsed_s"],
            "document_sha256": r["document_sha256"],
        }
    except Exception as exc:  # pragma: no cover - environment dependent
        return {"dpi": dpi, "status": f"error:{type(exc).__name__}"}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    report = {
        "schema": "harness.electronics-raster-resolution-experiment.v1",
        "ground_truth": "vector references (tps548c26_p10.json); tick-fit "
                        "residual ~0.01% of span; same-page comparison",
        "dpi_runs": [run_dpi(d) for d in (200, 300, 600)],
        "tls4125d0epv_p18": [run_tls(d) for d in (300, 600)],
        "laws": [
            "higher DPI is not assumed better; the table is the verdict",
            "raster outputs are machine references only — adjudication "
            "gates apply identically to vector outputs",
        ],
    }
    (OUT / "resolution_experiment.json").write_text(
        json.dumps(report, indent=2, default=str) + "\n"
    )
    summary = [
        {k: v for k, v in row.items() if k != "refusals"}
        for row in report["dpi_runs"]
    ]
    print(json.dumps({"dpi_runs": summary,
                      "tls": report["tls4125d0epv_p18"]}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
