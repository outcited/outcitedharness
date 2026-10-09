"""CURVE-08 R3 — evidence-grounded engineering decision engine.

Composes, in order and never out of order:

  1. candidate identity (R1)
  2. hard eligibility from ratings (R2)
  3. curve-evidence availability per candidate
  4. condition-matched comparison via the CURVE-07E comparator (R3)
  5. measured typical performance with interpolation disclosure
  6. unknown / missing / incompatible reporting
  7. review-state disclosure (machine-verified vs approved)

The five comparison levels of the CURVE-07E coverage matrix are preserved
and reported per answer; they are never merged into one "comparable"
flag. Approximate or scenario comparisons are a SEPARATE, explicitly
labeled list — condition-matched evidence is never diluted.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from harness.electronics.candidates import (
    Candidate,
    build_power_candidates,
    candidate_counts,
    load_default_inputs,
)
from harness.electronics.curve_evidence import (
    condition_compatibility,
    curve_from_bundle_row,
    query_operating_point,
)
from harness.electronics.eligibility import (
    evaluate_cohort,
    thermal_feasibility_note,
)

DECISION_SCHEMA = "harness.electronics-decision-answer.v1"

_LEVEL_ORDER = ("same_configuration", "within_device_configuration",
                "within_family", "cross_family", "cross_manufacturer")


def _bundle_rows(repo_root: Path) -> list[dict]:
    path = repo_root / "tests/fixtures/m4-handoff/" \
        "curve_evidence_bundle_v3.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()
            if line.strip()]


def _manifest(repo_root: Path) -> dict:
    path = repo_root / "tests/fixtures/m4-handoff/" \
        "curve_evidence_bundle_v3_manifest.json"
    return json.loads(path.read_text())


def _rows_for_candidate(candidate: Candidate, rows: Sequence[dict],
                        phenomenon: str) -> list[dict]:
    out = []
    for row in rows:
        if row.get("phenomenon") != phenomenon:
            continue
        app = row.get("applicability") or {}
        if candidate.device and app.get("part") == candidate.device:
            out.append(row)
        elif (not candidate.device and candidate.family
              and app.get("family_group") == candidate.family):
            out.append(row)
    return out


def _bracketing_points(row: Mapping[str, Any], x: float) -> list[list]:
    pts = sorted((row.get("curve") or {}).get("points") or [],
                 key=lambda p: p["x"])
    for a, b in zip(pts, pts[1:]):
        if a["x"] <= x <= b["x"]:
            return [a, b]
    return []


def _evidence_availability(candidate: Candidate, rows: Sequence[dict],
                           question: Mapping[str, Any]) -> dict[str, Any]:
    phenomenon = question.get("phenomenon") or "efficiency_vs_load"
    cand_rows = _rows_for_candidate(candidate, rows, phenomenon)
    if not cand_rows:
        return {"state": "missing",
                "reason": "no curve evidence for this candidate at this "
                          "phenomenon",
                "rows": 0}
    op = question.get("operating_point") or {}
    x = op.get("x")
    comparable, non_comparable = [], []
    for row in cand_rows:
        if x is None:
            non_comparable.append({
                "evidence_id": row["evidence_id"],
                "reason": "no operating point supplied",
            })
            continue
        result = query_operating_point(
            curve_from_bundle_row(row), float(x),
            required_conditions=question.get("conditions"),
            x_unit=op.get("unit"),
        )
        if result["status"] == "ok":
            comparable.append({"row": row, "result": result})
        else:
            non_comparable.append({
                "evidence_id": row["evidence_id"],
                "reason": result.get("reason"),
            })
    if comparable:
        return {"state": "comparable", "comparable": comparable,
                "non_comparable": non_comparable, "rows": len(cand_rows)}
    return {"state": "non_comparable", "comparable": [],
            "non_comparable": non_comparable, "rows": len(cand_rows)}


def _comparison_level(entries: Sequence[dict]) -> dict[str, Any]:
    """Which of the five published levels does THIS comparison achieve?"""

    families = {e["family"] for e in entries if e.get("family")}
    manufacturers = {e["manufacturer"] for e in entries
                     if e.get("manufacturer")}
    devices = {e["part"] for e in entries if e.get("part")}
    if len(manufacturers) >= 2:
        level = "cross_manufacturer"
    elif len(families) >= 2:
        level = "cross_family"
    elif len(devices) >= 2:
        level = "within_family"
    elif len({(e["part"], e.get("series")) for e in entries}) >= 2:
        level = "within_device_configuration"
    else:
        level = "same_configuration"
    return {
        "achieved_level": level,
        "levels_present": [lv for lv in _LEVEL_ORDER
                           if _LEVEL_ORDER.index(lv)
                           <= _LEVEL_ORDER.index(level)],
        "families": sorted(f for f in families if f),
        "manufacturers": sorted(m for m in manufacturers if m),
        "devices": sorted(d for d in devices if d),
        "law": "levels are reported, never merged into one comparable flag",
    }


def answer_question(
    question: Mapping[str, Any],
    *,
    repo_root: Optional[Path] = None,
    candidates: Optional[Sequence[Candidate]] = None,
    rows: Optional[Sequence[dict]] = None,
) -> dict[str, Any]:
    repo_root = Path(repo_root or Path(__file__).resolve().parents[2])
    if candidates is None or rows is None:
        catalog, bundle, packets, ratings = load_default_inputs(repo_root)
        candidates = list(build_power_candidates(
            catalog, bundle, packets, ratings).values())
        rows = rows if rows is not None else bundle
    rows = list(rows) if rows is not None else _bundle_rows(repo_root)
    manifest = _manifest(repo_root)

    requirements = {k: v for k, v in
                    (question.get("requirements") or {}).items()
                    if v is not None}
    eligibility = evaluate_cohort(candidates, requirements)

    entries: list[dict[str, Any]] = []
    approximate_entries: list[dict[str, Any]] = []
    availability: dict[str, dict] = {}
    ineligible: list[dict[str, Any]] = []
    unknown: list[dict[str, Any]] = []
    for candidate in candidates:
        verdict = eligibility[candidate.candidate_id]
        if verdict["verdict"] == "ineligible":
            ineligible.append({
                "candidate": candidate.candidate_id,
                "part": candidate.device or candidate.opn,
                "family": candidate.family,
                "grain": candidate.grain,
                "rules": [r for r in verdict["rules"]
                          if r["verdict"] == "ineligible"],
            })
            continue
        avail = _evidence_availability(candidate, rows, question)
        availability[candidate.candidate_id] = {
            "state": avail["state"],
            "rows": avail.get("rows", 0),
            "refusals": avail.get("non_comparable", [])[:4],
        }
        if verdict["verdict"] == "unknown":
            unknown.append({
                "candidate": candidate.candidate_id,
                "part": candidate.device or candidate.opn,
                "missing_ratings": [
                    r["rule"] for r in verdict["rules"]
                    if r["verdict"] == "unknown"],
                "note": "electrically unconfirmed; retained, never "
                        "eliminated",
            })
        for item in avail.get("comparable", []):
            row, result = item["row"], item["result"]
            entry = {
                "candidate": candidate.candidate_id,
                "part": candidate.device or row["applicability"].get("part"),
                "family": row["applicability"].get("family_group"),
                "manufacturer": row["applicability"].get("manufacturer"),
                "series": row["curve"].get("series_name"),
                "value": result["value"],
                "unit": result["unit"],
                "evidence_id": row["evidence_id"],
                "evidence_class": result["evidence_class"],
                "guarantee": False,
                "adjudication_state": (row.get("adjudication") or {})
                .get("state"),
                "matched_conditions": result["matched_conditions"],
                "interpolation": {
                    "method": (row.get("operating_point_query") or {})
                    .get("method"),
                    "supporting_points": _bracketing_points(
                        row, result["x"]),
                    "operating_point": result["x"],
                    "conditions": question.get("conditions"),
                    "uncertainty": result["uncertainty"],
                    "limitations": [
                        "typical printed-curve value, not a guarantee",
                        "vendor evaluation configuration unless the "
                        "series legend states otherwise",
                    ],
                },
                "source": row["source"],
                "eligibility_verdict": verdict["verdict"],
                "thermal": thermal_feasibility_note(candidate),
            }
            entries.append(entry)
    if question.get("approximate"):
        # scenario mode: also surface condition-mismatched evidence,
        # explicitly labeled, with the mismatched dimensions listed
        for candidate in candidates:
            if eligibility[candidate.candidate_id]["verdict"] == \
                    "ineligible":
                continue
            for row in _rows_for_candidate(
                    candidate, rows,
                    question.get("phenomenon") or "efficiency_vs_load"):
                compat = condition_compatibility(
                    row.get("conditions"), question.get("conditions"))
                if compat["status"] == "comparable":
                    continue
                approximate_entries.append({
                    "candidate": candidate.candidate_id,
                    "part": candidate.device,
                    "family": row["applicability"].get("family_group"),
                    "manufacturer": row["applicability"].get(
                        "manufacturer"),
                    "evidence_id": row["evidence_id"],
                    "mode": "approximate_scenario",
                    "mismatch": compat.get("mismatched"),
                    "missing": compat.get("missing"),
                    "reason": compat.get("reason"),
                    "value": None,
                    "guarantee": False,
                    "note": "scenario comparison requested by the "
                            "engineer; NOT condition-matched evidence",
                })

    best_per_candidate = {}
    for entry in entries:
        cur = best_per_candidate.get(entry["candidate"])
        if cur is None or (entry["value"] or 0) > (cur["value"] or 0):
            best_per_candidate[entry["candidate"]] = entry
    ranking = sorted(best_per_candidate.values(),
                     key=lambda e: -(e["value"] or 0.0))

    comp_map = {cid: ("comparable" if a["state"] == "comparable"
                      else "non_comparable" if a["state"]
                      == "non_comparable" else "missing")
                for cid, a in availability.items()}
    counts = candidate_counts(candidates, eligibility, comp_map)

    return {
        "schema": DECISION_SCHEMA,
        "question": dict(question),
        "evidence_release": manifest["release_id"],
        "bundle_sha256": manifest["bundle_sha256"],
        "counts": counts,
        "hard_eligibility": {
            "eligible": [c for c, v in eligibility.items()
                         if v["verdict"] == "eligible"],
            "ineligible": ineligible,
            "unknown": unknown,
        },
        "evidence_availability": availability,
        "comparison": {
            "condition_matched_entries": ranking,
            "comparison_level": _comparison_level(ranking),
            "approximate_scenario_entries": approximate_entries
            if question.get("approximate") else [],
        },
        "laws": [
            "hard eligibility precedes every curve comparison",
            "missing ratings are unknown, never failure",
            "typical values never guarantee performance",
            "interpolation only inside the digitized domain",
            "approximate scenarios are labeled separately",
        ],
    }


__all__ = ["DECISION_SCHEMA", "answer_question"]
