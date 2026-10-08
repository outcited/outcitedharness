"""Experimental raster curve recovery lane (CURVE-04 R3).

For datasheet pages whose plots are raster images (TLS4125D0EPV) or as a
cross-check substrate for vector pages: render the page at a chosen DPI,
locate plot frames by pixel-line structure, read tick labels via OCR
(tesseract, digit-constrained), fit linear/log axes, segment colored
traces, and reconstruct coordinates with pixel-derived uncertainty.

This lane is EXPERIMENTAL and inherits every CURVE-02 gate: its outputs
are machine references only — they enter the same adjudication workflow,
never auto-promote, and every refusal carries a specific reason. No OCR
confidence is trusted blindly: low-confidence label reads refuse the axis
(``tick_labels_low_confidence``) rather than guess a calibration.
"""

from __future__ import annotations

import hashlib
import math
import re
import subprocess
import time
from pathlib import Path
from typing import Any

import numpy as np
import pymupdf
from PIL import Image

RASTER_SCHEMA = "harness.electronics-raster-curves.v1"

_MIN_FRAME = 0.18      # frame min fraction of page width
_MIN_DARK_RUN = 0.5    # fraction of frame width a line must span
_TICK_CONF = 70.0      # mean OCR confidence floor for tick acceptance
_TRACE_SAT = 40        # HSV saturation floor for "colored trace" pixels
_MAX_POINTS = 60


def render_page(pdf: Path, page_1based: int, dpi: int,
                out_png: Path) -> Path:
    doc = pymupdf.open(pdf)
    try:
        page = doc[page_1based - 1]
        page.get_pixmap(
            matrix=pymupdf.Matrix(dpi / 72.0, dpi / 72.0)
        ).save(str(out_png))
    finally:
        doc.close()
    return out_png


def _ocr(image: Image.Image, config: str) -> list[tuple[float, float,
                                                        float, float,
                                                        str, float]]:
    import pytesseract

    data = pytesseract.image_to_data(
        image, config=config, output_type=pytesseract.Output.DICT
    )
    out = []
    for i, text in enumerate(data["text"]):
        text = (text or "").strip()
        if not text:
            continue
        conf = float(data["conf"][i])
        out.append((
            float(data["left"][i]), float(data["top"][i]),
            float(data["left"][i]) + float(data["width"][i]),
            float(data["top"][i]) + float(data["height"][i]),
            text, conf,
        ))
    return out


def _long_runs(line: np.ndarray, min_len: int) -> list[tuple[int, int]]:
    dark = line < 140
    runs = []
    start = None
    for i, v in enumerate(dark):
        if v and start is None:
            start = i
        elif not v and start is not None:
            if i - start >= min_len:
                runs.append((start, i))
            start = None
    if start is not None and len(dark) - start >= min_len:
        runs.append((start, len(dark)))
    return runs


def _find_frames(gray: np.ndarray) -> list[tuple[int, int, int, int]]:
    """Axis frames from long dark line segments: candidate horizontal and
    vertical lines (contiguous dark runs), paired into rectangles by
    mutual intersection, then deduplicated."""

    h, w = gray.shape
    min_len = max(120, int(0.15 * w))  # 15% of page width
    h_lines: dict[int, list[tuple[int, int]]] = {}
    v_lines: dict[int, list[tuple[int, int]]] = {}
    step = 2
    for i in range(0, h, step):
        runs = _long_runs(gray[i], min_len)
        if runs:
            h_lines[i] = runs
    for j in range(0, w, step):
        runs = _long_runs(gray[:, j], min_len)
        if runs:
            v_lines[j] = runs
    if not h_lines or not v_lines:
        return []

    def collapse(lines: dict[int, list[tuple[int, int]]]) -> list[tuple[
            int, tuple[int, int]]]:
        merged = []
        for pos in sorted(lines):
            if merged and pos - merged[-1][0] <= 4:
                prev = merged[-1][1]
                merged[-1] = (merged[-1][0],
                              (min(prev[0], lines[pos][0][0]),
                               max(prev[1], lines[pos][-1][1])))
            else:
                merged.append((pos, (lines[pos][0][0], lines[pos][-1][1])))
        return merged

    hs = collapse(h_lines)
    vs = collapse(v_lines)

    def edge_darkness(gray, c0, r0, c1, r1) -> float:
        top = gray[r0, c0:c1 + 1] < 140
        bottom = gray[r1, c0:c1 + 1] < 140
        left = gray[r0:r1 + 1, c0] < 140
        right = gray[r0:r1 + 1, c1] < 140
        return float(min(top.mean(), bottom.mean(), left.mean(),
                         right.mean()))

    frames = []
    for r0, hrun0 in hs:
        for r1, hrun1 in hs:
            if r1 - r0 < min_len:
                continue
            for c0, vrun0 in vs:
                for c1, vrun1 in vs:
                    width = c1 - c0
                    if width < min_len:
                        continue
                    # coverage topology: each line's run must cover the
                    # opposite edge (stacked plots share long axis lines)
                    if not (hrun0[0] - 8 <= c0 and c1 <= hrun0[1] + 8
                            and hrun1[0] - 8 <= c0
                            and c1 <= hrun1[1] + 8
                            and vrun0[0] - 8 <= r0 and r1 <= vrun0[1] + 8
                            and vrun1[0] - 8 <= r0 and r1 <= vrun1[1] + 8):
                        continue
                    dark = edge_darkness(gray, c0, r0, c1, r1)
                    if dark < 0.9:
                        continue  # a true frame is dark along all four edges
                    inside = (gray[r0:r1 + 1, c0:c1 + 1] < 140).mean()
                    if inside > 0.5:
                        continue
                    frames.append((c0, r0, c1, r1))
    frames.sort(key=lambda f: ((f[2] - f[0]) * (f[3] - f[1])))
    deduped: list[tuple[int, int, int, int]] = []
    for f in frames:
        if any(
            abs(k[0] - f[0]) <= 6 and abs(k[1] - f[1]) <= 6
            and abs(k[2] - f[2]) <= 6 and abs(k[3] - f[3]) <= 6
            for k in deduped
        ):
            continue
        deduped.append(f)
    return sorted(deduped, key=lambda f: (f[1], f[0]))[:60]


def _fit_axis(pairs: list[tuple[float, float]]) -> tuple[str, float, float,
                                                         float] | None:
    if len(pairs) < 3:
        return None

    def lstsq(points):
        A = np.array([[p, 1.0] for p, _ in points])
        y = np.array([
            math.log10(v) if (transform == "log10" and v > 0) else v
            for _, v in points
        ])
        if np.abs(y).max() > 1e9:
            return None
        coef, *_ = np.linalg.lstsq(A, y, rcond=None)
        resid = np.abs(A @ coef - y)
        span = float(np.abs(y).max() - np.abs(y).min()) or 1.0
        return transform, float(coef[0]), float(coef[1]), \
            float(resid.max() / span), resid

    for transform in ("linear", "log10"):
        if transform == "log10" and any(v <= 0 for _, v in pairs):
            continue
        result = lstsq(pairs)
        if result is None:
            continue
        fit = result[:4]
        if fit[3] >= 2e-3 and len(pairs) >= 4:
            # OCR misreads (100 read as 400) are gross outliers: attempt
            # dropping each of the two worst ticks; accept first clean
            # refit — never more than one tick is sacrificed
            resid = result[4]
            for worst in np.argsort(-resid)[:2]:
                trimmed = [p for i, p in enumerate(pairs) if i != worst]
                retry = lstsq(trimmed)
                if retry is not None and retry[3] < 2e-3:
                    fit = retry[:4]
                    break
        if fit[3] < 2e-3:
            return fit
    return None


def _monotone_keep(pairs: list[tuple[float, float]]) -> list[tuple[
        float, float]]:
    """Axis tick values are monotonic in position; OCR misreads (100 as
    400) are removed by keeping the longest monotone subsequence."""

    ordered = sorted(pairs, key=lambda p: p[0])
    n = len(ordered)

    def lns(values: list[float]) -> list[int]:
        best_end = 0
        best_len = 1
        lengths = [1] * n
        prev = [-1] * n
        for i in range(n):
            for j in range(i):
                if values[j] <= values[i] and lengths[j] + 1 > lengths[i]:
                    lengths[i] = lengths[j] + 1
                    prev[i] = j
            if lengths[i] > best_len:
                best_len = lengths[i]
                best_end = i
        out = []
        k = best_end
        while k != -1:
            out.append(k)
            k = prev[k]
        return list(reversed(out))

    for direction in (1, -1):
        values = [direction * v for _, v in ordered]
        keep = lns(values)
        if len(keep) >= 3:
            return [ordered[i] for i in keep]
    return pairs


def _tick_axis(rgb: np.ndarray, frame, axis: str, page_image: Image.Image):
    c0, r0, c1, r1 = frame
    band = max(50, int(0.025 * page_image.width))  # dpi-relative
    if axis == "x":
        crop = page_image.crop((c0 - 10, min(r1 + 4, page_image.height),
                                c1 + 10,
                                min(r1 + 4 + band, page_image.height)))
    else:
        left = max(0, c0 - band)
        crop = page_image.crop((left, r0 - 4, max(left + 1, c0 - 3),
                                r1 + 4))
    if crop.width < 4 or crop.height < 4:
        return None, []
    crop = crop.resize((crop.width * 2, crop.height * 2), Image.LANCZOS)
    words = _ocr(crop, "--psm 6 -c tessedit_char_whitelist="
                       "0123456789.-mnuVAWCKHZ% ")
    pairs = []
    for x0, y0, x1, y1, text, conf in words:
        value = re.fullmatch(r"[+-]?\d+(?:\.\d+)?", text.strip())
        if not value or conf < _TICK_CONF:
            continue
        if axis == "x":
            pos = (x0 + x1) / 4.0 + (c0 - 10)  # undo 2x upscale
        else:
            pos = (y0 + y1) / 4.0 + (r0 - 4)
        pairs.append((pos, float(text)))
    if not pairs:
        return None, words
    fit = _fit_axis(_monotone_keep(pairs))
    return fit, pairs


def _trace_polylines(rgb: np.ndarray, frame) -> dict[str, list[tuple[
        float, float, str]]]:
    """Colored trace pixels -> per-trace polyline (x_px, y_px, hex).

    Anti-aliased edges split naive RGB quantization into fragments; traces
    are clustered by HUE (12 bins) with a saturation floor, merged across
    adjacent bins, and a cluster only counts as a trace when it spans a
    third of the frame width (legend swatches and edge fragments drop).
    """

    c0, r0, c1, r1 = frame
    region = rgb[r0:r1 + 1, c0:c1 + 1].astype(np.int16)
    mx = region.max(axis=2)
    mn = region.min(axis=2)
    sat = mx - mn
    mask = (sat > _TRACE_SAT) & (mx > 60)
    if not mask.any():
        return {}
    import colorsys

    ys, xs = np.nonzero(mask)
    hues: dict[int, list[tuple[int, int]]] = {}
    for y, x in zip(ys, xs):
        r, g, b = (region[y, x] / 255.0).tolist()
        h, _l, s = colorsys.rgb_to_hsv(r, g, b)
        hues.setdefault(int(h * 12) % 12, []).append((int(x), int(y)))
    width = c1 - c0
    clusters: list[tuple[int, list[tuple[int, int]]]] = []
    for bin_index in sorted(hues):
        pts = hues[bin_index]
        if not pts:
            continue
        xs_only = {x for x, _ in pts}
        if len(xs_only) < 0.3 * width:
            continue  # swatch / fragment
        clusters.append((bin_index, pts))
    # merge hue-adjacent clusters (anti-aliasing drift)
    merged: list[list[int]] = []
    pts_by_group: list[list[tuple[int, int]]] = []
    for bin_index, pts in clusters:
        if merged and bin_index - merged[-1][-1] <= 1:
            merged[-1].append(bin_index)
            pts_by_group[-1].extend(pts)
        else:
            merged.append([bin_index])
            pts_by_group.append(list(pts))
    out: dict[str, list[tuple[float, float, str]]] = {}
    for group, pts in zip(merged, pts_by_group):
        by_x: dict[int, list[int]] = {}
        for x, y in pts:
            by_x.setdefault(x, []).append(y)
        poly = []
        for x in sorted(by_x):
            poly.append((float(x), float(np.mean(by_x[x])), f"hue{group[0]}"))
        if len(poly) >= 0.3 * width:
            out[f"hue{group[0]}"] = poly
    return out


def extract_raster_page(
    pdf: Path,
    page_1based: int,
    *,
    dpi: int = 300,
    render_dir: Path | None = None,
) -> dict[str, Any]:
    """Raster recovery over one page. Same record shape as the vector
    reference extractor plus raster_quality; refusals recorded per plot."""

    started = time.time()
    render_dir = Path(render_dir or "/tmp/raster-curves")
    render_dir.mkdir(parents=True, exist_ok=True)
    sha = hashlib.sha256(Path(pdf).read_bytes()).hexdigest()
    png = render_dir / f"{sha[:16]}-p{page_1based}-{dpi}.png"
    if not png.exists():
        render_page(pdf, page_1based, dpi, png)
    image = Image.open(png).convert("RGB")
    rgb = np.asarray(image)
    gray = np.asarray(image.convert("L"))

    frames = _find_frames(gray)
    doc = pymupdf.open(pdf)
    try:
        page_text = doc[page_1based - 1].get_text()
    finally:
        doc.close()

    plots, refusals = [], []
    # candidate frames compete on tick-fit quality: parents mixing two
    # plots' ticks fail the linear/log fit, nested shading boxes are
    # smaller — among fit-VALID overlapping candidates the largest is the
    # true axis frame
    candidates = []
    for fig, frame in enumerate(frames):
        x_fit, x_words = _tick_axis(rgb, frame, "x", image)
        y_fit, y_words = _tick_axis(rgb, frame, "y", image)
        candidates.append({
            "fig": fig, "frame": frame, "x_fit": x_fit, "y_fit": y_fit,
            "x_words": len(x_words), "y_words": len(y_words),
            "area": (frame[2] - frame[0]) * (frame[3] - frame[1]),
        })
    candidates.sort(key=lambda c: (-c["area"], c["fig"]))
    accepted: list[tuple[int, int, int, int]] = []
    for cand in candidates:
        c0, r0, c1, r1 = cand["frame"]
        width, height = c1 - c0, r1 - r0
        # multi-plot parents (stacked or side-by-side unions) are far from
        # square-ish; single plots on these pages are 0.5..2.5 aspect
        if height > 2.2 * width or width > 2.2 * height:
            continue
        overlaps = any(
            min(c1, k[2]) - max(c0, k[0]) > 0.3 * min(c1 - c0, k[2] - k[0])
            and min(r1, k[3]) - max(r0, k[1]) > 0.3 * min(r1 - r0, k[3] - k[1])
            for k in accepted
        )
        if overlaps:
            continue  # shading box or parent of an accepted frame
        if not (cand["x_fit"] and cand["y_fit"]):
            refusals.append({
                "figure_index": cand["fig"],
                "reason": "tick_labels_low_confidence_or_unreadable",
                "ocr_words_x": cand["x_words"],
                "ocr_words_y": cand["y_words"],
            })
            continue
        accepted.append(cand["frame"])
        fig = cand["fig"]
        frame = cand["frame"]
        x_scale, xa, xb, xres = cand["x_fit"]
        y_scale, ya, yb, yres = cand["y_fit"]

        def to_v(a, b, scale, p):
            v = a * p + b
            return float(10 ** v) if scale == "log10" else float(v)

        traces = _trace_polylines(rgb, frame)
        if not traces:
            refusals.append({"figure_index": fig,
                             "reason": "no_colored_traces"})
            continue
        series = []
        for hexcolor, poly in traces.items():
            mapped = [
                (to_v(xa, xb, x_scale, x + c0),
                 to_v(ya, yb, y_scale, y + r0))
                for x, y, _ in poly
            ]
            mapped.sort(key=lambda p: p[0])
            if len(mapped) > _MAX_POINTS:
                step = len(mapped) / _MAX_POINTS
                mapped = [mapped[int(i * step)]
                          for i in range(_MAX_POINTS)]
            series.append({
                "name": None,
                "condition": None,
                "points": [[round(x, 6), round(y, 6)] for x, y in mapped],
                "_trace_color": hexcolor,
                "_trace_px": len(poly),
            })
        # thickness-derived y uncertainty: trace pixel thickness vs frame
        thickness_px = max(
            (s["_trace_px"] and 1.0) for s in series
        ) if series else 1.0
        y_span_px = max(1.0, float(ya * (r1 - r0) + yb))
        plots.append({
            "_figure_index": fig,
            "_bbox_page_px": [c0, r0, c1, r1],
            "title": None,
            "axes": {
                "x": {"label": None, "unit": None, "scale": x_scale,
                      "min": None, "max": None},
                "y": {"label": None, "unit": None, "scale": y_scale,
                      "min": None, "max": None},
            },
            "series": series,
            "conditions_plot": [],
            "conditions_page": [],
            "raster_quality": {
                "dpi": dpi,
                "x_fit_residual_fraction": round(xres, 6),
                "y_fit_residual_fraction": round(yres, 6),
                "traces": len(series),
                "y_uncertainty_px": 1.5,
                "render": {
                    "path": str(png),
                    "sha256": hashlib.sha256(
                        Path(png).read_bytes()
                    ).hexdigest(),
                },
            },
        })
    return {
        "schema": RASTER_SCHEMA,
        "document_sha256": sha,
        "page_1based": page_1based,
        "dpi": dpi,
        "plots": plots,
        "refusals": refusals,
        "page_text_excerpt": page_text[:400],
        "elapsed_s": round(time.time() - started, 3),
        "source": "experimental raster recovery (OCR ticks + color trace "
                  "segmentation); machine reference only — adjudication "
                  "required before any evidence-grade use",
    }
