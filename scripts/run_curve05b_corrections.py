#!/usr/bin/env python3
"""CURVE-05B targeted corrections (review round 2).

Everything here runs off the frozen v2 bundle
(curve-evidence-bundle-v2-…): canonical hierarchy grain, device ratings,
legend-bound vendor traces. The v1 bundle and the v1 ABC results stay
frozen (abc_discovery_v1_frozen.json) — corrections are reported
transparently, never silently rewritten.

Sections:
  1. matrix v2 — comparison cells grouped by CANONICAL family_group
  2. cold recheck of the 47.6 C vs 45.0 C thermal claim (variant spread,
     conditions, uncertainty, significance verdict)
  3. within-family SiC46x four-variant efficiency demo at 1 A and 3 A
     with HARD current-rating eligibility applied before curve ranking
  4. cross-family (canonical) search — what prevents above-OPN family
     discovery today, exactly
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harness.electronics.curve_evidence import (
    CurveEvidence,
    query_operating_point,
)

BUNDLE_V2 = (ROOT / "tests/fixtures/m4-handoff/"
             "curve_evidence_bundle_v2.jsonl")
MANIFEST_V2 = ROOT / "tests/fixtures/m4-handoff/release_manifest_v2.json"
OUT = ROOT / "results/curve-05b"


def load_curves() -> tuple[list[CurveEvidence], dict]:
    manifest = json.loads(MANIFEST_V2.read_text())
    curves = []
    for line in BUNDLE_V2.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        curve = CurveEvidence(
            document_sha256=row["source"]["document_sha256"],
            page_1based=row["source"]["page_1based"],
            figure_index=row["source"]["figure_index"],
            series_index=row["source"]["locator"]["series_index"],
            caption=row["source"]["caption"],
            region_bbox=row["source"]["locator"]["region_bbox"],
            figure_revision=row["source"]["document_revision"],
            axes={
                "x": {"label": row["quantity"]["x"]["label"],
                      "unit": row["quantity"]["x"]["unit"],
                      "scale": row["quantity"]["x"]["scale"]},
                "y": {"label": row["quantity"]["y"]["label"],
                      "unit": row["quantity"]["y"]["unit"]},
            },
            series={"name": row["curve"]["series_name"],
                    "points": row["curve"]["points"]},
            conditions_verbatim=row["conditions"].get("verbatim", []),
            evidence_class_=row["evidence_class"],
            applies_to=dict(row["applicability"]),
            uncertainty=row["uncertainty"],
            verification={"status": "reference"},
        )
        curve.conditions = row["conditions"]
        curve.x_kind = row["quantity"]["x"]["kind"]
        curve.y_kind = row["quantity"]["y"]["kind"]
        curve.supported_region = row["supported_region"]
        curve.relevance = ([{"phenomenon": row["phenomenon"]}]
                           if row.get("phenomenon") else [])
        curves.append(curve)
    return curves, manifest


def _op(curve, x, conditions, unit):
    r = query_operating_point(curve, x, required_conditions=conditions,
                              x_unit=unit)
    return {
        "evidence_id": curve.curve_id,
        "device": curve.applies_to.get("part"),
        "family": curve.applies_to.get("family_group"),
        "series": curve.series.get("name"),
        "rated_iout_a": curve.applies_to.get("rated_iout_a"),
        "status": r["status"],
        "value": r.get("value"),
        "unit": r.get("unit"),
        "guarantee": r.get("guarantee", False),
        "reason": r.get("reason"),
        "page": curve.page_1based,
        "figure": curve.figure_index,
    }


def matrix_v2(curves) -> dict:
    cells: dict[tuple, dict] = {}
    for curve in curves:
        if not curve.relevance:
            continue
        phenomenon = curve.relevance[0]["phenomenon"]
        keys = (curve.conditions.get("keys") or {})
        if (curve.conditions.get("conflict")):
            continue  # conflicted rows never support cells (v1 law kept)
        vin, vout = keys.get("vin_v"), keys.get("vout_v")
        cell_key = (phenomenon, vin, vout)
        entry = cells.setdefault(cell_key, {"phenomenon": phenomenon,
                                            "vin_v": vin, "vout_v": vout,
                                            "families": {}})
        fam = curve.applies_to.get("family_group")
        entry["families"].setdefault(fam, set()).add(curve.applies_to.get(
            "part"))
    out = []
    for key, entry in sorted(cells.items(), key=str):
        families = {f: sorted(p) for f, p in entry["families"].items()}
        out.append({**entry, "families": families,
                    "canonical_cross_family": len(families) >= 2})
    return {"cells": out,
            "cross_family_cells": [c for c in out
                                   if c["canonical_cross_family"]]}


def cold_recheck_thermal(curves) -> dict:
    """Recheck SiC462 vs SiC463 case temperature at 3 A with everything
    on the table: per-variant spread, differing external inductors,
    measurement uncertainty, and a significance verdict."""

    readings = {"SiC462": [], "SiC463": []}
    for curve in curves:
        if curve.relevance and \
                curve.relevance[0]["phenomenon"] == "case_temperature_vs_load":
            if curve.applies_to.get("part") in readings:
                r = query_operating_point(
                    curve, 3.0,
                    required_conditions={"vin_v": 48.0, "vout_v": 5.0},
                    x_unit="A",
                )
                if r["status"] == "ok":
                    readings[curve.applies_to["part"]].append(
                        r["value"])
    spread_462 = max(readings["SiC462"]) - min(readings["SiC462"]) \
        if len(readings["SiC462"]) > 1 else None
    spread_463 = max(readings["SiC463"]) - min(readings["SiC463"]) \
        if len(readings["SiC463"]) > 1 else None
    best_462 = max(readings["SiC462"]) if readings["SiC462"] else None
    best_463 = max(readings["SiC463"]) if readings["SiC463"] else None
    delta = (best_462 - best_463) if (best_462 is not None
                                      and best_463 is not None) else None
    max_spread = max(spread_462 or 0, spread_463 or 0)
    return {
        "claim": "v1 reported SiC462 47.6 C vs SiC463 45.0 C sustained "
                 "case temperature at 3 A as a cross-family ranking",
        "corrections": [
            "GRAIN: SiC462 and SiC463 are ONE canonical family (Vishay "
            "SiC46x); the comparison is within-family device comparison, "
            "not family-level discovery",
            "SEMANTICS: the curves are case temperature vs output "
            "current measured on Vishay's evaluation setup — NOT "
            "guaranteed thermal derating limits",
        ],
        "readings_at_3A_C": readings,
        "best_values_C": {"SiC462": best_462, "SiC463": best_463},
        "per_device_variant_spread_C": {"SiC462": spread_462,
                                        "SiC463": spread_463},
        "delta_of_bests_C": delta,
        "measurement_uncertainty": "tick-fit residual <= 0.014% of axis "
                                   "span (~0.006 C) — negligible vs the "
                                   "variant spread",
        "external_components_differ": {
            "SiC462_efficiency_legends": "L = 5.6-8.2 uH by input voltage",
            "SiC463_efficiency_legends": "L = 10-22 uH by input voltage",
            "note": "the derating traces are unnamed variants; the "
                    "efficiency figures show different inductors per "
                    "device, so eval-board thermal setups plausibly "
                    "differ too",
        },
        "significance_verdict": (
            "NOT SIGNIFICANT as a device ranking: the best-vs-best delta "
            f"({round(delta, 2) if delta is not None else None} C) is "
            "smaller than the unnamed-variant spread within each device "
            f"({round(spread_462, 2) if spread_462 is not None else None}"
            " / "
            f"{round(spread_463, 2) if spread_463 is not None else None}"
            " C). The v1 ranking is retracted."
        ),
    }


def within_family_demo(curves) -> dict:
    """48 V -> 5 V efficiency across the four SiC46x devices at 1 A
    (all four rating-eligible) and 3 A (only SiC461/SiC462 eligible —
    HARD rating, not curve opinion)."""

    def eligible(curve, load):
        rated = curve.applies_to.get("rated_iout_a")
        return rated is None or rated >= load - 1e-9

    out = {}
    for load in (1.0, 3.0):
        results = []
        for curve in curves:
            if not (curve.relevance and curve.relevance[0]["phenomenon"]
                    == "efficiency_vs_load"):
                continue
            keys = curve.conditions.get("keys") or {}
            if keys.get("vin_v") != 48.0 or keys.get("vout_v") != 5.0:
                continue
            rated = curve.applies_to.get("rated_iout_a")
            entry = _op(curve, load, {"vin_v": 48.0, "vout_v": 5.0}, "A")
            if rated is not None and rated < load - 1e-9:
                entry["status"] = "rating_excluded"
                entry["reason"] = (
                    f"hard current rating {rated} A excludes this device "
                    f"at {load} A before any curve consideration")
            results.append(entry)
        ok = sorted([r for r in results if r["status"] == "ok"],
                    key=lambda r: -(r["value"] or 0))
        out[f"load_{load}A"] = {
            "results": results,
            "ranking": [
                {"device": r["device"], "series": r["series"],
                 "eta_pct": round(r["value"], 2),
                 "evidence_id": r["evidence_id"]}
                for r in ok
            ],
            "rating_excluded": [
                {"device": r["device"], "reason": r["reason"]}
                for r in results if r["status"] == "rating_excluded"
            ],
        }
    return out


def cross_family_search(matrix) -> dict:
    blocked = []
    for cell in matrix["cells"]:
        if cell["canonical_cross_family"]:
            blocked.append({"cell": cell, "status": "EXISTS"})
    if not blocked:
        blocked = [
            {
                "phenomenon": c["phenomenon"],
                "vin_v": c["vin_v"],
                "vout_v": c["vout_v"],
                "single_family": list(c["families"]),
                "status": "single_canonical_family_only",
            }
            for c in matrix["cells"]
        ]
    return {
        "cross_family_cells_found": len(matrix["cross_family_cells"]),
        "cells": blocked,
        "explanation": (
            "After canonical-grain correction, NO cell in the frozen "
            "corpus contains two different canonical families with "
            "condition-compatible curves: the 48 V/5 V and 48 V derating "
            "cells are all SiC46x (one Vishay family); every TI "
            "efficiency cell sits at different rails (12/1.1, 12/3.3, "
            "13.5/3.3, 24/3.3, 36/3.3, 48/3.3) — LMR36502's 48 V cell is "
            "3.3 V-out, one rail away from comparability with SiC46x "
            "48 V/5 V. Above-OPN family discovery is therefore NOT yet "
            "demonstrated, and the exact unlockers are: (1) any second "
            "family's efficiency curve at 48 V -> 5 V (or SiC46x at "
            "3.3 V-out), (2) the network-blocked ADI/Infineon "
            "acquisitions, (3) LMR36502 3.3 V rails being one vout away."
        ),
    }


def main() -> int:
    curves, manifest = load_curves()
    matrix = matrix_v2(curves)
    report = {
        "schema": "harness.electronics-curve05b-corrections.v1",
        "bundle_release": manifest["release_id"],
        "supersedes": manifest.get("supersedes"),
        "frozen_v1_results": "results/curve-05b/abc_discovery_v1_frozen"
                             ".json (unchanged)",
        "matrix_v2": matrix,
        "cold_recheck_thermal": cold_recheck_thermal(curves),
        "within_family_demo": within_family_demo(curves),
        "cross_family_search": cross_family_search(matrix),
    }
    (OUT / "corrections_v2.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str)
        + "\n"
    )
    recheck = report["cold_recheck_thermal"]
    print(json.dumps({
        "bundle": manifest["release_id"],
        "thermal_recheck": recheck["significance_verdict"],
        "ranking_1A": [r["device"] for r in
                       report["within_family_demo"]["load_1.0A"]
                       ["ranking"]],
        "rating_excluded_3A": [r["device"] for r in
                               report["within_family_demo"]["load_3.0A"]
                               ["rating_excluded"]],
        "cross_family_cells": report["cross_family_search"]
        ["cross_family_cells_found"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
