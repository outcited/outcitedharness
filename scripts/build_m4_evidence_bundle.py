#!/usr/bin/env python3
"""CURVE-05A Workstream 4 — build the frozen M4 evidence bundle.

One JSONL row per curve series, advisory-only, carrying the full versioned
contract: evidence_id, source SHA + revision, locator, applicability +
coverage_kind, phenomenon, quantity/units, conditions + missing
dimensions, operating-point result when supported, interpolation method +
uncertainty, evidence class, adjudication state, comparability verdict,
release IDs, and the full curve coordinates. Pinned fixtures only — M4
never needs M5's SQLite databases. No knife promotion, no gold admission.
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
from harness.electronics.curve_evidence import condition_sufficiency

PILOT = ROOT / "tests/fixtures/gold/curve_evidence_pilot"
OUT = ROOT / "tests/fixtures/m4-handoff"

BUNDLE_SCHEMA = "harness.m4-curve-evidence-bundle.v1"
RETRIEVAL_RELEASE = "curve-retrieval-v0-experimental"
EXTRACTION_RELEASE = "vector-reference-v1+raster-experimental-v0"


def canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), default=str).encode()


def build_rows() -> list[dict]:
    adjudication = {}
    manifest_path = ROOT / "results/curve-adjudication/packets/manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        adjudication = {p["packet_hash"]: p for p in manifest["packets"]}
    rows = []
    for path in sorted(PILOT.glob("*.json")):
        if path.name.startswith("_"):
            continue
        record = json.loads(path.read_text())
        curves = load_reference_curves([path])
        for curve in curves:
            citation = curve.citation()
            packet = adjudication.get(curve.curve_id)
            relevance = curve.relevance[0] if curve.relevance else None
            missing = condition_sufficiency(curve, {}) if relevance else []
            rows.append({
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
                        "series_index": curve.series_index,
                        "region_bbox": citation.get("region_bbox"),
                    },
                    "source_artifact": record.get("source_artifact"),
                    "manufacturer": record.get("manufacturer"),
                },
                "applicability": {
                    "family": curve.applies_to.get("part"),
                    "part": curve.applies_to.get("part"),
                    "manufacturer": curve.applies_to.get("manufacturer"),
                    "category": curve.applies_to.get("category"),
                    "coverage_kind": "primary"
                    if curve.applies_to.get("part") else "document",
                    "note": "family-level advisory evidence; never "
                            "widened to OPN guarantees",
                },
                "phenomenon": relevance["phenomenon"] if relevance else None,
                "quantity": {
                    "x": {
                        "kind": curve.x_kind,
                        "label": (curve.axes.get("x") or {}).get("label"),
                        "unit": (curve.axes.get("x") or {}).get("unit"),
                        "scale": curve.x_scale,
                    },
                    "y": {
                        "kind": curve.y_kind,
                        "label": (curve.axes.get("y") or {}).get("label"),
                        "unit": (curve.axes.get("y") or {}).get("unit"),
                    },
                },
                "conditions": curve.conditions,
                "missing_dimensions": missing,
                "supported_region": curve.supported_region,
                "resolution": curve._resolution(),
                "evidence_class": curve.evidence_class,
                "guarantee": False,
                "adjudication": {
                    "state": (packet or {}).get("adjudication_state")
                    or "human_review_pending",
                    "packet_hash": (packet or {}).get("packet_hash"),
                    "note": "machine-verified only; human approval pending"
                    if not (packet or {}).get("adjudication_state")
                    else None,
                },
                "operating_point_query": {
                    "supported": True,
                    "method": "linear_interpolation_on_digitized_polyline"
                    if curve.x_scale == "linear" else
                    "log10_interpolation_on_digitized_polyline",
                    "bounds": "strictly inside sampled support; "
                              "extrapolation refused",
                    "example": _example_query(curve),
                },
                "uncertainty": curve.uncertainty,
                "comparability": {
                    "verdict": "condition_aware",
                    "rules": "comparable only when stated conditions "
                             "agree; missing conditions are never "
                             "defaulted; typical is never guaranteed",
                },
                "release_ids": {
                    "extraction": EXTRACTION_RELEASE,
                    "retrieval": RETRIEVAL_RELEASE,
                    "bundle": None,  # stamped below
                },
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
            })
    return rows


def _example_query(curve) -> dict | None:
    if not curve.points or not curve.supported_region:
        return None
    mid = (curve.supported_region["min"] + curve.supported_region["max"]) / 2
    return {
        "query": {"x": mid, "unit":
                  (curve.axes.get("x") or {}).get("unit")},
        "note": "evaluatable via bundle points + documented method; "
                "M4 may recompute without M5 services",
    }


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    rows = build_rows()
    release_digest = hashlib.sha256(
        b"".join(canonical(r) for r in rows)
    ).hexdigest()[:12]
    release_id = f"curve-evidence-bundle-v1-{release_digest}"
    for row in rows:
        row["release_ids"]["bundle"] = release_id
    bundle = OUT / "curve_evidence_bundle.jsonl"
    with bundle.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False,
                                    default=str) + "\n")
    manifest = {
        "schema": "harness.m4-curve-evidence-release.v1",
        "release_id": release_id,
        "advisory_only": True,
        "rows": len(rows),
        "bundle_sha256": hashlib.sha256(bundle.read_bytes()).hexdigest(),
        "adjudication_states": {
            "human_approved": 0,
            "machine_verified_or_pending": len(rows),
        },
        "laws": [
            "no knife promotion; no automatic gold admission",
            "typical curves never establish guaranteed limits",
            "missing conditions are never defaulted",
            "comparability requires explicitly matching conditions",
        ],
        "consuming": "pinned fixture; no M5 services or databases needed; "
                     "recompute operating points from row.curve.points "
                     "with row.operating_point_query.method",
        "built_at": time.time(),
    }
    (OUT / "release_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
    )
    print(json.dumps({"release_id": release_id, "rows": len(rows),
                      "bundle": str(bundle)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
