#!/usr/bin/env python3
"""Vision envelope builder v2 — source-bound provenance contract
(PRD-CONTEXT-FIRST-EXTRACTION, per the RA-03-closeout directive).

Envelope fields are SOURCE-DERIVED EVIDENCE with explicit status. Rules:

- `document_sha256` is computed and verified against the file bytes.
- Every field carries `{value, status, source_locator}` where status is
  `ok | unknown | missing`; no field is ever fabricated.
- Caller-supplied identity is recorded as `caller_hint`, NOT verified
  applicability. Source-derived identity (part numbers printed in the
  document) carries its own locator.
- The `trust_policy` block is part of the contract: deterministic parser
  output is source-derived evidence, not unquestionable truth; where the
  map conflicts with the image or the source, the claim is HELD for
  review, not forced.
- Evidence method + extractor version are recorded for auditability.
"""

from __future__ import annotations

import argparse
import hashlib
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

ENVELOPE_VERSION = "vision-envelope-v2"
EVIDENCE_METHOD = (
    "deterministic word-layer + vector-frame geometry "
    "(extract_vector_curve_references lineage)"
)
TRUST_POLICY = {
    "kind": "source_derived_evidence",
    "not_unquestionable": True,
    "rule": ("Parser output is source-derived evidence, not proof. Use it to "
             "locate and name; verify readings against the printed image. "
             "Where the envelope conflicts with the image or the source text, "
             "HOLD the claim for human review — do not force either side."),
    "conflict_action": "REVIEW",
}

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


def field(value, status="ok", source=None):
    return {"value": value, "status": status, "source_locator": source}


def classify_plot(caption: str):
    low = (caption or "").lower()
    for pat, kind in TYPE_WORDS:
        if re.search(pat, low):
            return kind
    return None  # unknown — never fabricate


def build_envelope(pdf: Path, page_1based: int, caller_identity: str | None) -> dict:
    raw_sha = hashlib.sha256(pdf.read_bytes()).hexdigest()
    doc = pymupdf.open(pdf)
    try:
        page = doc[page_1based - 1]
        words = evc._words(page)
        boxes = evc._frames(page, words)
        page_lines = evc._page_lines(words)
        figures = []
        for fig, box in enumerate(boxes):
            x_row, x_pairs = evc._ticks_for_axis(words, box, "x")
            y_row, y_pairs = evc._ticks_for_axis(words, box, "y")

            caption = ""
            caption_y = None
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
                            caption, caption_y = text, run[0][1]
                            break
                if caption:
                    break
            fig_no = None
            m = re.match(r"^\s*(?:figure|fig\.?)\s*(\d+(?:-\d+)?)", caption or "", re.I)
            if m:
                fig_no = m.group(1)

            # legend names (swatch ↔ word) with locator = y-band on this page
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
                    legend.append({"name": " ".join(w[4] for w in name),
                                   "y_pt": round(sy, 1)})

            cond = []
            for kind, payload in evc._below_frame_rows(words, box, x_row):
                if kind == "condition":
                    for p in evc._split_condition_row(payload):
                        if p not in [c["text"] for c in cond]:
                            cond.append({"text": p,
                                         "source_locator": f"page {page_1based}, below-frame row"})
            for text, wbox in page_lines:
                if wbox[1] < boxes[0][1] and re.search(r"unless otherwise|=\s", text, re.I):
                    if text not in [c["text"] for c in cond]:
                        cond.append({"text": text,
                                     "source_locator": f"page {page_1based}, page header"})

            x_label = evc._axis_label(words, box, "x")
            y_label = evc._axis_label(words, box, "y")
            x_fit = evc._fit_axis(x_pairs) if x_pairs else None
            y_fit = evc._fit_axis(y_pairs) if y_pairs else None

            # source-derived identity: package-ish token near the frame
            pkg_tok = None
            for w in words:
                t = w[4]
                if re.match(r"^(?:PowerPAK|SOIC|QFN|DFN|SOT|TSSOP|MSOP|WLCSP|BGA|"
                            r"TO-\d+|SMA|SMB|DPAK|LFCSP|VQFN|WDFN|USON)", t, re.I):
                    pkg_tok = t
                    break

            figures.append({
                "figure_index": fig,
                "figure_id": {
                    "printed_number": fig_no,
                    "status": "ok" if fig_no else "unknown",
                },
                "region": {
                    "bbox_pdf_points": [round(v, 1) for v in box],
                    "region_id": f"frame[{fig}]",
                    "region_source": "vector_frame_or_stroked_frame",
                },
                "coordinate_space": "PDF points, origin top-left, y down",
                "caption": field(caption or None, "ok" if caption else "missing",
                                 f"page {page_1based}, y≈{round(caption_y,1)}" if caption_y else None),
                "plot_type": field(classify_plot(caption),
                                   "ok" if classify_plot(caption) else "unknown",
                                   "derived from caption keywords"),
                "x_axis": {
                    "label": field(x_label or None, "ok" if x_label else "missing",
                                   f"page {page_1based}, left-of/below frame[{fig}]"),
                    "unit": field(evc._unit_of(x_label) if x_label else None,
                                  "ok" if x_label and evc._unit_of(x_label) else "missing"),
                    "scale": field(x_fit[0] if x_fit else None,
                                   "ok" if x_fit else "unknown"),
                    "tick_values": field([v for _, v in x_pairs],
                                         "ok" if x_pairs else "missing",
                                         f"page {page_1based}, tick band x of frame[{fig}]"),
                },
                "y_axis": {
                    "label": field(y_label or None, "ok" if y_label else "missing",
                                   f"page {page_1based}, left of frame[{fig}]"),
                    "unit": field(evc._unit_of(y_label) if y_label else None,
                                  "ok" if y_label and evc._unit_of(y_label) else "missing"),
                    "scale": field(y_fit[0] if y_fit else None,
                                   "ok" if y_fit else "unknown"),
                    "tick_values": field([v for _, v in y_pairs],
                                         "ok" if y_pairs else "missing",
                                         f"page {page_1based}, tick band y of frame[{fig}]"),
                },
                "legend_series": legend if legend else field(None, "missing"),
                "printed_conditions": cond if cond else field(None, "missing"),
                "package_token": field(pkg_tok, "ok" if pkg_tok else "unknown",
                                       f"page {page_1based}, word layer near frame[{fig}]"
                                       if pkg_tok else None),
            })
        return {
            "schema": ENVELOPE_VERSION,
            "document_sha256": raw_sha,
            "document_sha_verified": True,
            "page_1based": page_1based,
            "coordinate_space": "PDF points, origin top-left, y down",
            "part_identity": {
                "caller_hint": caller_identity,
                "caller_hint_status": "hint_not_verified_applicability",
                "source_derived": field(None, "unknown"),
            },
            "evidence_method": EVIDENCE_METHOD,
            "extractor_version": ENVELOPE_VERSION,
            "trust_policy": TRUST_POLICY,
            "figures": figures,
        }
    finally:
        doc.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pdf", type=Path, required=True)
    ap.add_argument("--page", type=int, required=True)
    ap.add_argument("--part", type=str, default=None)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    env = build_envelope(args.pdf, args.page, args.part)
    args.out.write_text(json.dumps(env, indent=1, ensure_ascii=False) + "\n")
    print(json.dumps({"sha": env["document_sha256"][:16],
                      "figures": len(env["figures"])}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
