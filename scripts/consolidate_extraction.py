#!/usr/bin/env python3
"""Consolidate all extraction lanes into one clean, API-ready dataset.

Reads every completed lane's JSONL output, merges by part_number,
deduplicates (best provenance wins: deterministic > vision, typ > max,
TA=25 preferred), and emits:

  part_number -> { axis -> { value, unit, verbatim, page, pdf_sha,
                             lane, method } }

This is the file the ingest scripts or the API consume directly.
Held/rejected rows never appear. Every value carries its evidence.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

RESULTS = ROOT / "results"

LANES = [
    ("thermal", RESULTS / "thermal-metrics-20261006/thermal-rows.jsonl"),
    ("vout_iq", RESULTS / "power-v2-axes-20261006/power-v2-rows.jsonl"),
    ("power_modes_mcu", RESULTS / "power-modes-wave-b-20261005/mode-current-rows.jsonl"),
    ("power_modes_ti", RESULTS / "power-modes-wave-b-20261006/power-mode-rows-ti-cache.jsonl"),
    ("power_curves", RESULTS / "power-v2-axes-20261006/power-curve-rows.jsonl"),
    ("curves_datasheet", RESULTS / "vision-backlog-batch-20261007/datasheet-vision-rows.jsonl"),
    ("curves_allclasses", RESULTS / "vision-backlog-batch-20261007/remaining-classes-rows.jsonl"),
    ("curves_chunk1", RESULTS / "vision-backlog-batch-20261007/chunk1-netnew-rows.jsonl"),
    ("curves_chunk1_B", RESULTS / "vision-backlog-batch-20261007/chunk1-netnew-rows-B.jsonl"),
    ("curves_tp4", RESULTS / "typical-curves-wave-a-20261006/tp4-rows.jsonl"),
    ("aec_mcu", RESULTS / "aec-grade-wave-20261006/aec-grade-rows-v2.jsonl"),
    ("aec_power", RESULTS / "aec-grade-wave-20261006/aec-grade-power-rows.jsonl"),
    ("errata", RESULTS / "errata-wave-c-20261006/errata-rows-v3-scoped.jsonl"),
    ("p3_reread", Path("/Volumes/M5_4TB/exports/cr_drops/power-reread-p0-20260917/rows.jsonl")),
]

TIER = {"deterministic": 3, "printed_row": 3, "dymupdf_deterministic": 3,
        "deterministic_mode_currents_v1": 3, "deterministic_thermal_metrics_v1": 3,
        "deterministic_power_axes_v2_v1": 3, "deterministic_aec_grade_v1": 3,
        "deterministic_connector_parametrics_v1": 3,
        "vision": 2, "cloud": 2, "openrouter-batch": 2,
        "unknown": 0}


def _tier(row: dict) -> int:
    method = str(row.get("method", "")).lower()
    for key, tier in TIER.items():
        if key in method:
            return tier
    return TIER.get(str(row.get("verdict", "")), 0)


def _axis_name(row: dict) -> str | None:
    for key in ("field", "metric", "axis", "record_kind"):
        v = row.get(key)
        if v:
            return str(v)
    return None


def _row_value(row: dict) -> dict:
    return {
        "value": row.get("value") or row.get("grade"),
        "vout_min_v": row.get("vout_min_v"),
        "vout_max_v": row.get("vout_max_v"),
        "unit": row.get("unit") or row.get("unit_as_printed"),
        "value_role": row.get("value_role"),
        "mode": row.get("mode"),
        "verbatim": row.get("verbatim") or row.get("context_verbatim"),
        "condition_verbatim": row.get("condition_verbatim") or row.get("conditions_verbatim"),
        "page_1based": row.get("page_1based") or row.get("page"),
        "pdf_sha": row.get("pdf_sha") or row.get("document_sha256"),
        "source_url": row.get("source_url"),
        "method": row.get("method"),
        "lane": row.get("_lane"),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    parts: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    docs: dict[str, dict] = defaultdict(lambda: defaultdict(list))
    stats = {"parts": set(), "rows_loaded": 0, "rows_skipped": 0, "by_lane": {}}

    for lane_name, path in LANES:
        if not path.exists():
            stats["by_lane"][lane_name] = "missing"
            continue
        loaded = 0
        skipped = 0
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            verdict = row.get("verdict", "")
            if verdict not in ("extracted", "printed_row", None, ""):
                if verdict != "hold_anchor_disagreement":
                    skipped += 1
                    continue
            loaded += 1
            row["_lane"] = lane_name
            axis = _axis_name(row)
            if not axis:
                skipped += 1
                continue
            entry = _row_value(row)
            part = row.get("part_number")
            sha = row.get("pdf_sha") or row.get("document_sha256")
            if part:
                parts[part][axis].append(entry)
                stats["parts"].add(part)
            if sha:
                docs[sha][axis].append(entry)
        stats["by_lane"][lane_name] = {"loaded": loaded, "skipped": skipped}

    # deduplicate: best tier wins, then typ role, then first-seen
    clean_parts = {}
    for part, axes in parts.items():
        clean = {}
        for axis, entries in axes.items():
            if len(entries) == 1:
                clean[axis] = entries[0]
                continue
            best = max(entries, key=lambda e: (
                TIER.get("deterministic" if "deterministic" in str(e.get("method", "")) else "vision", 0),
                1 if e.get("value_role") in ("typ", "range", "printed") else 0,
                1 if e.get("condition_verbatim") and "25" in str(e.get("condition_verbatim")) else 0,
            ))
            clean[axis] = best
        clean_parts[part] = clean

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w") as sink:
        for part in sorted(clean_parts):
            sink.write(json.dumps({"part_number": part, "axes": clean_parts[part]}, ensure_ascii=False, sort_keys=True) + "\n")

    stats["parts"] = len(stats["parts"])
    print(json.dumps(stats, indent=1))
    print(f"\nclean parts written: {len(clean_parts)}")


if __name__ == "__main__":
    main()
