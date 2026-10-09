"""Experimental curve-evidence retrieval integration (CURVE-04 R4).

A standalone, flag-gated index over qualified curve evidence. It borrows
the evidence-unit store's LAWS (provenance is mandatory; verification is
recorded, never minted here; applicability is never widened) but keeps its
own database so the PRD-SEARCH-01 release (`search-release-v1-…`) is
untouched: no writes to search_index.db, no contract weakening on either
side.

Feature flag: ``CURVE_RETRIEVAL_ENABLED=1`` (or ``enable=True`` in code).
Disabled, every query returns an explicit not_enabled refusal — the
integration cannot leak into production paths by default.

Match classes (explicit, never silently upgraded):

- ``exact``                 — typed conditions equal, operating point on a
                              printed sample
- ``condition_compatible``  — conditions comparable, point inside support
                              (interpolated estimate carries uncertainty)
- ``interpolated``          — compatible with distance-to-sample > 0
- ``not_comparable``        — with reason (condition_mismatch/missing/
                              conflict/out_of_range/unit_incompatible)
- ``missing``               — no curve for the family/quantity at all

Records without complete provenance are refused at INDEX time
(``provenance_refused``), not silently filtered at query time.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Mapping, Sequence

from harness.discovery.curves import load_reference_curves
from harness.electronics.curve_evidence import (
    CurveEvidence,
    condition_compatibility,
    quantity_kind,
    query_operating_point,
)

RETRIEVAL_SCHEMA = "harness.electronics-curve-retrieval.v1"

SCHEMA = """
CREATE TABLE IF NOT EXISTS curves (
    curve_id TEXT PRIMARY KEY,
    packet_hash TEXT,
    category TEXT,
    family TEXT,
    part TEXT,
    manufacturer TEXT,
    phenomenon TEXT,
    x_kind TEXT,
    y_kind TEXT,
    x_unit TEXT,
    y_unit TEXT,
    supported_min REAL,
    supported_max REAL,
    conditions_json TEXT NOT NULL,
    evidence_class TEXT,
    adjudication_state TEXT NOT NULL,
    provenance_json TEXT NOT NULL,
    points_json TEXT NOT NULL,
    axes_json TEXT NOT NULL,
    indexed_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_curves_part ON curves(part);
CREATE INDEX IF NOT EXISTS idx_curves_phenomenon ON curves(phenomenon);
"""

_REQUIRED_PROVENANCE = ("document_sha256", "page_1based", "figure_index",
                        "series_index")


def flag_enabled(explicit: bool | None = None) -> bool:
    if explicit is not None:
        return explicit
    return os.environ.get("CURVE_RETRIEVAL_ENABLED", "") == "1"


def provenance_complete(curve: CurveEvidence) -> bool:
    return all(getattr(curve, field.split(".")[0], None) is not None
               or (curve.citation() or {}).get(field)
               for field in _REQUIRED_PROVENANCE) and \
        bool(curve.document_sha256)


def build_index(
    db_path: Path,
    pilot_dir: Path,
    adjudication_manifest: Path | None = None,
) -> dict[str, int]:
    """Index qualified curves. Adjudication states come from the immutable
    ledger manifest when supplied; unlisted packets stay
    machine/human_review_pending — never approved."""

    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    con.executescript(SCHEMA)
    states: dict[str, str] = {}
    if adjudication_manifest and Path(adjudication_manifest).exists():
        manifest = json.loads(Path(adjudication_manifest).read_text())
        states = {p["packet_hash"]: "human_review_pending"
                  for p in manifest.get("packets", [])}
    stats = {"indexed": 0, "provenance_refused": 0,
             "no_relevance": 0}
    import time

    for path in sorted(Path(pilot_dir).glob("*.json")):
        if path.name.startswith("_"):
            continue
        record = json.loads(path.read_text())
        for plot in record.get("plots") or []:
            for index in range(len(plot.get("series") or [])):
                curve = CurveEvidence.from_reference_plot(
                    record, plot, index
                )
                if not provenance_complete(curve):
                    stats["provenance_refused"] += 1
                    continue
                for tag in curve.relevance or [None]:
                    con.execute(
                        "INSERT OR REPLACE INTO curves VALUES "
                        "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            curve.curve_id,
                            None,
                            curve.applies_to.get("category"),
                            curve.applies_to.get("family_group")
                            or curve.applies_to.get("part"),
                            curve.applies_to.get("part"),
                            curve.applies_to.get("manufacturer"),
                            tag["phenomenon"] if tag else None,
                            curve.x_kind,
                            curve.y_kind,
                            (curve.axes.get("x") or {}).get("unit"),
                            (curve.axes.get("y") or {}).get("unit"),
                            curve.supported_region["min"]
                            if curve.supported_region else None,
                            curve.supported_region["max"]
                            if curve.supported_region else None,
                            json.dumps(curve.conditions,
                                       ensure_ascii=False),
                            curve.evidence_class,
                            "machine_verified",
                            json.dumps(curve.citation(),
                                       ensure_ascii=False),
                            json.dumps(curve.points),
                            json.dumps(curve.axes, ensure_ascii=False),
                            time.time(),
                        ),
                    )
                    if tag:
                        stats["indexed"] += 1
                    break  # one row per curve; primary phenomenon in row
                if not curve.relevance:
                    stats["no_relevance"] += 1
    con.commit()
    con.close()
    return stats


def _conditions_of(row: sqlite3.Row) -> dict[str, Any]:
    return json.loads(row["conditions_json"])


def search_curves(
    db_path: Path,
    *,
    filters: Mapping[str, Any] | None = None,
    operating_point: float | None = None,
    conditions: Mapping[str, Any] | None = None,
    x_unit: str | None = None,
    limit: int = 50,
    enable: bool | None = None,
) -> dict[str, Any]:
    """Query the curve index. Refuses (never silently degrades) when the
    flag is off, provenance is incomplete, or conditions mismatch."""

    base = {"schema": RETRIEVAL_SCHEMA}
    if not flag_enabled(enable):
        return {**base, "status": "not_enabled",
                "reason": "CURVE_RETRIEVAL_ENABLED != 1"}
    if not Path(db_path).exists():
        return {**base, "status": "missing_index",
                "reason": f"no curve index at {db_path}; build_index first"}
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    filters = dict(filters or {})
    where: list[str] = []
    params: list[Any] = []
    for key in ("category", "family", "part", "manufacturer",
                "phenomenon", "y_kind", "x_kind"):
        if filters.get(key):
            where.append(f"{key} = ?")
            params.append(filters[key])
    if filters.get("review_status"):
        where.append("adjudication_state = ?")
        params.append(filters["review_status"])
    sql = "SELECT * FROM curves" + \
        (" WHERE " + " AND ".join(where) if where else "")
    rows = con.execute(sql, params).fetchall()
    con.close()

    results: list[dict[str, Any]] = []
    for row in rows[:limit]:
        entry: dict[str, Any] = {
            "curve_id": row["curve_id"],
            "part": row["part"],
            "family": row["family"],
            "family_scoped": row["part"] is None,
            "phenomenon": row["phenomenon"],
            "supported_region": [row["supported_min"],
                                 row["supported_max"]],
            "evidence_class": row["evidence_class"],
            "adjudication_state": row["adjudication_state"],
            "provenance": json.loads(row["provenance_json"]),
            "guarantee": False,
        }
        if operating_point is None:
            entry["match_class"] = "retrieved"
            results.append(entry)
            continue
        compat = condition_compatibility(
            _conditions_of(row), conditions,
            x_kind=row["x_kind"],
            x_value=float(operating_point),
        )
        if compat["status"] != "comparable":
            results.append({**entry, "match_class": "not_comparable",
                            "reason": compat["reason"],
                            "compatibility": compat})
            continue
        region = row["supported_min"], row["supported_max"]
        if region[0] is None:
            results.append({**entry, "match_class": "not_comparable",
                            "reason": "no_supported_region"})
            continue
        curve = _evidence_from_row(row)
        outcome = query_operating_point(
            curve, float(operating_point),
            required_conditions=conditions, x_unit=x_unit,
        )
        if outcome["status"] != "ok":
            results.append({**entry, "match_class": "not_comparable",
                            "reason": outcome.get("reason")})
            continue
        distance = outcome["uncertainty"].get(
            "distance_to_nearest_sample"
        ) or 0.0
        entry.update({
            "match_class": "exact" if distance == 0.0 else "interpolated",
            "value": outcome["value"],
            "unit": row["y_unit"],
            "uncertainty": outcome["uncertainty"],
            "matched_conditions": outcome["matched_conditions"],
            "note": "typical printed-curve value — advisory, never a "
                    "guaranteed limit",
        })
        results.append(entry)
    return {
        **base,
        "status": "ok",
        "filters": filters,
        "operating_point": operating_point,
        "conditions": dict(conditions or {}),
        "results": results,
        "counts": {
            "retrieved": sum(1 for r in results
                             if r["match_class"] == "retrieved"),
            "exact": sum(1 for r in results if r["match_class"] == "exact"),
            "interpolated": sum(1 for r in results
                                if r["match_class"] == "interpolated"),
            "not_comparable": sum(1 for r in results
                                  if r["match_class"] == "not_comparable"),
        },
    }


def _evidence_from_row(row: sqlite3.Row) -> CurveEvidence:
    """Reconstruct a queryable CurveEvidence from the indexed row — the
    index is self-contained; the pilot fixtures are not needed at query
    time."""

    prov = json.loads(row["provenance_json"])
    return CurveEvidence(
        document_sha256=prov["document_sha256"],
        page_1based=prov["page_1based"],
        figure_index=prov["figure_index"],
        series_index=prov["series_index"],
        caption=prov.get("caption"),
        region_bbox=prov.get("region_bbox"),
        figure_revision=prov.get("figure_revision"),
        axes=json.loads(row["axes_json"]),
        series={"name": prov.get("series_name"),
                "points": json.loads(row["points_json"])},
        conditions_verbatim=(prov.get("conditions_verbatim") or []),
        evidence_class_=row["evidence_class"] or "unspecified",
        applies_to={"part": row["part"], "category": row["category"],
                    "manufacturer": row["manufacturer"]},
        uncertainty={"method": "indexed_reference"},
        verification={"status": "reference"},
    )


__all__ = [
    "RETRIEVAL_SCHEMA",
    "build_index",
    "flag_enabled",
    "provenance_complete",
    "search_curves",
]
