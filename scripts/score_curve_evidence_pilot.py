#!/usr/bin/env python3
"""Score the frozen curve-evidence pilot (PRD-CURVE-02 R6).

Pilot set: tests/fixtures/gold/curve_evidence_pilot/ (vector-anchored
references + hand-authored probes) plus the MCU hand-labeled gold in
tests/fixtures/gold/typical_curves/.

Two evaluation layers:

1. reference integrity — plot identification vs the printed figure count,
   axis label/unit correctness, series identification (count + names),
   evidence class, printed-condition capture;
2. decision-grade behavior — probe-by-probe condition compatibility
   classification, false comparability rate (must_reject probes answered
   'ok'), and correct rejection of out-of-range operating points.

With --predictions (extract_typical_curves JSONL) the wave-A extraction
metrics (axis/series/point error vs the frozen references) also run via
score_typical_curves_gold; without it they are reported as not_run.

Exit code 1 when any acceptance gate fails.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harness.discovery.curves import query_curve_evidence  # noqa: E402
from harness.electronics.curve_evidence import (  # noqa: E402
    CurveEvidence,
    evidence_class,
)

PILOT_DIR = ROOT / "tests/fixtures/gold/curve_evidence_pilot"
MCU_GOLD_DIR = ROOT / "tests/fixtures/gold/typical_curves"

GATES = {
    "plot_identification_accuracy": 1.0,
    "axis_unit_correctness": 1.0,
    "series_identification_accuracy": 0.95,
    "condition_classification_accuracy": 0.95,
    "false_comparability_rate_max": 0.0,
    "out_of_range_correct_rejection": 1.0,
}


def _canon(text: str) -> str:
    return re.sub(r"[^a-z0-9%µ]+", " ", str(text or "").lower()).strip()


def _labels_agree(expected: str, actual: str) -> bool:
    e, a = _canon(expected), _canon(actual)
    return bool(e) and (e == a or e in a or a in e)


def score_reference_integrity(spec: dict, records: dict) -> dict:
    total_plots = matched_plots = 0
    axis_total = axis_hit = 0
    series_total = series_count_hit = 0
    series_name_total = series_name_hit = 0
    condition_total = condition_hit = 0
    class_total = class_hit = 0
    misses: list[str] = []
    for page in spec["pages"]:
        record = records[page["file"]]
        plots = record.get("plots") or []
        total_plots += page["expected_figures"]
        matched_plots += len(plots)
        if len(plots) != page["expected_figures"]:
            misses.append(
                f"{page['file']}: {len(plots)} plots != "
                f"{page['expected_figures']} printed figures"
            )
        if evidence_class(record.get("section_title")) != \
                page["expected_section_class"]:
            misses.append(f"{page['file']}: section class mismatch")
        else:
            class_total += 1
            class_hit += 1
        blob = " ".join(
            " ".join((p.get("conditions_plot") or [])
                     + (p.get("conditions_page") or []))
            for p in plots
        )
        for cond in page.get("expected_page_conditions", []):
            condition_total += 1
            if _canon(cond) in _canon(blob):
                condition_hit += 1
            else:
                misses.append(f"{page['file']}: condition {cond!r} not captured")
        for want in page["plots"]:
            got = next((p for p in plots if want["title_contains"] in
                        str(p.get("title") or "")), None)
            if got is None:
                misses.append(
                    f"{page['file']}: figure {want['title_contains']!r} missing"
                )
                series_total += 1
                continue
            series_total += 1
            got_names = [str(s.get("name") or "") for s in got["series"]]
            if len(got_names) == len(want["series_names"]):
                series_count_hit += 1
            else:
                misses.append(
                    f"{page['file']} {want['title_contains']}: series count "
                    f"{len(got_names)} != {len(want['series_names'])}"
                )
            for name in want["series_names"]:
                series_name_total += 1
                if any(_labels_agree(name, g) for g in got_names):
                    series_name_hit += 1
                else:
                    misses.append(
                        f"{page['file']} {want['title_contains']}: series "
                        f"{name!r} unidentified"
                    )
            for side in ("x", "y"):
                axis_total += 2
                if _labels_agree(want[f"{side}_label"],
                                 (got["axes"].get(side) or {}).get("label")):
                    axis_hit += 1
                else:
                    misses.append(
                        f"{page['file']} {want['title_contains']}: "
                        f"{side} label mismatch"
                    )
                if _canon(want[f"{side}_unit"]) == _canon(
                    (got["axes"].get(side) or {}).get("unit")
                ):
                    axis_hit += 1
                else:
                    misses.append(
                        f"{page['file']} {want['title_contains']}: "
                        f"{side} unit mismatch"
                    )
            for cond in want.get("conditions", []):
                condition_total += 1
                found = any(
                    _canon(cond) in _canon(c)
                    for c in (got.get("conditions_plot") or [])
                )
                if found:
                    condition_hit += 1
                else:
                    misses.append(
                        f"{page['file']} {want['title_contains']}: condition "
                        f"{cond!r} not captured"
                    )
    return {
        "plot_identification_accuracy": (
            matched_plots / total_plots if total_plots else None
        ),
        "axis_unit_correctness": axis_hit / axis_total if axis_total else None,
        "series_identification_accuracy": (
            (series_count_hit + series_name_hit)
            / (series_total + series_name_total)
            if (series_total + series_name_total)
            else None
        ),
        "evidence_class_accuracy": (
            class_hit / class_total if class_total else None
        ),
        "printed_condition_capture": (
            condition_hit / condition_total if condition_total else None
        ),
        "counts": {
            "plots": total_plots,
            "series": series_total - 1 + 1,
        },
        "misses": misses,
    }


def _classify(response: dict) -> dict[str, int]:
    ok = sum(1 for r in response["results"] if r.get("status") == "ok")
    listed = sum(1 for r in response["results"] if r.get("status") == "applicable")
    not_comparable = sum(
        1 for r in response["not_usable"] if r.get("status") == "not_comparable"
    )
    out_of_range = sum(
        1 for r in response["not_usable"] if r.get("status") == "out_of_range"
    )
    insufficient = len(response["insufficient_conditions"])
    profiles = response.get("load_profiles") or []
    return {
        "ok": ok,
        "listed": listed,
        "not_comparable": not_comparable,
        "out_of_range": out_of_range,
        "insufficient": insufficient,
        "applicable": response["applicable"],
        "profile_ok": sum(1 for p in profiles if p.get("status") == "proposal"),
        "profile_rejected": sum(
            1 for p in profiles if p.get("status") in ("out_of_range", "rejected")
        ),
        "profile_not_comparable": sum(
            1 for p in profiles if p.get("status") == "not_comparable"
        ),
    }


def score_probes(spec: dict, curves: list[CurveEvidence]) -> dict:
    total = passed = 0
    must_reject = []
    false_comparable = 0
    oob_probes = []
    oob_correct = 0
    failures: list[dict] = []
    reasons: dict[str, int] = {}
    for probe in spec["probes"]:
        total += 1
        response = query_curve_evidence(curves, **probe["request"])
        got = _classify(response)
        want = probe["expect"]
        ok = True
        for key, value in want.items():
            if key == "unpinned_dimensions":
                unpinned = (
                    response.get("derived") or {}
                ).get("condition_dimensions_unpinned") or {}
                if sorted(value) != sorted(unpinned):
                    ok = False
                    failures.append({
                        "probe": probe["id"],
                        "check": key,
                        "expected": value,
                        "actual": unpinned,
                    })
                continue
            if key == "reason":
                nu = response["not_usable"] + response["insufficient_conditions"]
                actual_reason = next(
                    (r.get("reason") for r in nu if r.get("reason")), None
                )
                if actual_reason != value:
                    ok = False
                    failures.append({
                        "probe": probe["id"],
                        "check": "reason",
                        "expected": value,
                        "actual": actual_reason,
                    })
                else:
                    reasons[value] = reasons.get(value, 0) + 1
                continue
            if got.get(key) != value:
                ok = False
                failures.append({
                    "probe": probe["id"],
                    "check": key,
                    "expected": value,
                    "actual": got.get(key),
                })
        if ok:
            passed += 1
        if probe.get("must_reject"):
            must_reject.append(probe["id"])
            if got["ok"] > 0 or got["profile_ok"] > 0:
                false_comparable += 1
        if probe.get("expect_out_of_range"):
            oob_probes.append(probe["id"])
            if got["ok"] == 0 and got["out_of_range"] > 0:
                oob_correct += 1
    return {
        "probes_total": total,
        "probes_passed": passed,
        "condition_classification_accuracy": passed / total if total else None,
        "false_comparability_rate": (
            false_comparable / len(must_reject) if must_reject else 0.0
        ),
        "out_of_range_correct_rejection": (
            oob_correct / len(oob_probes) if oob_probes else None
        ),
        "must_reject_probes": len(must_reject),
        "out_of_range_probes": len(oob_probes),
        "failures": failures,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pilot-dir", type=Path, default=PILOT_DIR)
    ap.add_argument("--predictions", type=Path)
    args = ap.parse_args()

    spec = json.loads((args.pilot_dir / "_probes.json").read_text())
    records = {}
    curves: list[CurveEvidence] = []
    for page in spec["pages"]:
        path = args.pilot_dir / page["file"]
        record = json.loads(path.read_text())
        records[page["file"]] = record
        for plot in record.get("plots") or []:
            for index in range(len(plot.get("series") or [])):
                curves.append(
                    CurveEvidence.from_reference_plot(record, plot, index)
                )
    for path in sorted(MCU_GOLD_DIR.glob("*.json")):
        if path.name.startswith("_"):
            continue
        record = json.loads(path.read_text())
        for plot in record.get("plots") or []:
            for index in range(len(plot.get("series") or [])):
                curves.append(
                    CurveEvidence.from_reference_plot(record, plot, index)
                )

    integrity = score_reference_integrity(spec, records)
    probes = score_probes(spec, curves)

    extraction = {"status": "not_run", "note": "pass --predictions JSONL"}
    if args.predictions:
        from scripts.score_typical_curves_gold import _pair, load_gold, score

        gold = load_gold(MCU_GOLD_DIR) + load_gold(args.pilot_dir)
        predictions = []
        for line in args.predictions.read_text().splitlines():
            if line.strip():
                row = json.loads(line)
                predictions.append({
                    **(row.get("payload") or {}),
                    "source_path": row.get("source_path"),
                    "page_1based": row.get("page_1based"),
                    "figure_index": row.get("figure_index"),
                })
        extraction = {
            "status": "scored",
            **score(_pair(gold, predictions)),
        }

    result = {
        "pilot": {
            "files": [p["file"] for p in spec["pages"]],
            "curves_total": len(curves),
            "curves_hand_reviewed": sum(
                len(p.get("series") or [])
                for page in spec["pages"]
                for p in records[page["file"]].get("plots") or []
            ),
            "curves_mcu_gold": len(curves) - sum(
                len(p.get("series") or [])
                for page in spec["pages"]
                for p in records[page["file"]].get("plots") or []
            ),
        },
        "reference_integrity": integrity,
        "decision_grade": probes,
        "extraction_metrics": extraction,
        "gates": GATES,
    }
    print(json.dumps(result, indent=2, default=str))

    failed = []
    if integrity["plot_identification_accuracy"] is not None and \
            integrity["plot_identification_accuracy"] < GATES["plot_identification_accuracy"]:
        failed.append("plot_identification_accuracy")
    if integrity["axis_unit_correctness"] is not None and \
            integrity["axis_unit_correctness"] < GATES["axis_unit_correctness"]:
        failed.append("axis_unit_correctness")
    if integrity["series_identification_accuracy"] is not None and \
            integrity["series_identification_accuracy"] < GATES["series_identification_accuracy"]:
        failed.append("series_identification_accuracy")
    if probes["condition_classification_accuracy"] < GATES["condition_classification_accuracy"]:
        failed.append("condition_classification_accuracy")
    if probes["false_comparability_rate"] > GATES["false_comparability_rate_max"]:
        failed.append("false_comparability_rate")
    if probes["out_of_range_correct_rejection"] is not None and \
            probes["out_of_range_correct_rejection"] < GATES["out_of_range_correct_rejection"]:
        failed.append("out_of_range_correct_rejection")
    if failed:
        print(json.dumps({"gate_failures": failed}), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
