#!/usr/bin/env python3
"""CURVE-05A Workstream 2 — cold-review packet emission.

Cold review = the reviewer transcribes the figure from the original
render and the printed conditions BEFORE seeing the extraction. Each
packet file orders content accordingly: render reference + blind
transcription checklist first, the model's extraction in a clearly marked
SEALED section after, plus the packet hash for the ledger. Where no human
reviewer is available the packets stay pending — never simulated.

Coverage after this emission: 40 vector plots (TI, Vishay) + 2 Infineon
raster plots (TLS4125D0EPV, recovered by the CURVE-04 raster lane with
text-layer conditions attached here) = 42 independent power plots,
12 device families, 3 manufacturers — subject to held documents.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pymupdf

from harness.discovery.curves import load_reference_curves
from harness.electronics.raster_curves import extract_raster_page

PILOT = ROOT / "tests/fixtures/gold/curve_evidence_pilot"
OUT = ROOT / "results/curve-05a/cold-review"
TLS = Path(
    "/Volumes/M5_4TB/exports/corpus-deliveries/datasheet-tail-20261007/"
    "blobs/14/cc/14cc7b1af8ba7af289f7bf24d7fda16449a743f3eb611c2502899e"
    "36f32e7478.pdf"
)

BLIND_CHECKLIST = [
    "source_document_and_page",
    "figure_and_caption",
    "x_axis_quantity_unit_scale",
    "y_axis_quantity_unit_scale",
    "series_and_legend_mapping",
    "operating_conditions_printed",
    "sampled_coordinates_at identifiable_features",
    "applicability_part_family",
    "uncertainty_estimate",
]


def _render_page(pdf: Path, page: int, dpi: int, out: Path) -> str:
    doc = pymupdf.open(pdf)
    doc[page - 1].get_pixmap(
        matrix=pymupdf.Matrix(dpi / 72.0, dpi / 72.0)
    ).save(str(out))
    doc.close()
    return str(out)


def emit_vector_packets() -> int:
    count = 0
    for path in sorted(PILOT.glob("*.json")):
        if path.name.startswith("_"):
            continue
        record = json.loads(path.read_text())
        pdf = None
        sha = record.get("document_sha256")
        blob = (ROOT.parent / "Harnessv1").name  # not used; resolve below
        candidates = [
            Path("/Volumes/M5_4TB/data/ti_power_datasheets")
            / str(record.get("source_artifact") or ""),
            Path("/Volumes/M5_4TB/exports/corpus-deliveries/"
                 "datasheet-tail-20261007/blobs") / (sha or "")[:2]
            / (sha or "")[2:4] / f"{sha}.pdf",
        ]
        for candidate in candidates:
            if candidate.exists():
                pdf = candidate
                break
        render = None
        if pdf is not None:
            render = _render_page(
                pdf, int(record.get("page_1based") or 1), 200,
                OUT / "renders" / f"{path.stem}.png",
            )
        packet_dir = OUT / path.stem
        packet_dir.mkdir(parents=True, exist_ok=True)
        curves = load_reference_curves([path])
        for curve in curves:
            sealed = {
                "document_sha256": curve.document_sha256,
                "page_1based": curve.page_1based,
                "figure_index": curve.figure_index,
                "series_index": curve.series_index,
                "caption": curve.caption,
                "axes": curve.axes,
                "series_name": curve.series.get("name"),
                "points_sample": curve.points[:5],
                "conditions": curve.conditions,
                "applicability": curve.applies_to,
                "evidence_class": curve.evidence_class,
                "verification": curve.verification,
            }
            packet = {
                "cold_review": {
                    "instructions": "Transcribe every checklist item from "
                                    "the render and printed conditions "
                                    "BEFORE opening the sealed answer.",
                    "render": {
                        "path": render,
                        "sha256": hashlib.sha256(
                            Path(render).read_bytes()
                        ).hexdigest() if render else None,
                    } if render else None,
                    "printed_conditions_verbatim": curve.conditions_verbatim,
                    "blind_checklist": BLIND_CHECKLIST,
                    "reviewer_transcription": None,
                },
                "sealed_extraction": sealed,
                "adjudication": {
                    "state": "human_review_pending",
                    "decision": None,
                    "reviewer": None,
                    "note": "machine output; approval only via the "
                            "adjudication ledger",
                },
            }
            name = f"fig{curve.figure_index}-s{curve.series_index}.json"
            (packet_dir / name).write_text(
                json.dumps(packet, indent=2, ensure_ascii=False,
                           default=str) + "\n"
            )
            count += 1
    return count


def emit_tls_raster_records() -> dict:
    """Infineon coverage from the held raster challenge: recovered plots
    with text-layer conditions attached (typed where the alias layer
    knows the symbol; verbatim otherwise)."""

    if not TLS.exists():
        return {"status": "document_not_held", "plots": 0}
    record = extract_raster_page(TLS, 18, dpi=300,
                                 render_dir=OUT / "renders")
    conditions_page = [
        line.strip()
        for line in record.get("page_text_excerpt", "").splitlines()
        if "=" in line or "FREQ" in line
    ][:4]
    out = {
        "status": "partial_raster_recovery",
        "document_sha256": record["document_sha256"],
        "page_1based": 18,
        "manufacturer": "infineon",
        "device_family": "TLS4125D0EPV50",
        "conditions_page_verbatim": conditions_page,
        "plots": [],
        "note": "raster lane output; series names unbound (legend OCR "
                "pending); machine reference only",
    }
    packet_dir = OUT / "tls4125d0epv_p18"
    packet_dir.mkdir(parents=True, exist_ok=True)
    for plot in record["plots"]:
        out["plots"].append({
            "figure_index": plot["_figure_index"],
            "bbox_page_px": plot["_bbox_page_px"],
            "series_count": len(plot["series"]),
            "raster_quality": plot["raster_quality"],
        })
    (packet_dir / "raster_record.json").write_text(
        json.dumps(record, indent=2, ensure_ascii=False, default=str)
        + "\n"
    )
    return out


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "renders").mkdir(parents=True, exist_ok=True)
    vector = emit_vector_packets()
    tls = emit_tls_raster_records()
    summary = {
        "schema": "harness.electronics-cold-review-summary.v1",
        "vector_series_packets": vector,
        "infineon_raster": tls,
        "coverage": {
            "independent_power_plots": 40 + len(tls.get("plots") or []),
            "device_families": 12,
            "manufacturers": ["texas_instruments", "vishay", "infineon"],
            "independent_review_status": "pending — no simulated approval",
        },
        "laws": [
            "reviewer sees render + printed conditions before the "
            "sealed extraction",
            "approval only through the adjudication ledger by a named "
            "reviewer",
        ],
    }
    (OUT / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, default=str)
        + "\n"
    )
    print(json.dumps({
        "vector_series_packets": vector,
        "tls_plots": len(tls.get("plots") or []),
        "coverage": summary["coverage"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
