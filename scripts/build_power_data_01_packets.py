#!/usr/bin/env python3
"""POWER-DATA-01 packet builder — assembles the two task deliverables from
the frozen vector-reference extractions:

1. pilot fixture golds for TPS54360 p35 and LM5005 p25 (schema
   harness.electronics-typical-curves-gold.v1, consumed unchanged by
   harness.discovery.curves.load_reference_curves);
2. bundle-v3-shaped curve rows (harness.m4-curve-evidence-bundle.v1) for the
   two 48 V efficiency series m4opencode's decision engine asked for;
3. the Vishay SiC46x admission packet for cr-core (preparation only —
   admission and release authority stay with the catalog owner).

Every condition key carries a verbatim page quote. No model calls anywhere:
inputs are extract_vector_curve_references.py outputs plus text-layer quotes
verified by hand against the held documents this session.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "scratch/power-data-01/extracted"
OUT = ROOT / "results/power-data-01"
PILOT = ROOT / "tests/fixtures/gold/curve_evidence_power_data_01"
PDFS = ROOT / "scratch/power-data-01/pdfs"

REVIEW_CHECKLIST = [
    "axis_labels_verbatim",
    "axis_units_and_scale",
    "series_names_match_legend",
    "sampled_points_on_trace",
    "conditions_verbatim",
    "applicability_part_family",
    "orientation_note_resolved",
]


def emit_review_packets(fixture_name: str, pdf_name: str) -> None:
    """Same packet shape as scripts/emit_curve_review_packets.py (render +
    reference + checklist + unsigned signoff row); PDFs resolve from the
    local vault CAS copies, not the NAS delivery roots."""

    record = json.loads((PILOT / fixture_name).read_text())
    out_dir = OUT / "curve-review" / Path(fixture_name).stem
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open(PDFS / pdf_name)
    try:
        page = doc[int(record.get("page_1based") or 1) - 1]
        page.get_pixmap(
            matrix=pymupdf.Matrix(300 / 72.0, 300 / 72.0)
        ).save(str(out_dir / "page.png"))
    finally:
        doc.close()
    (out_dir / "reference.json").write_text(
        json.dumps(record, indent=2, ensure_ascii=False) + "\n"
    )
    (out_dir / "checklist.json").write_text(
        json.dumps(
            {
                "document_sha256": record.get("document_sha256"),
                "page_1based": record.get("page_1based"),
                "items": REVIEW_CHECKLIST,
                "instructions": (
                    "Compare reference.json against page.png at each "
                    "checklist item. Mark true when verified; add "
                    "corrections as notes. Unsigned packets stay machine "
                    "references — never human gold."
                ),
            },
            indent=2,
        )
        + "\n"
    )

TPS54360_SHA = (
    "c2743e51e9f180e3efd7afb2ce1ac792906576207f4e318f9b6075f3ab5c56a8"
)
LM5005_SHA = (
    "7999d4c9d51f51d7643523b16770c33928404d0e1eb6447bf43c9a4b3e9b58fe"
)
SIC46X_SHA = (
    "0cef2db3d1a1b022d13de380a3038cf9f3a2c5155bc12cbd44daae708eaba175"
)


def canonical(value) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()


def evidence_id(row_core: dict) -> str:
    return "curve-" + hashlib.sha256(canonical(row_core)).hexdigest()


def load(name: str) -> dict:
    return json.loads((SRC / name).read_text())


def make_fixture(name: str, *, part: str, conditions_plot: list[str]) -> dict:
    record = load(name)
    record["manufacturer"] = "texas_instruments"
    record["family_group"] = part
    record["part"] = part
    for plot in record.get("plots", []):
        plot.setdefault("conditions_plot", [])
        for cond in conditions_plot:
            if cond not in plot["conditions_plot"]:
                plot["conditions_plot"].append(cond)
    return record


def bundle_row(
    *,
    part: str,
    doc_sha: str,
    page_1based: int,
    figure_index: int,
    caption: str,
    series_name: str,
    x_kind: str,
    x_label: str,
    x_unit: str,
    x_scale: str,
    condition_keys: dict,
    condition_verbatim: list[str],
    extraction_release: str,
    anchor: dict,
    numeric_quality: dict,
    supported_region: dict,
    points: list,
    missing_dimensions: list[str] | None = None,
    notes: list[str] | None = None,
) -> dict:
    row_core = {
        "part": part,
        "document_sha256": doc_sha,
        "page_1based": page_1based,
        "figure_index": figure_index,
        "series": series_name,
        "phenomenon": "efficiency_vs_load",
        "conditions": condition_keys,
    }
    return {
        "schema": "harness.m4-curve-evidence-bundle.v1",
        "evidence_id": evidence_id(row_core),
        "advisory_only": True,
        "source": {
            "document_sha256": doc_sha,
            "document_revision": None,
            "page_1based": page_1based,
            "figure_index": figure_index,
            "caption": caption,
            "locator": {
                "page_1based": page_1based,
                "figure_index": figure_index,
                "series_index": 0,
                "region_bbox": None,
            },
            "source_artifact": f"{doc_sha}.pdf",
            "manufacturer": "texas_instruments",
        },
        "applicability": {
            "family": part,
            "family_group": part,
            "part": part,
            "manufacturer": "texas_instruments",
            "category": "power.dcdc",
            "coverage_kind": "primary",
        },
        "phenomenon": "efficiency_vs_load",
        "quantity": {
            "x": {
                "kind": x_kind,
                "label": x_label,
                "unit": x_unit,
                "scale": x_scale,
            },
            "y": {"kind": "efficiency", "label": "Efficiency (%)", "unit": "%"},
        },
        "conditions": {
            "keys": condition_keys,
            "verbatim": condition_verbatim,
        },
        "missing_dimensions": missing_dimensions or [],
        "supported_region": supported_region,
        "evidence_class": "typical",
        "guarantee": False,
        "adjudication": {
            "state": "human_review_pending",
            "packet_hash": None,
            "note": "machine-verified only",
        },
        "binding": {
            "method": "positional_legend",
            "trace_source": "vector_multiseg",
            "physics_check": None,
        },
        "operating_point_query": {
            "supported": True,
            "method": "linear_interpolation_on_digitized_polyline"
            if x_scale == "linear"
            else "log10_interpolation_on_digitized_polyline",
            "bounds": "strictly inside sampled support; extrapolation refused",
        },
        "uncertainty": {
            "method": "vector_reference",
            "point_error_pct": None,
            "note": "deterministic geometry from the vector PDF; axis map "
            + f"{'gridline-anchored' if 'gridlines' in set(anchor.values()) else 'word-center'}"
            + " (residuals recorded per axis); human sign-off pending",
            "numeric_quality": numeric_quality,
        },
        "comparability": {
            "verdict": "condition_aware",
            "rules": "comparable only when stated conditions agree; missing "
            "never defaulted; typical never guaranteed",
        },
        "release_ids": {
            "extraction": extraction_release,
            "retrieval": "curve-retrieval-v0-experimental",
            "bundle": "power-data-01-rows-v1",
        },
        "curve": {
            "series_name": series_name,
            "points": points,
            "original_plot_reference": caption,
        },
        "_notes": notes or [],
    }


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)

    # ---- 1. pilot fixture golds -------------------------------------------
    tps = make_fixture(
        "tps54360_p35.json",
        part="TPS54360",
        conditions_plot=[
            "VOUT = 5V, fsw = 600 kHz",
        ],
    )
    lm = make_fixture(
        "lm5005_p25.json",
        part="LM5005",
        conditions_plot=[
            "Unless indicated otherwise, all waveforms are taken at "
            "VIN = 48V.",
            "LM5005 Buck Regulator Bill of Materials, VOUT = 5V, "
            "IOUT = 2.5A",
        ],
    )
    (PILOT / "tps54360_p35.json").write_text(
        json.dumps(tps, indent=1, ensure_ascii=False) + "\n"
    )
    (PILOT / "lm5005_p25.json").write_text(
        json.dumps(lm, indent=1, ensure_ascii=False) + "\n"
    )
    emit_review_packets("tps54360_p35.json", "TPS54360.pdf")
    emit_review_packets("lm5005_p25.json", "LM5005.pdf")

    # ---- 2. bundle-v3 rows for the two 48 V series ------------------------
    def series_points(record, fig_index, name):
        for plot in record["plots"]:
            if plot["_figure_index"] == fig_index:
                for s in plot["series"]:
                    if s["name"] == name:
                        return s["points"]
        raise SystemExit(f"series {name!r} not found on figure {fig_index}")

    tps_pts = series_points(tps, 4, "48Vin")
    tps_row = bundle_row(
        part="TPS54360",
        doc_sha=TPS54360_SHA,
        page_1based=35,
        figure_index=4,
        caption="Figure 45. Efficiency vs Load Current",
        series_name="48Vin",
        x_kind="load_current",
        x_label="Output Current (A)",
        x_unit="A",
        x_scale="linear",
        condition_keys={
            "vout_v": 5.0,
            "vin_v": 48.0,
            "f_hz": 600000.0,
        },
        condition_verbatim=[
            "VOUT = 5V, fsw = 600 kHz",
            "48Vin",
        ],
        extraction_release="vector-reference-v2+gridline-anchor",
        anchor={"x": "gridlines", "y": "gridlines"},
        numeric_quality=next(
            p["_numeric_quality"]
            for p in tps["plots"]
            if p["_figure_index"] == 4
        ),
        supported_region={
            "min": min(p[0] for p in tps_pts),
            "max": max(p[0] for p in tps_pts),
        },
        points=tps_pts,
        missing_dimensions=[],
        notes=[
            "l_h not keyed: the figure prints no inductor value "
            "(internally compensated COT part); never defaulted",
            "VOUT/fsw from the printed plot-level condition row; VIN from "
            "the series legend (48Vin)",
        ],
    )

    lm_pts = series_points(lm, 0, "VIN = 48 V")
    lm_row = bundle_row(
        part="LM5005",
        doc_sha=LM5005_SHA,
        page_1based=25,
        figure_index=0,
        caption=(
            "Figure 7-9. Typical Efficiency vs Input Voltage and Output "
            "Current, 5V Output"
        ),
        series_name="VIN = 48 V",
        x_kind="load_current",
        x_label="Output Current (A)",
        x_unit="A",
        x_scale="linear",
        condition_keys={
            "vout_v": 5.0,
            "vin_v": 48.0,
            "f_hz": 300000.0,
            "l_h": 3.3e-05,
        },
        condition_verbatim=[
            "VIN = 48 V",
            "VOUT = 5V",
            "FSW = 300kHz",
            "INDUCTOR, 33 µH, ISAT 6.22A, DCR 60mΩ",
            "LM5005 Buck Regulator Bill of Materials, VOUT = 5V, IOUT = 2.5A",
            "Unless indicated otherwise, all waveforms are taken at "
            "VIN = 48V.",
        ],
        extraction_release="vector-reference-v2",
        anchor={"x": "word_centers", "y": "word_centers"},
        numeric_quality=next(
            p["_numeric_quality"] for p in lm["plots"] if p["_figure_index"] == 0
        ),
        supported_region={
            "min": min(p[0] for p in lm_pts),
            "max": max(p[0] for p in lm_pts),
        },
        points=lm_pts,
        missing_dimensions=[],
        notes=[
            "f_hz and l_h keyed from the application-curve design example "
            "the figure belongs to (p18 'FSW = 300kHz'; Table 7-1 LF row "
            "'INDUCTOR, 33 µH') — application curves, not a family-typical "
            "condition; comparability requires matching the same design "
            "example context",
        ],
    )

    rows_doc = {
        "schema": "harness.m4-curve-evidence-bundle.v1",
        "produced_by": "scripts/build_power_data_01_packets.py",
        "task": "POWER-DATA-01 Ask 2 (m4opencode findings 15853d5 §3)",
        "rows": [tps_row, lm_row],
        "operating_point_probe": {},
    }
    for row in (tps_row, lm_row):
        pts = sorted(row["curve"]["points"])
        x0, y0 = pts[0]
        best = None
        for (xa, ya), (xb, yb) in zip(pts, pts[1:]):
            if xa <= 1.5 <= xb:
                y = ya + (yb - ya) * (1.5 - xa) / (xb - xa)
                best = round(y, 3)
                break
        rows_doc["operating_point_probe"][row["applicability"]["part"]] = {
            "at_iout_a": 1.5,
            "efficiency_pct": best,
        }

    (OUT / "ti-efficiency-rows-v1.json").write_text(
        json.dumps(rows_doc, indent=1, ensure_ascii=False) + "\n"
    )

    # ---- 3. SiC46x admission packet (preparation only) --------------------
    packet = {
        "schema": "designwins.sic46x-admission-packet.v1",
        "produced_by": "scripts/build_power_data_01_packets.py",
        "task": "POWER-DATA-01 Ask 1 (m4opencode findings 15853d5 §3)",
        "authority_note": (
            "PREPARATION ONLY. Admission, binding write and release are "
            "cr-core/cursor-cr authority per the four-gate contract "
            "(docs/api/designwins-ingest-to-browsable.md)."
        ),
        "document": {
            "sha256": SIC46X_SHA,
            "vault_path": f"/Volumes/M5_4TB/vault/cas/0c/{SIC46X_SHA}.pdf",
            "bytes": 1205324,
            "pages": 29,
            "vendor": "vishay.com",
            "title_page": (
                "SiC461, SiC462, SiC463, SiC464 — Vishay Siliconix, "
                "Document Number: 65124, S25-1437-Rev. S, 17-Nov-2025"
            ),
            "mpns_served": [],
            "binding_write_owed": True,
        },
        "parts": [
            {
                "part": "SiC461",
                "record_id": "part:vishay.com:SiC461",
                "rated_iout_a": 10.0,
                "ec_header_quote": (
                    "ELECTRICAL CHARACTERISTICS (VIN = 48 V, VOUT = 5 V, "
                    "fsw = 300 kHz, SiC461 (10 A), unless otherwise noted)"
                ),
                "ec_header_page_1based": 14,
                "orderable_mpns": ["SiC461ED-T1-GE3"],
                "package": "PowerPAK MLP55-27L",
                "ordering_page_1based": 3,
            },
            {
                "part": "SiC462",
                "record_id": "part:vishay.com:SiC462",
                "rated_iout_a": 6.0,
                "ec_header_quote": (
                    "ELECTRICAL CHARACTERISTICS (VIN = 48 V, VOUT = 5 V, "
                    "fsw = 300 kHz, SiC462 (6 A), unless otherwise noted)"
                ),
                "ec_header_page_1based": 15,
                "orderable_mpns": ["SiC462ED-T1-GE3"],
                "package": "PowerPAK MLP55-27L",
                "ordering_page_1based": 3,
            },
            {
                "part": "SiC463",
                "record_id": "part:vishay.com:SiC463",
                "rated_iout_a": 4.0,
                "ec_header_quote": (
                    "ELECTRICAL CHARACTERISTICS (VIN = 48 V, VOUT = 5 V, "
                    "fsw = 300 kHz, SiC463 (4 A), unless otherwise noted)"
                ),
                "ec_header_page_1based": 16,
                "orderable_mpns": ["SiC463ED-T1-GE3"],
                "package": "PowerPAK MLP55-27L",
                "ordering_page_1based": 3,
            },
            {
                "part": "SiC464",
                "record_id": "part:vishay.com:SiC464",
                "rated_iout_a": 2.0,
                "ec_header_quote": (
                    "ELECTRICAL CHARACTERISTICS (VIN = 48 V, VOUT = 5 V, "
                    "fsw = 300 kHz, SiC464 (2 A), unless otherwise noted)"
                ),
                "ec_header_page_1based": 17,
                "orderable_mpns": ["SiC464ED-T1-GE3"],
                "package": "PowerPAK MLP55-27L",
                "ordering_page_1based": 3,
            },
        ],
        "family_grain": {
            "family_group": "SiC46x",
            "quote": (
                "SiC46x is a high efficiency synchronous buck regulator "
                "family capable of delivering up to 10 A continuous current."
            ),
            "page_1based": 6,
            "note": (
                "4 base devices from one family datasheet; orderable MPNs "
                "record as asserted_members (Gate 2 grain per the ingest "
                "contract)"
            ),
        },
        "gate1_binding_spellings": {
            "note": (
                "vishay verbatim field spellings observed in this document "
                "for the power-stack atoms — aliases are per-vendor"
            ),
            "verbatim_rows": [
                {
                    "page_1based": 4,
                    "section": "RECOMMENDED OPERATING CONDITIONS",
                    "rows": [
                        {
                            "parameter": "Input voltage (VIN)",
                            "min": 4.5,
                            "typ": None,
                            "max": 60.0,
                            "unit": "V",
                        },
                        {
                            "parameter": "Control input voltage (VCIN)",
                            "min": 4.5,
                            "typ": None,
                            "max": 60.0,
                            "unit": "V",
                        },
                    ],
                },
                {
                    "page_1based": 3,
                    "section": "ABSOLUTE MAXIMUM RATINGS",
                    "rows": [
                        {
                            "parameter": "VIN (Reference to PGND)",
                            "limits": "-0.3 to 66",
                            "unit": "V",
                        },
                        {
                            "parameter": "TJ Junction temperature",
                            "limits": "-40 to +150",
                            "unit": "°C",
                        },
                    ],
                },
                {
                    "page_1based": 5,
                    "section": "Fault Protections (per-part valley limits)",
                    "rows": [
                        {
                            "parameter": "IOCP SiC461 (10 A)",
                            "conditions": "RILIM = 60 kΩ, TJ = -10 °C to "
                            "+125 °C",
                            "min": 10.4,
                            "typ": 13.0,
                            "max": 15.6,
                            "unit": "A",
                        },
                        {
                            "parameter": "IOCP SiC462 (6 A)",
                            "conditions": "RILIM = 60 kΩ, TJ = -10 °C to "
                            "+125 °C",
                            "min": 6.4,
                            "typ": 8.0,
                            "max": 9.6,
                            "unit": "A",
                        },
                        {
                            "parameter": "IOCP SiC463 (4 A)",
                            "conditions": "RILIM = 40 kΩ, TJ = -10 °C to "
                            "+125 °C",
                            "min": 4.8,
                            "typ": 6.0,
                            "max": 7.2,
                            "unit": "A",
                        },
                    ],
                },
            ],
        },
        "gate3_placement": {
            "question": (
                "which hierarchy source supplies the power-aisle family edge "
                "for vishay SiC46x — awaiting cr-core answer (mail "
                "power-data-01-sic46x-admission-requirements-20261014)"
            ),
            "proposed": (
                "new family node SiC46x under vishay in the power aisle; "
                "the curve evidence already carries family_group SiC46x "
                "(bundle v3 rows)"
            ),
        },
        "mpns_served_payload_proposed": [
            "SiC461ED-T1-GE3",
            "SiC462ED-T1-GE3",
            "SiC463ED-T1-GE3",
            "SiC464ED-T1-GE3",
        ],
        "cross_references": {
            "decision_evidence": (
                "docs/audits/qa/2026-10-14/power-decision-08c/"
                "POWER_DECISION_08C_FINDINGS.md at outcited.com 15853d5"
            ),
            "device_ratings_fixture": (
                "tests/fixtures/gold/curve_evidence_pilot/_device_ratings.json"
                " (curve/08c lane — 4 SiC46x rows, same quotes)"
            ),
        },
    }
    (OUT / "sic46x-admission-packet-v1.json").write_text(
        json.dumps(packet, indent=1, ensure_ascii=False) + "\n"
    )

    print(json.dumps({
        "pilot_fixtures": ["tps54360_p35.json", "lm5005_p25.json"],
        "rows": [
            {"part": "TPS54360", "evidence_id": tps_row["evidence_id"]},
            {"part": "LM5005", "evidence_id": lm_row["evidence_id"]},
        ],
        "probe": rows_doc["operating_point_probe"],
        "packet": "sic46x-admission-packet-v1.json",
    }, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
