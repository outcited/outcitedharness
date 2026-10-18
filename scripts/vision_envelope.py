#!/usr/bin/env python3
"""Vision envelope builder (PRD-CONTEXT-FIRST-EXTRACTION, test harness).

For a given PDF page + figure, emit the FREE context envelope a vision
call should carry — everything the deterministic stack already knows
before any model runs:

- caption + figure id (title words below the frame)
- plot type (caption-keyword classification)
- axis labels + units + tick values + scale (word layer + frame geometry)
- legend/series names (swatch-word binding, vector or text layer)
- printed conditions verbatim (page header + in-plot '=' rows)
- part identity (caller-supplied)

Built from extract_vector_curve_references' page analysis (the same
word/frame machinery), minus the digitized points: for a raster plot we
would not have those either. Everything here is text-layer/geometry —
true for both vector and raster sources.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "evc", str(ROOT / "scripts/extract_vector_curve_references.py"))
evc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evc)

import pymupdf  # noqa: E402

TYPE_WORDS = [
    (r"efficiency", "efficiency_vs_load"),
    (r"power\s+dissipation", "power_dissipation"),
    (r"thermal\s+(impedance|resistance)", "thermal"),
    (r"bode|gain\s+and\s+phase", "bode"),
    (r"switching\s+frequency", "switching_frequency"),
    (r"on[- ]?resistance|rds", "rds_on"),
    (r"output\s+current|load\s+current", "output_characteristic"),
    (r"pinout|pin\s+assignment|pin\s+diagram|ball", "pinout"),
    (r"package|dimension", "package_mechanical"),
    (r"typical application|schematic", "application_schematic"),
]


def classify_plot(caption: str) -> str:
    low = (caption or "").lower()
    for pat, kind in TYPE_WORDS:
        if re.search(pat, low):
            return kind
    return "unknown"


def build_envelope(pdf: Path, page_1based: int, part: str) -> dict:
    doc = pymupdf.open(pdf)
    try:
        page = doc[page_1based - 1]
        words = evc._words(page)
        boxes = evc._frames(page, words)
        page_lines = evc._page_lines(words)
        envelopes = []
        for fig, box in enumerate(boxes):
            x_row, x_pairs = evc._ticks_for_axis(words, box, "x")
            y_row, y_pairs = evc._ticks_for_axis(words, box, "y")
            # caption: same rule as the extractor (figure run below frame)
            caption = ""
            for row in evc._rows_between(
                    words, box[3] + 2.0, box[3] + evc._TICK_BAND + 60,
                    box[0] - 40, box[2] + 40):
                row_words = sorted(row, key=lambda w: w[0])
                runs = [[row_words[0]]]
                for w in row_words[1:]:
                    (runs[-1].append(w) if w[0] - runs[-1][-1][2] <= 12.0
                     else runs.append([w]))
                for run in runs:
                    text = " ".join(w[4] for w in run)
                    if re.match(r"^\s*(?:figure|fig\.?)\s+\d", text, re.I):
                        overlap = (min(run[-1][2], box[2]) - max(run[0][0], box[0]))
                        if overlap > 0.3 * (box[2] - box[0]):
                            caption = text
                            break
                if caption:
                    break
            # legends: swatch-word pairs (vector) + '=' condition rows
            drawings = page.get_drawings()
            swatches = [d for d in drawings
                        if 1 <= len(d["items"]) <= 3
                        and 4.0 <= d["rect"].width <= 40.0
                        and d["rect"].width >= 3 * d["rect"].height
                        and evc._inside(box, d["rect"], pad=3.0)]
            legend = []
            for d in swatches:
                sy = (d["rect"].y0 + d["rect"].y1) / 2.0
                row = [w for w in words
                       if abs(evc._center(w)[1] - sy) < 5.0
                       and d["rect"].x1 - 2 < w[0]
                       and evc._center(w)[0] <= min(d["rect"].x1 + 90, box[2] - 2)]
                row.sort(key=lambda w: w[0])
                name = []
                for w in row:
                    if name and w[0] - name[-1][2] > 14.0:
                        break
                    name.append(w)
                if name:
                    legend.append(" ".join(w[4] for w in name))
            cond = []
            for kind, payload in evc._below_frame_rows(words, box, x_row):
                if kind == "condition":
                    for p in evc._split_condition_row(payload):
                        if p not in cond:
                            cond.append(p)
            for text, wbox in page_lines:
                if wbox[1] < boxes[0][1] and re.search(r"unless otherwise|=\s", text, re.I):
                    cond.append(text)
            x_label = evc._axis_label(words, box, "x")
            y_label = evc._axis_label(words, box, "y")
            envelopes.append({
                "figure_index": fig,
                "caption": caption or None,
                "plot_type": classify_plot(caption),
                "x_axis": {
                    "label": x_label,
                    "unit": evc._unit_of(x_label),
                    "scale": (evc._fit_axis(x_pairs) or (None,))[0] if x_pairs else None,
                    "tick_values": [v for _, v in x_pairs],
                },
                "y_axis": {
                    "label": y_label,
                    "unit": evc._unit_of(y_label),
                    "scale": (evc._fit_axis(y_pairs) or (None,))[0] if y_pairs else None,
                    "tick_values": [v for _, v in y_pairs],
                },
                "legend_series": legend,
                "printed_conditions": cond,
                "bbox": [round(v, 1) for v in box],
            })
        return {
            "schema": "vision-envelope.v1",
            "document_sha256": evc.__dict__.get("sha256_hex", lambda b: None)(
                pdf.read_bytes()) if False else None,
            "page_1based": page_1based,
            "part": part,
            "figures": envelopes,
        }
    finally:
        doc.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pdf", type=Path, required=True)
    ap.add_argument("--page", type=int, required=True)
    ap.add_argument("--part", type=str, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    env = build_envelope(args.pdf, args.page, args.part)
    args.out.write_text(json.dumps(env, indent=1, ensure_ascii=False) + "\n")
    print(json.dumps(env, ensure_ascii=False)[:400])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
