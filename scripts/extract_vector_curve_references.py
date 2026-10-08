#!/usr/bin/env python3
"""Vector curve reference extractor — gold-building tool for the curve pilot.

Deterministically recovers plotted curve points from vector-drawn datasheet
plots (TI-style): axis frames and curve traces are PDF vector paths, tick
labels and legends are page words. Produces reference ("gold") files in the
``harness.electronics-typical-curves-gold.v1`` plot schema so the existing
scorer and the curve-evidence pilot can consume them unchanged.

This is a LABELING tool, not a runtime digitizer: it never feeds the
production curve lane (the vision lane owns extraction); it builds
hand-reviewable references cheaply. Every plot is fail-closed — a plot that
cannot satisfy the geometric assumptions ships as a skip row with the
reason, never guessed points.

Pipeline per page:
  1. frames: big vector rects, deduped, each claiming the numeric tick words
     within 30 pt of its left/bottom edge
  2. axis maps: least-squares fit value~position over >= 3 ticks per axis
     (log10 fallback when the linear fit residuals explode), residual-checked
  3. labels/units: non-numeric word runs under the frame (x) and stacked
     beside it (y, rotated text); unit from the trailing "(unit)" token
  4. curves: multi-segment stroke paths inside the frame, chained along the
     path, mapped to data coordinates
  5. legend: short stroke paths (swatches) inside the frame matched to the
     word row on their right; curves attach to names by color. A curve color
     with no swatch is a skip reason (legend unresolvable), unless the plot
     has exactly one curve color.
  6. conditions: '=' word rows inside the frame (plot-level) plus the
     page-level "unless otherwise specified" header; the x-axis quantity is
     the sweep variable and is excluded from fixed conditions
  7. caption: nearest Figure word-run below the frame

Output: one JSON per page next to --out (schema-compatible with the gold
fixtures; extra keys are additive and ignored by the wave-A scorer).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harness.electronics.typical_curves import _CAPTION  # noqa: E402

GOLD_SCHEMA = "harness.electronics-typical-curves-gold.v1"
_NUM_TICK = re.compile(r"[+-]?\d+(?:\.\d+)?")
_MIN_SEGS = 5
_TICK_BAND = 32.0
_MAX_POINTS = 60


def _words(page) -> list[tuple[float, float, float, float, str]]:
    return [
        (float(w[0]), float(w[1]), float(w[2]), float(w[3]), str(w[4]))
        for w in page.get_text("words")
    ]


def _center(w):
    return ((w[0] + w[2]) / 2.0, (w[1] + w[3]) / 2.0)


def _frames(page, words) -> list[tuple[float, float, float, float]]:
    ticks = [w for w in words if _NUM_TICK.fullmatch(w[4])]
    rects = []
    for d in page.get_drawings():
        r = d.get("rect")
        if r is None or r.width < 80 or r.height < 60:
            continue
        box = (float(r.x0), float(r.y0), float(r.x1), float(r.y1))
        near = sum(
            1
            for w in ticks
            if box[0] - _TICK_BAND <= _center(w)[0] <= box[2] + _TICK_BAND
            and box[1] - _TICK_BAND <= _center(w)[1] <= box[3] + _TICK_BAND
        )
        if near >= 4:
            rects.append(box)
    # near-identical duplicates first (TI prints frames 2-3x with sub-point
    # jitter; with containment slop they would mutually contain and drop
    # each other), then containment dedupe of genuinely nested boxes
    uniq: list[tuple[float, float, float, float]] = []
    for box in rects:
        twin = any(
            all(abs(box[k] - u[k]) <= 2.5 for k in range(4)) for u in uniq
        )
        if not twin:
            uniq.append(box)
    kept: list[tuple[float, float, float, float]] = []
    for i, box in enumerate(uniq):
        contained = any(
            j != i
            and other[0] - 4 <= box[0] and other[1] - 4 <= box[1]
            and box[2] <= other[2] + 4 and box[3] <= other[3] + 4
            for j, other in enumerate(uniq)
        )
        if not contained:
            kept.append(box)
    kept.sort(key=lambda b: (b[1], b[0]))
    return kept


def _ticks_for_axis(words, box, axis: str) -> tuple[float, list[tuple[float, float]]]:
    """Ticks share one aligned row (x axis) or column (y axis) just outside
    the frame; condition numerals printed under the axis (e.g. "PVIN = 12
    V") sit on different rows and must not pollute the fit. Returns
    (row_center, [(position, value)]) where position is along the axis."""

    cands = []
    for w in words:
        if not _NUM_TICK.fullmatch(w[4]):
            continue
        cx, cy = _center(w)
        if axis == "x":
            if box[3] <= cy <= box[3] + _TICK_BAND and box[0] - 8 <= cx <= box[2] + 8:
                cands.append((cy, cx, float(w[4])))
        else:
            if box[0] - _TICK_BAND <= cx <= box[0] and box[1] - 8 <= cy <= box[3] + 8:
                cands.append((cx, cy, float(w[4])))
    if len(cands) < 3:
        return (float("nan"), [])
    groups: list[list[tuple[float, float, float]]] = []
    for c in sorted(cands):
        key = c[0]
        if groups and abs(groups[-1][0][0] - key) <= 4.0:
            groups[-1].append(c)
        else:
            groups.append([c])
    rows = sorted(groups, key=lambda g: -len(g))
    best = rows[0]
    if len(best) < 3:
        return (float("nan"), [])
    if axis == "x":
        span = max(c[1] for c in best) - min(c[1] for c in best)
        if span < 0.5 * (box[2] - box[0]):
            return (float("nan"), [])
        pairs = sorted(((c[1], c[2]) for c in best), key=lambda t: t[0])
    else:
        span = max(c[1] for c in best) - min(c[1] for c in best)
        if span < 0.5 * (box[3] - box[1]):
            return (float("nan"), [])
        pairs = sorted(((c[1], c[2]) for c in best), key=lambda t: t[0])
    row_center = sum(c[0] for c in best) / len(best)
    return (row_center, pairs)


def _fit_linear(pairs) -> tuple[float, float, float] | None:
    n = len(pairs)
    if n < 2:
        return None
    sx = sum(p for p, _ in pairs)
    sy = sum(v for _, v in pairs)
    sxx = sum(p * p for p, _ in pairs)
    sxy = sum(p * v for p, v in pairs)
    den = n * sxx - sx * sx
    if abs(den) < 1e-9:
        return None
    a = (n * sxy - sx * sy) / den
    b = (sy - a * sx) / n
    resid = max(abs(a * p + b - v) for p, v in pairs)
    span = max(v for _, v in pairs) - min(v for _, v in pairs) or 1.0
    return (a, b, resid / abs(span))


def _fit_axis(pairs):
    """Linear first; log10 when tick values are decades on a linear grid.
    Returns (kind, slope, intercept, residual-as-fraction-of-value-span)."""

    lin = _fit_linear(pairs)
    if lin and lin[2] < 1e-3:
        return ("linear", lin[0], lin[1], lin[2])
    log_pairs = [(p, (v if v > 0 else None)) for p, v in pairs]
    if all(v is not None for _, v in log_pairs):
        logv = _fit_linear([(p, math.log10(v)) for p, v in log_pairs])
        if logv and logv[2] < 1e-3:
            return ("log10", logv[0], logv[1], logv[2])
    return None


_COND_SPLIT = re.compile(
    r"(?=\b(?:PVIN|VIN|VOUT|VCC|VDD|VEN|VFB|EN|TA|TJ|TC|MODE|IOUT|"
    r"FS|ƒS|fSW|FSW|L|C)\s*=)",
    re.I,
)


def _split_condition_row(text: str) -> list[str]:
    """One printed row can carry several conditions ("VCC = External 5 V
    Bias VOUT = 1.1 V" when the gap heuristic merges them); split at each
    known symbol boundary so typed parsing sees one condition per string."""

    parts = [p.strip(" ,;") for p in _COND_SPLIT.split(text) if p.strip(" ,;")]
    return parts if parts else [text]


def _rows_between(words, y_lo, y_hi, x_lo, x_hi) -> list[list[tuple]]:
    """Word rows (sorted by x) whose band sits in [y_lo, y_hi] and words in
    [x_lo, x_hi] — label/condition rows keep numerics, unlike ticks."""

    rows: dict[int, list[tuple]] = {}
    for w in words:
        cy = _center(w)[1]
        cx = _center(w)[0]
        if y_lo <= cy <= y_hi and x_lo <= cx <= x_hi:
            rows.setdefault(int(cy // 4), []).append(w)
    out = []
    for band in sorted(rows):
        members = sorted(rows[band], key=lambda w: w[0])
        joined = members[0]
        row = [members[0]]
        for w in members[1:]:
            if w[0] - row[-1][2] > 18.0:  # column break -> separate row run
                out.append(row)
                row = [w]
            else:
                row.append(w)
        out.append(row)
    return out


def _below_frame_rows(words, box, tick_row_y: float) -> list[tuple[str, list | str]]:
    """Classify word rows under the frame, top to bottom: tick row, x-axis
    label row (first row without '=' and without a Figure caption), then
    '='-condition rows. Shared by labels and condition capture."""

    rows = _rows_between(
        words, tick_row_y + 2.0, box[3] + _TICK_BAND + 40,
        box[0] - 40, box[2] + 40,
    )
    out: list[tuple[str, list | str]] = []
    label_taken = False
    for row in rows:
        text = " ".join(w[4] for w in row)
        if _CAPTION.search(text):
            break
        if "=" in text:
            out.append(("condition", text))
            continue
        if all(_NUM_TICK.fullmatch(w[4]) for w in row):
            continue
        if not label_taken:
            label_taken = True
            out.append(("xlabel", row))
    return out


def _axis_label(words, box, axis: str, tick_x_min: float | None = None,
                below_rows: list | None = None) -> str:
    if axis == "x":
        if below_rows:
            for kind, payload in below_rows:
                if kind == "xlabel":
                    return " ".join(w[4] for w in payload)
        return ""
    hi = (tick_x_min - 6.0) if tick_x_min is not None else box[0] - _TICK_BAND
    # rotated y-label words are taller than wide; legends and neighbor-plot
    # text are horizontal and must not leak into the label
    cands = [
        w for w in words
        if _center(w)[1] >= box[1] - 8 and _center(w)[1] <= box[3] + 8
        and box[0] - 90 <= _center(w)[0] <= hi
        and (w[2] - w[0]) < (w[3] - w[1])
        and not _NUM_TICK.fullmatch(w[4])
    ]
    if not cands:
        return ""
    # nearest column cluster to the frame = this plot's label stack
    cands.sort(key=lambda w: _center(w)[0])
    columns: list[list[tuple]] = []
    for w in cands:
        if columns and abs(_center(w)[0] - _center(columns[-1][-1])[0]) <= 8.0:
            columns[-1].append(w)
        else:
            columns.append([w])
    stack = max(columns, key=lambda col: _center(col[-1])[0])
    stack.sort(key=lambda w: -_center(w)[1])
    return " ".join(w[4] for w in stack)


def _unit_of(label: str) -> str | None:
    m = re.search(r"\(([^()]{1,12})\)\s*$", label)
    return m.group(1) if m else None


def _inside(box, r, pad=2.0) -> bool:
    return box[0] - pad <= r.x0 and r.x1 <= box[2] + pad and box[1] - pad <= r.y0 and r.y1 <= box[3] + pad


def _chain_points(drawing) -> list[tuple[float, float]]:
    pts: list[tuple[float, float]] = []
    for item in drawing["items"]:
        if item[0] == "l":
            p0, p1 = item[1], item[2]
        elif item[0] == "c":
            p0, p1 = item[1], item[4]
        else:
            continue
        if not pts:
            pts.append((p0.x, p0.y))
        lx, ly = pts[-1]
        if abs(lx - p0.x) > 1.5 or abs(ly - p0.y) > 1.5:
            pts.append((p0.x, p0.y))
        pts.append((p1.x, p1.y))
    return pts


def _color_key(color) -> str:
    if not color:
        return "none"
    return ",".join(f"{c:.3f}" for c in color)


def _page_lines(words) -> list[tuple[str, tuple[float, float, float, float]]]:
    """All word rows joined per y-band x-run (no caption filter)."""

    lines: dict[int, list[tuple]] = {}
    for w in words:
        lines.setdefault(int(w[1] // 4), []).append(w)
    out = []
    for band in sorted(lines):
        members = sorted(lines[band], key=lambda w: w[0])
        runs: list[list[tuple]] = []
        for w in members:
            if runs and w[0] - runs[-1][-1][2] > 24.0:
                runs.append([w])
            elif runs:
                runs[-1].append(w)
            else:
                runs.append([w])
        for run in runs:
            out.append((
                " ".join(w[4] for w in run),
                (
                    min(w[0] for w in run), min(w[1] for w in run),
                    max(w[2] for w in run), max(w[3] for w in run),
                ),
            ))
    return out


_TITLE_VS = re.compile(
    r"^\s*(?:Fig(?:ure)?\.?\s*\d+\s*[-.:]?\s*)?(.+?)\s+vs\.?\s+(.+?)"
    r"(?:\s*[,.]\s*)?$",
    re.I,
)


def _label_tokens(text: str) -> set[str]:
    return {
        t for t in re.findall(r"[a-z]+", str(text or "").lower())
        if t not in ("the", "and", "of", "a")
    }


def _title_axes_note(caption: str, x_label: str, y_label: str) -> str | None:
    """Vendor titles can contradict the printed axes ("Load Current vs.
    Case Temperature" with current on x). The printed labels are
    authoritative; the mismatch is flagged for the human review packet,
    never silently corrected."""

    m = _TITLE_VS.match(str(caption or "").strip())
    if not m:
        return None
    head, tail = _label_tokens(m.group(1)), _label_tokens(m.group(2))
    x_tok, y_tok = _label_tokens(x_label), _label_tokens(y_label)
    if not x_tok or not y_tok:
        return None
    swapped = len(x_tok & head) + len(y_tok & tail)
    direct = len(x_tok & tail) + len(y_tok & head)
    if swapped > direct and swapped >= 1:
        return (
            "title says A vs B but printed axes put A on x — labels kept "
            "as printed; human review should confirm orientation"
        )
    return None


def _section_title(page_lines, boxes) -> str:
    for text, wbox in page_lines:
        if boxes and wbox[3] < boxes[0][1] and len(text) < 60 and re.search(
            r"characteristic", text, re.I
        ):
            return text
    return ""


def extract_page(pdf: Path, page_1based: int) -> dict:
    doc = pymupdf.open(pdf)
    try:
        page = doc[page_1based - 1]
        words = _words(page)
        boxes = _frames(page, words)
        page_lines = _page_lines(words)
        plots, skips = [], []
        for fig, box in enumerate(boxes):
            x_row, x_pairs = _ticks_for_axis(words, box, "x")
            y_row, y_pairs = _ticks_for_axis(words, box, "y")
            x_fit = _fit_axis(x_pairs)
            y_fit = _fit_axis(y_pairs)
            if not x_fit or not y_fit:
                skips.append({"figure_index": fig, "reason": "axis_fit_failed"})
                continue

            def to_value(fit, pos):
                kind, a, b, _resid = fit
                v = a * pos + b
                return float(10 ** v) if kind == "log10" else float(v)

            # curves: multi-segment strokes inside the frame
            curves: dict[str, list[tuple[float, float]]] = {}
            swatches: list[tuple[str, float, float]] = []  # color, x1, ycenter
            for d in page.get_drawings():
                r = d["rect"]
                if not _inside(box, r, pad=3.0):
                    continue
                if len(d["items"]) >= _MIN_SEGS:
                    pts = _chain_points(d)
                    if len(pts) >= _MIN_SEGS:
                        curves.setdefault(_color_key(d.get("color")), []).extend(pts)
                elif 1 <= len(d["items"]) <= 3 and 4.0 <= r.width <= 40.0 and r.width >= 3 * r.height:
                    swatches.append(
                        (_color_key(d.get("color")), r.x1, (r.y0 + r.y1) / 2.0)
                    )
            if not curves:
                skips.append({"figure_index": fig, "reason": "no_vector_curves"})
                continue

            # legend binding, in order of strength:
            #   1. swatch -> word run right of the swatch (TI style)
            #   2. POSITIONAL binding (Vishay style): legend text rows sit
            #      INSIDE the frame at their own trace's y position; each
            #      multi-segment colored trace is matched to the legend
            #      row nearest in y at the legend's x position
            legend: dict[str, str] = {}
            for color, sx1, sy in swatches:
                row = [
                    w for w in words
                    if abs(_center(w)[1] - sy) < 5.0
                    and sx1 - 2 < w[0] and _center(w)[0] <= min(sx1 + 90, box[2] - 2)
                ]
                row.sort(key=lambda w: w[0])
                name_words = []
                for w in row:
                    if name_words and w[0] - name_words[-1][2] > 14.0:
                        break
                    name_words.append(w)
                if name_words:
                    legend[color] = " ".join(w[4] for w in name_words)

            legend_rows = [
                row for row in _rows_between(
                    words, box[1] + 2, box[3] - 2, box[0] - 4, box[2] - 2
                )
                if re.search(r"=\s*\d", " ".join(w[4] for w in row))
                and not _NUM_TICK.fullmatch(row[0][4])
            ]
            if len(curves) > 1 and len(legend) < len(curves) \
                    and legend_rows:
                def trace_y_at(points, x):
                    ordered = sorted(points)
                    for a, b in zip(ordered, ordered[1:]):
                        if a[0] <= x <= b[0]:
                            if b[0] == a[0]:
                                return a[1]
                            t = (x - a[0]) / (b[0] - a[0])
                            return a[1] + t * (b[1] - a[1])
                    return None

                pairs = []
                for color, points in curves.items():
                    for row in legend_rows:
                        row_words = sorted(row, key=lambda w: w[0])
                        x_mid = sum(_center(w)[0] for w in row_words) \
                            / len(row_words)
                        y_at = trace_y_at(points, x_mid)
                        if y_at is None:
                            continue
                        y_row = _center(row_words[0])[1]
                        pairs.append((abs(y_at - y_row), color, row))
                pairs.sort(key=lambda p: p[0])
                used_colors, used_rows = set(), set()
                for _dist, color, row in pairs:
                    if color in used_colors or id(row) in used_rows:
                        continue
                    if color in legend:
                        continue
                    used_colors.add(color)
                    used_rows.add(id(row))
                    text = " ".join(
                        w[4] for w in sorted(row, key=lambda w: w[0])
                    )
                    # the legend grammar on these plots is uniformly
                    # "VIN = N V, L = X uH"; a row clipped before its
                    # symbol gets the symbol restored from the grammar
                    if re.match(r"^\s*=", text):
                        text = "VIN " + text
                    legend[color] = text
            names = {}
            for color in curves:
                if len(curves) == 1:
                    names[color] = legend.get(color) or ""
                    continue
                if color not in legend or not legend[color]:
                    skips.append({
                        "figure_index": fig, "reason": "legend_unresolved",
                        "color": color,
                    })
                    names[color] = None
                else:
                    names[color] = legend[color]
            usable = {c: p for c, p in curves.items() if names.get(c) is not None}
            if not usable:
                continue

            # below-frame rows: x label + '='-condition rows, deduped.
            # Legend-grammar rows ("VIN = N V, L = X uH") that sit INSIDE
            # any plot frame are per-trace legends (handled by binding),
            # never plot conditions — sibling-figure legends below a
            # frame must not pollute its conditions.
            _LEGEND_GRAMMAR = re.compile(
                r"=\s*\d+\s*V\b.*\bL\s*=\s*[\d.]+\s*[µμu]?H", re.I
            )

            def _inside_any_frame(row_words):
                cy = sum(_center(w)[1] for w in row_words) / len(row_words)
                cx = sum(_center(w)[0] for w in row_words) / len(row_words)
                return any(
                    b[1] < cy < b[3] and b[0] - 4 < cx < b[2] + 4
                    for b in boxes
                )

            below_rows = _below_frame_rows(words, box, x_row)
            cond_rows = []
            for kind, payload in below_rows:
                if kind != "condition":
                    continue
                for part in _split_condition_row(payload):
                    if _LEGEND_GRAMMAR.search(part):
                        continue
                    if part not in cond_rows:
                        cond_rows.append(part)
            page_conditions = []
            for text, wbox in page_lines:
                if not re.search(
                        r"unless otherwise|ELECTRAL CHARACTERISTICS|"
                        r"ELECTRICAL CHARACTERISTICS|SPECIFIED", text, re.I):
                    continue
                if _LEGEND_GRAMMAR.search(text):
                    continue  # per-trace legend, never a page condition
                page_conditions.append(text)

            caption = ""
            for row in _rows_between(
                words, box[3] + 2.0, box[3] + _TICK_BAND + 60,
                box[0] - 40, box[2] + 40,
            ):
                row_words = sorted(row, key=lambda w: w[0])
                # fine split: a condition text and a caption can share one
                # y-band across columns; runs break at word gaps
                runs: list[list[tuple]] = [[row_words[0]]]
                for w in row_words[1:]:
                    if w[0] - runs[-1][-1][2] > 12.0:
                        runs.append([w])
                    else:
                        runs[-1].append(w)
                for run in runs:
                    text = " ".join(w[4] for w in run)
                    if not re.match(r"^\s*(?:figure|fig\.?)\s+\d", text, re.I):
                        continue
                    rx0 = run[0][0]
                    rx1 = run[-1][2]
                    overlap = min(rx1, box[2]) - max(rx0, box[0])
                    if overlap > 0.3 * (box[2] - box[0]):
                        caption = text
                        break
                if caption:
                    break

            series = []
            raw_total = kept_total = 0
            for color, pts in sorted(usable.items()):
                mapped = []
                for px, py in pts:
                    if not (box[0] - 1 <= px <= box[2] + 1 and box[1] - 1 <= py <= box[3] + 1):
                        continue
                    mapped.append((to_value(x_fit, px), to_value(y_fit, py)))
                raw_total += len(pts)
                kept_total += len(mapped)
                mapped.sort(key=lambda p: p[0])
                dedup = []
                for p in mapped:
                    if not dedup or abs(p[0] - dedup[-1][0]) > 1e-9:
                        dedup.append(p)
                if len(dedup) > _MAX_POINTS:
                    step = len(dedup) / _MAX_POINTS
                    dedup = [dedup[int(i * step)] for i in range(_MAX_POINTS)]
                xs = [p[0] for p in dedup]
                series.append({
                    "name": names[color],
                    "condition": " ".join(cond_rows) or None,
                    "points": [[round(x, 6), round(y, 6)] for x, y in dedup],
                    "_x_range": [round(min(xs), 6), round(max(xs), 6)],
                })
            retained = (kept_total / raw_total) if raw_total else 0.0
            if retained < 0.5:
                skips.append({"figure_index": fig,
                              "reason": "curve_points_outside_frame",
                              "retained": round(retained, 3)})
                continue
            total_pts = sum(len(s["points"]) for s in series)
            out_of_axis = 0
            x_lo, x_hi = min(v for _, v in x_pairs), max(v for _, v in x_pairs)
            y_lo, y_hi = min(v for _, v in y_pairs), max(v for _, v in y_pairs)
            x_slop = max(0.01 * abs(x_hi - x_lo), 1e-6)
            y_slop = max(0.01 * abs(y_hi - y_lo), 1e-6)
            for s in series:
                for x, y in s["points"]:
                    if not (x_lo - x_slop <= x <= x_hi + x_slop
                            and y_lo - y_slop <= y <= y_hi + y_slop):
                        out_of_axis += 1
            outside_pct = (
                100.0 * out_of_axis / total_pts if total_pts else 0.0
            )
            if outside_pct > 10.0:
                skips.append({"figure_index": fig,
                              "reason": "points_outside_axis",
                              "pct": round(outside_pct, 2)})
                continue

            tick_x_min = None
            y_tick_words = [
                w for w in words
                if _NUM_TICK.fullmatch(w[4])
                and box[0] - _TICK_BAND <= _center(w)[0] <= box[0]
                and box[1] - 8 <= _center(w)[1] <= box[3] + 8
            ]
            if y_tick_words:
                tick_x_min = min(w[0] for w in y_tick_words)
            x_label = _axis_label(words, box, "x", below_rows=below_rows)
            y_label = _axis_label(words, box, "y", tick_x_min)
            x_ticks = [v for _, v in x_pairs]
            y_ticks = [v for _, v in y_pairs]
            # numeric quality: tick-fit residuals and point retention ride
            # every reference so error is measurable, not assumed zero
            plots.append({
                "_figure_index": fig,
                "title": caption or None,
                "axes": {
                    "x": {
                        "label": x_label, "unit": _unit_of(x_label),
                        "min": min(x_ticks), "max": max(x_ticks),
                        "scale": x_fit[0],
                    },
                    "y": {
                        "label": y_label, "unit": _unit_of(y_label),
                        "min": min(y_ticks), "max": max(y_ticks),
                        "scale": y_fit[0],
                    },
                },
                "series": series,
                "conditions_plot": cond_rows,
                "conditions_page": page_conditions,
                "_numeric_quality": {
                    "x_fit_residual_pct_of_span": round(x_fit[3] * 100.0, 6),
                    "y_fit_residual_pct_of_span": round(y_fit[3] * 100.0, 6),
                    "retained_point_fraction": round(retained, 4),
                    "points_outside_axis_pct": round(outside_pct, 4),
                },
                "_title_axes_note": _title_axes_note(
                    caption, x_label, y_label
                ),
                "source": "vector-reference extraction (deterministic); "
                          "pending human sign-off per the pilot labeling law",
            })
        return {
            "plots": plots,
            "skips": skips,
            "section_title": _section_title(page_lines, boxes),
        }
    finally:
        doc.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pdf", type=Path, required=True)
    ap.add_argument("--page", type=int, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    sha = hashlib.sha256(args.pdf.read_bytes()).hexdigest()
    result = extract_page(args.pdf, args.page)
    record = {
        "schema": GOLD_SCHEMA,
        "source_artifact": args.pdf.name,
        "document_sha256": sha,
        "page_1based": args.page,
        "section_title": result.get("section_title") or None,
        "plots": result["plots"],
        "skips": result["skips"],
        "source": (
            "Vector reference extraction (deterministic geometry from the "
            "vector PDF): frames/ticks/legends from the page word layer, "
            "curve points from vector paths. Hand-review pending; never "
            "generated from extractor output."
        ),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({
        "out": str(args.out), "plots": len(result["plots"]),
        "series": sum(len(p["series"]) for p in result["plots"]),
        "skips": result["skips"],
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
