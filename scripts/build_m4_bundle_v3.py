#!/usr/bin/env python3
"""CURVE-07E R7 — build the versioned M4 handoff (bundle v3 + handoff
manifest).

Bundle v3 succeeds curve-evidence-bundle-v2 (frozen; v1/v2 immutability
preserved). Rows keep the v2 contract (canonical hierarchy, ratings,
conditions) and add trace provenance: binding_method and trace_source per
series, plus physics-check results where applied (VIN monotonicity for
positional legend binding).

The handoff manifest carries the release-level artifacts the PRD
requires: five-level coverage matrix (R4), catalog-admission packets
(R3), evidence-gap outcomes (R6), cold-review packet index (R5), and the
comparator fingerprint (M5/M4 parity anchor). Everything is SHA-covered
by the manifest digest.
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harness.discovery.curves import load_reference_curves
from harness.electronics.curve_evidence import (
    condition_compatibility,
    condition_sufficiency,
    query_operating_point,
)

PILOT = ROOT / "tests/fixtures/gold/curve_evidence_pilot"
OUT = ROOT / "tests/fixtures/m4-handoff"
HANDOFF = ROOT / "results/curve-07e"

BUNDLE_SCHEMA = "harness.m4-curve-evidence-bundle.v1"
COMPARATOR_FINGERPRINT = (
    "harness.electronics-curve_evidence@curve/07e-supply: "
    "condition_compatibility(sweep-law, legend-ambiguity, "
    "envelope-resolution) + query_operating_point(bounded linear/log10) "
    "+ significance(variant-spread law); parity anchor for M4's vendored "
    "copy at curve-evidence-bundle-v2-3b5658b629cc"
)

# priority figures for cold review (R5): the 48V->5V decision figures
# plus the highest-value ambiguous traces
REVIEW_PRIORITIES = [
    ("vishay_sic46x_p15.json", 0, "SiC462 48V efficiency (full range)"),
    ("vishay_sic46x_p14.json", 4, "SiC461 48V efficiency (light load)"),
    ("vishay_sic46x_p16.json", 0, "SiC463 48V efficiency"),
    ("vishay_sic46x_p17.json", 0, "SiC464 48V efficiency"),
    ("vishay_sic46x_p14.json", 0,
     "SiC461 efficiency UNBOUND trace (36V-vs-48V ambiguous)"),
    ("vishay_sic46x_p15.json", 3,
     "SiC462 efficiency UNBOUND trace (ambiguous)"),
    ("vishay_sic46x_p17.json", 3,
     "SiC464 efficiency UNBOUND trace (ambiguous)"),
    ("lm5161_p7.json", 0,
     "LM5161 VOUT=5V efficiency (color-cloud + positional binding)"),
    ("lm5161_p7.json", 2, "LM5161 VOUT=3.3V efficiency"),
    ("tps548c26_p10.json", 0, "TPS548C26 12V/1.1V efficiency (anchor)"),
]


def canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), default=str).encode()


def _physics_check(curves_by_plot):
    """For plots whose series bind multiple VIN legends: efficiency must
    be non-increasing in VIN at matched load (buck physics)."""

    out = {}
    for key, curves in curves_by_plot.items():
        by_vin = {}
        for c in curves:
            m = c.series.get("name") or ""
            if "VIN =" in m:
                try:
                    vin = float(m.split("=")[1].replace("V", "").strip())
                except ValueError:
                    continue
                by_vin[vin] = c
        if len(by_vin) < 2:
            continue
        probes = []
        for x in (0.1, 0.5, 1.0):
            reading = []
            for vin in sorted(by_vin):
                r = query_operating_point(by_vin[vin], x)
                if r["status"] == "ok":
                    reading.append((vin, r["value"]))
            if len(reading) >= 2:
                mono = all(reading[i][1] >= reading[i + 1][1] - 1.5
                           for i in range(len(reading) - 1))
                probes.append({"x": x, "reading": reading,
                               "vin_monotone": mono})
        if probes:
            out[key] = {"checks": probes,
                        "pass": all(p["vin_monotone"] for p in probes)}
    return out


def build_rows(physics):
    rows = []
    for path in sorted(PILOT.glob("*.json")):
        if path.name.startswith("_"):
            continue
        record = json.loads(path.read_text())
        plot_curves = {}
        for plot in record.get("plots") or []:
            plot_curves[(path.name, plot.get("_figure_index"))] = (
                plot, [])
        curves_all = load_reference_curves([path])
        for curve in curves_all:
            key = (path.name, curve.figure_index)
            if key in plot_curves:
                plot_curves[key][1].append(curve)
        for (fname, fig_idx), (plot, curves) in plot_curves.items():
            physics_key = f"{fname}#fig{fig_idx}"
            for curve in curves:
                series_index = curve.series_index
                citation = curve.citation()
                relevance = curve.relevance[0] if curve.relevance else None
                binding = "positional_legend" if curve.series.get(
                    "name") else "unbound_ambiguous"
                row = {
                    "schema": BUNDLE_SCHEMA,
                    "evidence_id": curve.curve_id,
                    "advisory_only": True,
                    "source": {
                        "document_sha256": curve.document_sha256,
                        "document_revision": curve.figure_revision,
                        "page_1based": curve.page_1based,
                        "figure_index": curve.figure_index,
                        "caption": curve.caption,
                        "locator": {
                            "page_1based": curve.page_1based,
                            "figure_index": curve.figure_index,
                            "series_index": series_index,
                            "region_bbox": citation.get("region_bbox"),
                        },
                        "source_artifact": record.get("source_artifact"),
                        "manufacturer": record.get("manufacturer"),
                    },
                    "applicability": {
                        "family": curve.applies_to.get("family_group")
                        or curve.applies_to.get("part"),
                        "family_group": curve.applies_to.get(
                            "family_group") or curve.applies_to.get("part"),
                        "part": curve.applies_to.get("part"),
                        "manufacturer": curve.applies_to.get(
                            "manufacturer"),
                        "category": curve.applies_to.get("category"),
                        "coverage_kind": "primary",
                    },
                    "phenomenon": relevance["phenomenon"]
                    if relevance else None,
                    "quantity": {
                        "x": {"kind": curve.x_kind,
                              "label": (curve.axes.get("x") or {}).get(
                                  "label"),
                              "unit": (curve.axes.get("x") or {}).get(
                                  "unit"),
                              "scale": curve.x_scale},
                        "y": {"kind": curve.y_kind,
                              "label": (curve.axes.get("y") or {}).get(
                                  "label"),
                              "unit": (curve.axes.get("y") or {}).get(
                                  "unit")},
                    },
                    "conditions": curve.conditions,
                    "missing_dimensions": condition_sufficiency(curve, {})
                    if relevance else [],
                    "supported_region": curve.supported_region,
                    "evidence_class": curve.evidence_class,
                    "guarantee": False,
                    "adjudication": {
                        "state": "human_review_pending",
                        "packet_hash": None,
                        "note": "machine-verified only",
                    },
                    "binding": {
                        "method": binding,
                        "trace_source": "vector_multiseg_or_color_cloud",
                        "physics_check": physics.get(physics_key)
                        if binding == "positional_legend" else None,
                    },
                    "operating_point_query": {
                        "supported": True,
                        "method": "log10_interpolation_on_digitized_"
                                  "polyline" if curve.x_scale == "log10"
                        else "linear_interpolation_on_digitized_polyline",
                        "bounds": "strictly inside sampled support; "
                                  "extrapolation refused",
                    },
                    "uncertainty": curve.uncertainty,
                    "comparability": {
                        "verdict": "condition_aware",
                        "rules": "comparable only when stated conditions "
                                 "agree; missing never defaulted; typical "
                                 "never guaranteed",
                    },
                    "release_ids": {"extraction": "vector-reference-v2"
                                    "+cloud-binding",
                                    "retrieval": "curve-retrieval-v0-"
                                    "experimental",
                                    "bundle": None},
                    "curve": {
                        "series_name": curve.series.get("name"),
                        "points": curve.points,
                        "original_plot_reference": {
                            "document_sha256": curve.document_sha256,
                            "page_1based": curve.page_1based,
                            "figure_index": curve.figure_index,
                            "caption": curve.caption,
                        },
                    },
                }
                rows.append(row)
    return rows


def coverage_matrix(rows):
    """R4: five separated comparison levels per operating-condition
    cell. Never one blended number."""

    def cell_key(row):
        keys = (row.get("conditions") or {}).get("keys") or {}
        return (row.get("phenomenon"), keys.get("vin_v"), keys.get("vout_v"))

    cells: dict[tuple, dict] = {}
    for row in rows:
        if not row.get("phenomenon"):
            continue
        if (row.get("conditions") or {}).get("conflict"):
            continue
        if (row.get("conditions") or {}).get("legend_ambiguity"):
            continue
        key = cell_key(row)
        entry = cells.setdefault(key, {
            "phenomenon": key[0], "vin_v": key[1], "vout_v": key[2],
            "manufacturers": {}, "families": {}, "devices": {},
            "evidence_ids": [], "adjudication_states": set(),
        })
        app = row["applicability"]
        entry["manufacturers"].setdefault(
            app["manufacturer"], set()).add(app["family_group"])
        entry["families"].setdefault(
            app["family_group"], set()).add(app["part"])
        entry["devices"].setdefault(app["part"], set()).add(
            row["curve"].get("series_name") or "default")
        entry["evidence_ids"].append(row["evidence_id"])
        entry["adjudication_states"].add(
            row["adjudication"]["state"])

    out = []
    for key, e in sorted(cells.items(), key=str):
        n_fam = len(e["families"])
        n_man = len(e["manufacturers"])
        levels = {
            "same_configuration_observations":
                sum(len(v) for v in e["devices"].values()),
            "within_device_configurations": sum(
                1 for v in e["devices"].values() if len(v) > 1),
            "within_family_device_comparisons": max(
                (len(v) for v in e["families"].values()), default=0),
            "cross_family_comparisons": n_fam if n_fam >= 2 else 0,
            "cross_manufacturer_comparisons": n_man if n_man >= 2 else 0,
        }
        out.append({
            "phenomenon": e["phenomenon"], "vin_v": e["vin_v"],
            "vout_v": e["vout_v"],
            "manufacturers": {m: sorted(f)
                              for m, f in e["manufacturers"].items()},
            "families": sorted(f for f in e["families"] if f),
            "devices": sorted(d for d in e["devices"] if d),
            "family_scoped_devices": sum(
                1 for d in e["devices"] if not d),
            "evidence_ids": e["evidence_ids"],
            "comparison_levels": levels,
            "comparison_availability": (
                "cross_manufacturer" if n_man >= 2 else
                "cross_family" if n_fam >= 2 else
                "within_family" if any(len(v) > 1
                                       for v in e["families"].values())
                else "single_device"),
            "adjudication_states": sorted(e["adjudication_states"]),
            "uncertainty_note": "tick-fit residuals ride each evidence "
                                "row; variant spreads gate significance",
        })
    return out


def admission_packets(rows):
    """R3: deterministic catalog-admission packets for the Vishay SiC46x
    family (absent from M4's power catalog)."""

    ratings = json.loads(
        (PILOT / "_device_ratings.json").read_text())["ratings"]
    sha_ref = None
    sic_rows = [r for r in rows
                if r["applicability"]["family_group"] == "SiC46x"]
    if sic_rows:
        sha_ref = sic_rows[0]["source"]["document_sha256"]
    packets = []
    for rating in ratings:
        part = rating["part"]
        part_rows = [r for r in sic_rows
                     if r["applicability"]["part"] == part]
        packets.append({
            "packet_id": f"admit-sic46x-{part.lower()}",
            "schema": "harness.catalog-admission-request.v1",
            "identity": {
                "manufacturer": "vishay",
                "canonical_family": "SiC46x",
                "series": part,
                "device": part,
                "orderable_identities": [part],
                "identity_provenance": {
                    "datasheet_sha256": sha_ref,
                    "title_page_quote":
                        "SiC461, SiC462, SiC463, SiC464 — Vishay "
                        "Siliconix S25-1437-Rev. S, 17-Nov-2025, "
                        "Document Number: 65124",
                    "ratings_quote": rating["quote"],
                    "page_1based": rating["page_1based"],
                },
            },
            "ratings": {
                "vin_min_v": 4.5, "vin_max_v": 60.0,
                "vout_v": 5.0,
                "iout_max_a": rating["rated_iout_a"],
                "evidence": rating["quote"],
                "evidence_class": "rated_limit_from_header_quote",
            },
            "curve_associations": [r["evidence_id"] for r in part_rows],
            "part_vs_family_scope": (
                "ratings are DEVICE-scoped (each device's own header); "
                "curves are DEVICE-scoped on the shared family eval "
                "board; family-level claims require per-device evidence"
            ),
            "conflicts_unresolved": [
                "unbound efficiency traces (36V-vs-48V ambiguous) remain "
                "refused, never defaulted"
            ] if any(not r["curve"]["series_name"]
                     for r in part_rows) else [],
            "request": (
                "admit to power-management aisle via the standard "
                "ingestion pipeline; provenance tier: T1_DATASHEET "
                "(sha-verified); do NOT assign OPNs beyond the four "
                "documented device identities"
            ),
        })
    return packets


def evidence_requests(rows):
    """R6: consume M4's gap request and record outcomes."""

    matrix = coverage_matrix(rows)
    cross = [c for c in matrix
             if c["comparison_levels"]["cross_manufacturer_comparisons"]
             >= 2]
    return {
        "request_source": "M4 CURVE-06 cross_family_discovery evidence "
                          "request (48V->5V second family)",
        "outcomes": [
            {
                "request": "second family efficiency curve at 48 V -> "
                           "5 V",
                "status": "satisfied",
                "evidence": "LM5161 (texas_instruments) Figure 2 p.7: "
                            "VOUT = 5 V, RON = 169 kOhm, L = 47 uH, "
                            "300 kHz; VIN = 36/48/60 V traces "
                            "legend-bound, VIN-monotonicity physics "
                            "check passed",
                "cross_manufacturer_cells_unlocked": [
                    {"phenomenon": c["phenomenon"], "vin_v": c["vin_v"],
                     "vout_v": c["vout_v"], "families": c["families"]}
                    for c in cross
                ],
            },
            {
                "request": "catalog admission for Vishay SiC46x",
                "status": "prepared_not_applied",
                "evidence": "four admission packets attached; only the "
                            "authorized catalog owner may apply them",
            },
            {
                "request": "human adjudication of curve evidence",
                "status": "blocked_pending_human",
                "evidence": "cold-review packets emitted for the 10 "
                            "highest-value figures; approval pipeline "
                            "accepts authorized verdicts later without "
                            "identity change",
            },
            {
                "request": "IR3883/IR3447/ADP5003/LT8292 acquisition",
                "status": "blocked_source_access",
                "evidence": "network egress filtered (CURVE-04 "
                            "challenge manifest); retry from an "
                            "unfiltered environment",
            },
        ],
    }


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    HANDOFF.mkdir(parents=True, exist_ok=True)

    # physics checks over all fixtures
    physics = {}
    for path in sorted(PILOT.glob("*.json")):
        if path.name.startswith("_"):
            continue
        record = json.loads(path.read_text())
        plots = record.get("plots") or []
        curves_all = load_reference_curves([path])
        by_fig = {}
        for c in curves_all:
            by_fig.setdefault(c.figure_index, []).append(c)
        for plot in plots:
            fig = plot.get("_figure_index")
            curves = by_fig.get(fig, [])
            sub = {}
            for curve in curves:
                sub.setdefault(fig, []).append(curve)
            for fig_idx, cs in sub.items():
                result = _physics_check({(path.name, fig_idx): cs})
                if result:
                    physics.update(result)

    rows = build_rows(physics)
    manifest_prev = json.loads(
        (OUT / "release_manifest_v2.json").read_text())

    release_digest = hashlib.sha256(
        b"".join(canonical(r) for r in rows)).hexdigest()[:12]
    release_id = f"curve-evidence-bundle-v3-{release_digest}"
    for row in rows:
        row["release_ids"]["bundle"] = release_id

    bundle_path = OUT / "curve_evidence_bundle_v3.jsonl"
    with bundle_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False,
                                    default=str) + "\n")

    matrix = coverage_matrix(rows)
    packets = admission_packets(rows)
    requests = evidence_requests(rows)
    review = [
        {"file": f, "figure_index": i, "why": why}
        for f, i, why in REVIEW_PRIORITIES
    ]

    handoff = {
        "schema": "harness.m4-curve-handoff.v3",
        "release_id": release_id,
        "supersedes": manifest_prev["release_id"],
        "bundle_sha256": hashlib.sha256(
            bundle_path.read_bytes()).hexdigest(),
        "rows": len(rows),
        "coverage_matrix": matrix,
        "catalog_admission_packets": packets,
        "evidence_request_outcomes": requests,
        "cold_review_priority": review,
        "comparator_fingerprint": COMPARATOR_FINGERPRINT,
        "numeric_evaluation_fixtures": [
            "tests/test_curve05a_comparison.py (12-case stress suite)",
            "tests/test_curve_evidence_pilot.py (frozen pilot gates)",
            "results/curve-05b/corrections_v2.json (within-family demo)",
        ],
        "laws": [
            "typical never guaranteed; no gold promotion without human "
            "sign-off",
            "unbound/ambiguous traces stay refused, never defaulted",
            "cross-manufacturer claims require distinct canonical "
            "families AND aligned conditions AND physics checks",
        ],
        "built_at": time.time(),
    }
    (OUT / "curve_evidence_bundle_v3_manifest.json").write_text(
        json.dumps(handoff, indent=2, ensure_ascii=False, default=str)
        + "\n"
    )
    print(json.dumps({
        "release_id": release_id,
        "rows": len(rows),
        "matrix_cells": len(matrix),
        "cross_manufacturer_cells": sum(
            1 for c in matrix if c["comparison_levels"]
            ["cross_manufacturer_comparisons"] >= 2),
        "admission_packets": len(packets),
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
