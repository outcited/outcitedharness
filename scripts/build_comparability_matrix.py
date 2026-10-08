#!/usr/bin/env python3
"""CURVE-05B — cross-family comparability matrix.

Which operating points and engineering scenarios have enough
condition-compatible evidence for meaningful comparison? Built ONLY from
the frozen, SHA-verified M4 bundle (no M5 services): curves are grouped
into comparison cells by (phenomenon, typed condition cell); a cell
supports a cross-family comparison when >= 2 families have
condition-compatible evidence with overlapping supported regions.
Everything else is listed as blocked, with the reason.
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BUNDLE = ROOT / "tests/fixtures/m4-handoff/curve_evidence_bundle.jsonl"
MANIFEST = ROOT / "tests/fixtures/m4-handoff/release_manifest.json"
OUT = ROOT / "results/curve-05b"

MATRIX_SCHEMA = "harness.electronics-curve-comparability-matrix.v1"


def load_verified_bundle() -> tuple[list[dict], dict]:
    manifest = json.loads(MANIFEST.read_text())
    digest = hashlib.sha256(BUNDLE.read_bytes()).hexdigest()
    if digest != manifest["bundle_sha256"]:
        raise SystemExit("bundle SHA mismatch — refusing to build on "
                         "unverified evidence")
    rows = [json.loads(line) for line in BUNDLE.read_text().splitlines()
            if line.strip()]
    return rows, manifest


from harness.electronics.curve_evidence import PHENOMENA

# cell identity uses engineer-matchable dimensions only (the phenomenon's
# required keys plus temperature); device-rating-like keys (e.g. iout_a
# from "SiC461 (10 A)" headers) are identity CONTEXT, not test
# conditions. They ride every series row, and query-time compatibility
# still applies the full CURVE-02 law against whatever the engineer pins.
_TEMP_KEYS = ("ta_c", "tj_c", "t_c", "tvj_c", "tc_c")
_RAIL_KEYS = ("vin_v", "vout_v")  # engineer-matchable in every phenomenon
_CONTEXT_KEYS = ("iout_a", "id_a", "vgs_on_v")


def condition_cell(row: dict) -> tuple[tuple, dict]:
    keys = (row.get("conditions") or {}).get("keys") or {}
    categorical = (row.get("conditions") or {}).get("categorical") or {}
    phenomenon = row.get("phenomenon") or ""
    required = set(PHENOMENA.get(phenomenon, {}).get(
        "required_condition_keys") or [])
    identity, context = [], {}
    for key in sorted(keys):
        if key in _CONTEXT_KEYS:
            context[key] = keys[key]
            continue
        if key in required or key in _TEMP_KEYS or key in _RAIL_KEYS:
            identity.append((key, keys[key]))
        else:
            context[key] = keys[key]
    for key in sorted(categorical):
        if key == "mode":
            identity.append((f"c:{key}", categorical[key]))
        else:
            context[f"c:{key}"] = categorical[key]
    return tuple(identity), context


def region_overlap(regions: list[dict]) -> dict | None:
    lows = [r["min"] for r in regions if r]
    highs = [r["max"] for r in regions if r]
    if not lows or not highs:
        return None
    lo, hi = max(lows), min(highs)
    return {"min": lo, "max": hi} if hi > lo else None


def build() -> dict:
    rows, manifest = load_verified_bundle()
    cells: dict[tuple, dict] = defaultdict(lambda: {
        "phenomenon": None, "conditions": None,
        "families": defaultdict(list),
    })
    unclassified = []
    for row in rows:
        phenomenon = row.get("phenomenon")
        if not phenomenon:
            unclassified.append(row["evidence_id"])
            continue
        cell, context = condition_cell(row)
        key = (phenomenon, cell)
        cells[key]["phenomenon"] = phenomenon
        cells[key]["conditions"] = dict(cell)
        member = {
            "evidence_id": row["evidence_id"],
            "series_name": row["curve"]["series_name"],
            "supported_region": row["supported_region"],
            "evidence_class": row["evidence_class"],
            "adjudication_state": row["adjudication"]["state"],
            "guarantee": row["guarantee"],
            "context_conditions": context,
        }
        if (row.get("conditions") or {}).get("conflict"):
            # the row's own printed conditions conflict (e.g. a figure
            # mixing 12 V and 24 V legend curves with one unbound trace):
            # query-time law refuses it; it cannot support a comparison
            # cell either — recorded, never silently dropped
            cells[key].setdefault(
                "conflicted", []).append({**member,
                                          "conflict": row["conditions"]
                                          ["conflict"]})
            continue
        cells[key]["families"][row["applicability"]["family"]].append(
            member
        )

    comparable, single_family = [], []
    for (phenomenon, cell), entry in cells.items():
        families = entry["families"]
        regions = [s["supported_region"]
                   for members in families.values() for s in members]
        overlap = region_overlap(regions)
        record = {
            "phenomenon": phenomenon,
            "conditions": entry["conditions"],
            "families": {fam: len(members)
                         for fam, members in families.items()},
            "shared_support": overlap,
            "series": {fam: members for fam, members in families.items()},
            "conflicted_members": entry.get("conflicted", []),
            "legend_capture_ambiguity": [
                "figures whose page siblings print legend conditions "
                "(VIN = 12 V / 24 V, L = .. uH) but whose own legend rows "
                "were not captured rely on the page-default conditions; "
                "cold-review packets ask reviewers to confirm the legend "
                "before trusting the cell",
            ] if entry.get("conflicted") else [],
            "advisory_only": True,
        }
        if len(families) >= 2 and overlap:
            comparable.append(record)
        elif len(families) == 1:
            single_family.append(record)
    comparable.sort(key=lambda r: (-len(r["families"]),
                                   r["phenomenon"]))
    single_family.sort(key=lambda r: r["phenomenon"])

    blocked = []
    for row in rows:
        if row.get("phenomenon"):
            continue
        blocked.append({
            "evidence_id": row["evidence_id"],
            "family": row["applicability"]["family"],
            "reason": "phenomenon_unclassified — not comparable until "
                      "classified",
        })

    return {
        "schema": MATRIX_SCHEMA,
        "bundle_release": manifest["release_id"],
        "bundle_sha256": manifest["bundle_sha256"],
        "counts": {
            "rows": len(rows),
            "comparison_cells": len(cells),
            "cross_family_comparable_cells": len(comparable),
            "single_family_cells": len(single_family),
            "unclassified_rows": len(unclassified),
        },
        "cross_family_scenarios": comparable,
        "single_family_scenarios": single_family,
        "blocked": blocked,
        "laws": [
            "cells require condition compatibility — a missing or "
            "mismatched condition places a curve in a different cell, "
            "never into the comparison",
            "shared_support is the intersection of sampled supports; "
            "comparisons outside it are refused",
            "all rows advisory; typical never becomes a guarantee",
        ],
    }


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    matrix = build()
    path = OUT / "comparability_matrix.json"
    path.write_text(json.dumps(matrix, indent=2, ensure_ascii=False,
                               default=str) + "\n")
    print(json.dumps({
        "release": matrix["bundle_release"],
        **matrix["counts"],
        "scenarios": [
            {"phenomenon": s["phenomenon"],
             "conditions": {k: v for k, v in s["conditions"].items()},
             "families": s["families"],
             "shared_support": s["shared_support"]}
            for s in matrix["cross_family_scenarios"]
        ],
    }, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
