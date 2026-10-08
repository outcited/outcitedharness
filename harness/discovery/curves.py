"""Curve evidence provider for the Intent-to-Knife discovery service.

Serves the PRD-CURVE-02 integration contract on top of
``harness.electronics.curve_evidence``: given a phenomenon (or quantity)
and an engineer operating point with stated conditions, return the
applicable curves, matched conditions, supported regions, computed values
with uncertainty, citations, and — for every curve that cannot answer —
the reason. Read-only; loads curve evidence from a JSONL file of frozen
pilot fixtures or upgraded extraction rows.

Promotion law: this provider is EVIDENCE ONLY. Nothing it returns enters
the elimination ledger or becomes a hard knife; the response says so
explicitly, and ``promotion: none`` is a constant, not a configuration.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from harness.electronics.curve_evidence import (
    CURVE_EVIDENCE_SCHEMA,
    CurveEvidence,
    PHENOMENA,
    compare_at_operating_point,
    condition_sufficiency,
    evaluate_load_distribution,
    query_operating_point,
)

PROVIDER_SCHEMA = "harness.discovery-curve-evidence-provider.v1"


def load_reference_curves(paths: Sequence[Path]) -> list[CurveEvidence]:
    """Curve evidence from frozen reference/gold fixture JSON files."""

    curves: list[CurveEvidence] = []
    for path in paths:
        record = json.loads(Path(path).read_text())
        for plot in record.get("plots") or []:
            for index in range(len(plot.get("series") or [])):
                curves.append(
                    CurveEvidence.from_reference_plot(record, plot, index)
                )
    return curves


def load_extraction_curves(path: Path) -> list[CurveEvidence]:
    """Curve evidence from wave-A ``extract_typical_curves`` JSONL rows."""

    curves: list[CurveEvidence] = []
    for line in Path(path).read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        curves.extend(CurveEvidence.from_extraction_row(row))
    return curves


def _phenomena_matching(
    phenomenon: str | None,
    quantity: str | None,
    use_case: str | None,
) -> set[str]:
    names: set[str] = set()
    if phenomenon:
        if phenomenon not in PHENOMENA:
            raise ValueError(f"unknown phenomenon {phenomenon!r}")
        names.add(phenomenon)
    if quantity:
        for name, spec in PHENOMENA.items():
            if name.split("_vs_")[0].startswith(quantity) or quantity in name:
                names.add(name)
    if use_case:
        for name, spec in PHENOMENA.items():
            if use_case in spec["use_cases"]:
                names.add(name)
    if not names:
        names = set(PHENOMENA)
    return names


def query_curve_evidence(
    curves: Sequence[CurveEvidence],
    *,
    phenomenon: str | None = None,
    quantity: str | None = None,
    use_case: str | None = None,
    operating_point: Mapping[str, Any] | None = None,
    conditions: Mapping[str, Any] | None = None,
    load_profile: Sequence[Mapping[str, Any]] | None = None,
    category: str | None = None,
) -> dict[str, Any]:
    """The R7 evidence-query contract.

    operating_point: {"x": <value>} on the curve's x quantity (the
    phenomenon's sweep variable). conditions: typed keys + optional
    ``categorical`` map stated by the engineer — never defaulted.
    load_profile: optional [{x, weight}] evaluated per applicable curve.
    """

    phenomena = _phenomena_matching(phenomenon, quantity, use_case)
    applicable: list[CurveEvidence] = []
    for curve in curves:
        if not curve.relevance:
            continue
        if not ({t["phenomenon"] for t in curve.relevance} & phenomena):
            continue
        if category and category not in {
            c for t in curve.relevance for c in t["categories"]
        }:
            continue
        applicable.append(curve)

    results: list[dict[str, Any]] = []
    not_usable: list[dict[str, Any]] = []
    insufficient: list[dict[str, Any]] = []
    x_value = float((operating_point or {}).get("x")) if (
        operating_point or {}
    ).get("x") is not None else None
    for curve in applicable:
        missing = condition_sufficiency(curve, conditions)
        if missing:
            insufficient.append({
                "curve_id": curve.curve_id,
                "part": curve.applies_to.get("part"),
                "reason": "condition_missing",
                "missing_required_keys": missing,
                "engineer_params": sorted({
                    p for t in curve.relevance
                    for p in PHENOMENA[t["phenomenon"]]["engineer_params"]
                }),
            })
            continue
        if x_value is None and not load_profile:
            results.append({
                "status": "applicable",
                "curve_id": curve.curve_id,
                "part": curve.applies_to.get("part"),
                "series_name": curve.series.get("name"),
                "citation": curve.citation(),
                "matched_conditions": {
                    **(curve.conditions.get("keys") or {}),
                    **{
                        f"categorical.{k}": v
                        for k, v in (curve.conditions.get("categorical")
                                     or {}).items()
                    },
                },
                "supported_region": curve.supported_region,
                "resolution": curve._resolution(),
                "evidence_class": curve.evidence_class,
                "relevance": curve.relevance,
            })
            continue
        query = query_operating_point(
            curve, x_value if x_value is not None else 0.0,
            required_conditions=conditions,
        ) if x_value is not None else None
        if query is not None:
            if query["status"] == "ok":
                results.append({
                    "status": "ok",
                    "curve_id": curve.curve_id,
                    "part": curve.applies_to.get("part"),
                    "series_name": curve.series.get("name"),
                    "x": query["x"],
                    "value": query["value"],
                    "unit": query["unit"],
                    "guarantee": query["guarantee"],
                    "evidence_class": query["evidence_class"],
                    "matched_conditions": query["matched_conditions"],
                    "supported_region": query["supported_region"],
                    "uncertainty": query["uncertainty"],
                    "citation": query["citation"],
                    "relevance": curve.relevance,
                })
            elif query["status"] == "out_of_range":
                not_usable.append({
                    "curve_id": curve.curve_id,
                    "part": curve.applies_to.get("part"),
                    "status": "out_of_range",
                    "reason": query["reason"],
                    "requested": query.get("requested"),
                    "supported_region": curve.supported_region,
                    "citation": query["citation"],
                })
            else:
                not_usable.append({
                    "curve_id": curve.curve_id,
                    "part": curve.applies_to.get("part"),
                    "status": query["status"],
                    "reason": query.get("reason"),
                    "compatibility": query.get("compatibility"),
                    "citation": query["citation"],
                })
    derived: dict[str, Any] | None = None
    profile_results: list[dict[str, Any]] = []
    if x_value is not None and results:
        ok_curves = [c for c in applicable if any(
            r["curve_id"] == c.curve_id and r.get("status") == "ok"
            for r in results
        )]
        derived = compare_at_operating_point(
            ok_curves, x_value, required_conditions=conditions
        )
    if load_profile:
        for curve in applicable:
            if condition_sufficiency(curve, conditions):
                continue
            profile_results.append(
                evaluate_load_distribution(
                    curve, load_profile, required_conditions=conditions
                )
            )
    return {
        "schema": PROVIDER_SCHEMA,
        "phenomena": sorted(phenomena),
        "applicable": len(applicable),
        "results": results,
        "not_usable": not_usable,
        "insufficient_conditions": insufficient,
        "derived": derived,
        "load_profiles": profile_results or None,
        "promotion": "none",
        "promotion_note": (
            "curve evidence is advisory; it never enters the elimination "
            "ledger and never becomes a hard knife without the aisle "
            "qualification policy"
        ),
    }


__all__ = [
    "PROVIDER_SCHEMA",
    "load_extraction_curves",
    "load_reference_curves",
    "query_curve_evidence",
]
