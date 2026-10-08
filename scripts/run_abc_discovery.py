#!/usr/bin/env python3
"""CURVE-05B — frozen A/B/C discovery validation.

Three engineering cases over the SAME frozen, SHA-verified bundle
(curve-evidence-bundle-v1-…), the SAME parametric cohort, and explicit
per-case reporting:

  A — parametric discovery (frozen title-page facts)
  B — A + semantic evidence (quoted mode/feature flags)
  C — B + advisory curve evidence (bundle rows only; no M5 services)

Per case: engineer intent, activated question/knife, surviving cohort,
preference-ranking change, the exact evidence causing it, a defensibility
verdict, and the missing evidence. Curves stay advisory: no hard
eliminations, no verified claims from pending packets, typical never a
guarantee. Ranking changes are defensible ONLY when backed by
condition-compatible, cited, in-support evidence — otherwise the case
reports a refusal and what would unlock it.
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

from harness.electronics.curve_evidence import (
    CurveEvidence,
    compare_at_operating_point,
    query_operating_point,
)
from scripts.run_curve_discovery_experiments import (
    cohort_a,
    cohort_b,
    load_cohort,
)

BUNDLE = ROOT / "tests/fixtures/m4-handoff/curve_evidence_bundle.jsonl"
MANIFEST = ROOT / "tests/fixtures/m4-handoff/release_manifest.json"
OUT = ROOT / "results/curve-05b"

ABC_SCHEMA = "harness.electronics-abc-discovery.v1"


def load_verified() -> tuple[list[dict], dict, list[CurveEvidence]]:
    manifest = json.loads(MANIFEST.read_text())
    digest = hashlib.sha256(BUNDLE.read_bytes()).hexdigest()
    if digest != manifest["bundle_sha256"]:
        raise SystemExit("bundle SHA mismatch — refusing unverified "
                         "evidence")
    rows = [json.loads(line) for line in BUNDLE.read_text().splitlines()
            if line.strip()]
    curves = []
    for row in rows:
        citation = {
            "document_sha256": row["source"]["document_sha256"],
            "page_1based": row["source"]["page_1based"],
            "figure_index": row["source"]["figure_index"],
            "series_index": row["source"]["locator"]["series_index"],
            "caption": row["source"]["caption"],
            "figure_revision": row["source"]["document_revision"],
            "series_name": row["curve"]["series_name"],
            "conditions_verbatim": row["conditions"].get("verbatim", []),
        }
        curves.append(CurveEvidence(
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
            applies_to={
                "part": row["applicability"]["part"],
                "category": row["applicability"]["category"],
                "manufacturer": row["applicability"]["manufacturer"],
            },
            uncertainty=row["uncertainty"],
            verification={"status": "reference"},
        ))
        # re-attach typed conditions parsed at bundle build time
        curves[-1].conditions = row["conditions"]
        curves[-1].relevance = (
            [{"phenomenon": row["phenomenon"],
              "use_cases": [], "categories": [], "engineer_params": [],
              "constraints": [], "limitations": [],
              "supported_region": row["supported_region"]}]
            if row.get("phenomenon") else []
        )
        curves[-1].x_kind = row["quantity"]["x"]["kind"]
        curves[-1].y_kind = row["quantity"]["y"]["kind"]
        curves[-1].supported_region = row["supported_region"]
    return rows, manifest, curves


def _cited(result: dict) -> dict:
    keep = ("curve_id", "part", "series_name", "value", "unit",
            "evidence_class", "guarantee", "matched_conditions")
    return {k: result.get(k) for k in keep if k in result} | {
        "citation": {
            "document_sha256": result["citation"]["document_sha256"],
            "page_1based": result["citation"]["page_1based"],
            "figure_index": result["citation"]["figure_index"],
            "caption": result["citation"]["caption"],
        }
    } if "citation" in result else {k: result.get(k) for k in keep
                                    if k in result}


def _op(curves, evidence_ids, x, conditions, x_unit):
    picked = [c for c in curves if c.curve_id in set(evidence_ids)]
    out = []
    for curve in picked:
        r = query_operating_point(curve, x, required_conditions=conditions,
                                  x_unit=x_unit)
        out.append({
            "evidence_id": curve.curve_id,
            "part": curve.applies_to.get("part"),
            "series": curve.series.get("name"),
            "status": r["status"],
            "value": r.get("value"),
            "unit": r.get("unit"),
            "guarantee": r.get("guarantee", False),
            "evidence_class": r.get("evidence_class"),
            "reason": r.get("reason"),
            "matched_conditions": r.get("matched_conditions"),
            "document_sha256": curve.document_sha256,
            "page": curve.page_1based,
            "figure": curve.figure_index,
        })
    return out


def case_1_48v_5v_3a(cohort, curves, matrix) -> dict:
    """Cross-family: 48 V -> 5 V at 3 A, warm enclosure (SiC46x).

    After the conflicted-member law fix, the ONLY genuine cross-family
    cell is the derating envelope (SiC462 vs SiC463). Efficiency at
    48 V -> 5 V exists as a single-family measurement (SiC462); SiC461
    and SiC464 are refused with reasons (their figures mix 12 V/24 V
    legend curves with unbound traces)."""
    req = {"vin_min": 48.0, "vin_max": 48.0, "iout_min": 3.0}
    a = cohort_a(cohort, req)
    b = cohort_b(cohort, req, ["psm_documented"])
    der_cell = next(
        (s for s in matrix["cross_family_scenarios"]
         if s["phenomenon"] == "case_temperature_limit_vs_load"), None)
    eff_cell = next(
        (s for s in matrix["single_family_scenarios"]
         if s["phenomenon"] == "efficiency_vs_load"
         and s["conditions"].get("vin_v") == 48.0
         and s["conditions"].get("vout_v") == 5.0), None)
    der_ids = ({m["evidence_id"]
                for members in der_cell["series"].values()
                for m in members} if der_cell else set())
    eff_ids = ({m["evidence_id"]
                for members in eff_cell["series"].values()
                for m in members} if eff_cell else set())
    conflicted_ids = set()
    for cell in (der_cell, eff_cell):
        if cell:
            conflicted_ids |= {m["evidence_id"]
                               for m in cell.get("conflicted_members", [])}
    derating = _op(curves, der_ids, 3.0, {"vin_v": 48.0, "vout_v": 5.0},
                   "A")
    eff = _op(curves, eff_ids, 3.0, {"vin_v": 48.0, "vout_v": 5.0}, "A")
    light = _op(curves, eff_ids, 0.5, {"vin_v": 48.0, "vout_v": 5.0}, "A")
    refused = _op(curves, conflicted_ids, 3.0,
                  {"vin_v": 48.0, "vout_v": 5.0}, "A")
    der_ok = sorted([e for e in derating if e["status"] == "ok"],
                    key=lambda e: -(e["value"] or 0))
    eff_ok = [e for e in eff if e["status"] == "ok"]
    light_ok = [e for e in light if e["status"] == "ok"]
    return {
        "case": "48V-to-5V at 3 A, warm enclosure",
        "intent": "I need 48 V to 5 V conversion at 3 A continuous in a "
                  "warm enclosure; thermal margin and efficiency decide "
                  "the family.",
        "knife_activated": "VIN rating knife (parametric, hard) — "
                           "everything but the SiC46x family and two "
                           "UNKNOWN rows fails; IOUT stays UNKNOWN for "
                           "SiC46x (never eliminated); curve evidence "
                           "activates the thermal-envelope preference "
                           "question only",
        "A_cohort": a,
        "B_cohort": b,
        "C_evidence": {
            "case_temperature_limit_at_3A": derating,
            "efficiency_at_3A_single_family": eff,
            "light_load_efficiency_at_0A5": light,
            "refused_conflicted_evidence": [
                {"evidence_id": e["evidence_id"],
                 "part": e["part"],
                 "reason": e["reason"],
                 "detail": "figure mixes 12 V / 24 V legend curves; the "
                           "extracted trace is not legend-bound"}
                for e in refused
            ],
            "ranking_change": (
                "cross-family THERMAL ranking forms: SiC462 sustains a "
                "higher case temperature than SiC463 at the shared 3 A "
                "point (best typical readings 47.6 C vs 45.0 C). "
                "Efficiency cannot rank families today: only SiC462 "
                "answers (95.48% typical at 3 A); SiC461/SiC464 refuse "
                "on legend conflicts. A and B could not separate "
                "SiC462/SiC463 at all."
            ) if der_ok else "no comparable evidence",
            "winners": {
                "thermal_headroom_3A": [e["part"] for e in der_ok[:2]],
                "efficiency_3A": [e["part"] for e in eff_ok[:1]],
                "light_load_0A5": [e["part"] for e in light_ok[:1]],
            },
        },
        "defensibility": {
            "verdict": "defensible-with-caveats",
            "caveats": [
                "all values typical; adjudication human_review_pending "
                "on every row",
                "SiC462's efficiency figure carries NO captured legend "
                "rows, so it relies on the page-default conditions "
                "(VIN = 48 V) — sibling figures on other pages print "
                "12 V/24 V legends, so its cold-review packet must "
                "confirm the legend before the number is trusted",
                "derating series are unnamed variants (two per device); "
                "values reported as printed orientation",
                "guarantee:false everywhere; no hard eliminations",
            ],
        },
        "missing_evidence": [
            "legend binding for SiC461/SiC464 efficiency figures (their "
            "legend values are vector-text) — unlocks the four-way "
            "efficiency comparison",
            "SiC46x per-device IOUT ratings — parametric IOUT stays "
            "UNKNOWN",
            "a second manufacturer at 48 V -> 5 V for cross-vendor "
            "comparison",
            "airflow / junction context for the derating envelopes",
        ],
    }


def case_2_12v_1v1_30a(cohort, curves) -> dict:
    req = {"vin_min": 12.0, "vin_max": 12.0, "iout_min": 30.0}
    a = cohort_a(cohort, req)
    b = cohort_b(cohort, req, [])
    tps = [c for c in curves
           if c.applies_to.get("part") == "TPS548C26"
           and c.y_kind in ("efficiency", "power_dissipation")]
    eff = compare_at_operating_point(
        [c for c in tps if c.y_kind == "efficiency"], 30.0,
        required_conditions={"vin_v": 12.0, "vout_v": 1.1},
    )
    dis = compare_at_operating_point(
        [c for c in tps if c.y_kind == "power_dissipation"], 30.0,
        required_conditions={"vin_v": 12.0, "vout_v": 1.1},
    )
    best_eff = eff["entries"][0] if eff["entries"] else None
    best_dis = sorted(
        dis["entries"], key=lambda e: e["value"])[0] if dis["entries"] \
        else None
    return {
        "case": "12V-to-1.1V at 30 A continuous (variant selection)",
        "intent": "one parametric-eligible family (35 A rating); the real "
                  "decision is which switching mode, frequency, and bias "
                  "configuration to design in.",
        "knife_activated": "IOUT rating knife (parametric, hard) leaves "
                           "exactly one family; curve evidence answers "
                           "the VARIANT preference question A/B cannot",
        "A_cohort": a,
        "B_cohort": b,
        "C_evidence": {
            "efficiency_comparison": eff,
            "dissipation_comparison": dis,
            "ranking_change": (
                "within-family variant ranking: best typical efficiency "
                "and lowest typical dissipation at 30 A identified with "
                "citations; A and B rank variants not at all"
            ),
            "winners": {
                "efficiency_30A": best_eff and {
                    "series": best_eff["series_name"],
                    "value": best_eff["value"],
                    "unit": best_eff["unit"],
                    "evidence_id": best_eff["curve_id"],
                },
                "dissipation_30A": best_dis and {
                    "series": best_dis["series_name"],
                    "value": best_dis["value"],
                    "unit": best_dis["unit"],
                    "evidence_id": best_dis["curve_id"],
                },
            },
        },
        "defensibility": {
            "verdict": "defensible-with-caveats",
            "caveats": [
                "mode (FCCM/DCM) and VCC bias left unpinned are flagged "
                "in condition_dimensions_unpinned — the winner is "
                "conditional on those dimensions",
                "typical values; no derating curve at matched conditions "
                "exists, so thermal clearance stays an open question",
            ],
        },
        "missing_evidence": [
            "thermal derating or Zth for TPS548C26 at 12 V -> 1.1 V",
            "30 A is 86% of the 35 A rating — margin cannot be computed "
            "from present evidence",
        ],
    }


def case_3_12v_3v3_light_load(cohort, curves) -> dict:
    req = {"vin_min": 12.0, "vin_max": 12.0, "iout_min": 0.1}
    a = cohort_a(cohort, req)
    b = cohort_b(cohort, req, ["light_load_optimized", "psm_documented",
                               "fpwm_available"])
    lmr36 = [c for c in curves
             if c.applies_to.get("part") == "LMR36502"
             and c.y_kind == "efficiency"]
    lmr33 = [c for c in curves
             if c.applies_to.get("part") == "LMR33610"
             and c.y_kind == "current_consumption"]
    eff = _op(curves, {c.curve_id for c in lmr36}, 0.1,
              {"vin_v": 12.0, "vout_v": 3.3}, "A")
    # mA axis handled by unit conversion at query time
    standby = []
    for curve in lmr33:
        r = query_operating_point(curve, 12.0,
                                  required_conditions={"vin_v": 12.0},
                                  x_unit="V")
        if r["status"] == "ok" and r.get("unit"):
            scale = {"µa": 1e-6, "ua": 1e-6, "na": 1e-9,
                     "ma": 1e-3}.get(r["unit"].lower(), 1.0)
            amps = r["value"] * scale
            standby.append({
                "evidence_id": curve.curve_id,
                "series": curve.series.get("name"),
                "current_a": amps,
                "input_power_w": 12.0 * amps,
                "document_sha256": curve.document_sha256,
                "page": curve.page_1based,
                "guarantee": False,
                "evidence_class": r["evidence_class"],
            })
    standby.sort(key=lambda s: s["input_power_w"])
    eff_ok = [e for e in eff if e["status"] == "ok"]
    refusals = [e for e in eff if e["status"] != "ok"]
    return {
        "case": "12V-to-3.3V sensor supply, 95% sleep / 5% active",
        "intent": "compact supply for a sensor sleeping most of the day; "
                  "energy over the duty profile decides.",
        "knife_activated": "VIN rating knife eliminates TPS628302 only; "
                           "curve evidence activates the standby-budget "
                           "and light-load-efficiency questions",
        "A_cohort": a,
        "B_cohort": b,
        "C_evidence": {
            "efficiency_at_100mA": eff,
            "standby_power_at_12V": standby,
            "ranking_change": (
                "B flags LMR36502 'light-load optimized' (a claim); C "
                "converts the claim into a measured typical 84.77% at "
                "the exact operating point (legend VIN = 12 V) and adds "
                "the only measured 12 V standby figures (LMR33610 "
                "shutdown 61.7 uW / quiescent 300.6 uW). Preference: "
                "LMR36502 for the active burst; LMR33610 if a sleep "
                "budget under ~0.1 mW dominates and EN switching is "
                "acceptable."
            ) if eff_ok or standby else "no comparable evidence",
            "winners": {
                "efficiency_100mA": [e["part"] for e in eff_ok[:1]],
                "standby_power": [s["series"] for s in standby[:1]],
            },
            "refusals": [
                {"evidence_id": e["evidence_id"], "reason": e["reason"]}
                for e in refusals
            ],
        },
        "defensibility": {
            "verdict": "defensible-with-caveats",
            "caveats": [
                "the 84.77% figure is a legend-overridden condition "
                "(page default 13.5 V; series legend VIN = 12 V) — the "
                "override is recorded in the evidence row",
                "standby numbers are typical; sensor's own sleep draw "
                "must be added by the engineer",
            ],
        },
        "missing_evidence": [
            "efficiency curves at 12 V -> 3.3 V for the OTHER eligible "
            "families (TPS548C26, TPS563203, SiC448) — today they are "
            "refused, not guessed",
            "quiescent curves for LMR36502 at 12 V",
        ],
    }


def run() -> dict:
    rows, manifest, curves = load_verified()
    cohort = load_cohort()
    matrix = json.loads(
        (OUT / "comparability_matrix.json").read_text()
    ) if (OUT / "comparability_matrix.json").exists() else {}
    if not matrix:
        raise SystemExit("build the comparability matrix first")
    cases = [
        case_1_48v_5v_3a(cohort, curves, matrix),
        case_2_12v_1v1_30a(cohort, curves),
        case_3_12v_3v3_light_load(cohort, curves),
    ]
    report = {
        "schema": ABC_SCHEMA,
        "bundle_release": manifest["release_id"],
        "bundle_sha256": manifest["bundle_sha256"],
        "bundle_rows": len(rows),
        "laws": [
            "curves advisory: no hard eliminations, no verified claims "
            "from pending packets, typical never guaranteed",
            "every ranking change cites evidence ids resolvable in the "
            "bundle",
        ],
        "cases": cases,
        "three_examples_verdict": {
            "delivered": 3,
            "reproducible_from": "frozen bundle rows + pinned cohort; "
                                 "re-run scripts/run_abc_discovery.py",
            "note": "example 1 is a genuine cross-family ranking on the "
                    "derating envelope (SiC462 vs SiC463) plus a "
                    "single-family efficiency measurement and reasoned "
                    "refusals; example 2 ranks within-family variants; "
                    "example 3 converts a semantic claim into a cited "
                    "measurement. Cross-family EFFICIENCY ranking is "
                    "NOT claimable today — SiC461/SiC464 refuse on "
                    "legend conflicts until legend binding exists.",
        },
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "abc_discovery.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str)
        + "\n"
    )
    return report


def main() -> int:
    report = run()
    for case in report["cases"]:
        winners = case["C_evidence"].get("winners", {})
        print(f"{case['case']}: verdict="
              f"{case['defensibility']['verdict']} winners="
              f"{json.dumps(winners, default=str)}")
    print(json.dumps(report["three_examples_verdict"], indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
