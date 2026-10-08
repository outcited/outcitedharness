#!/usr/bin/env python3
"""Score extractor output against typical-curves gold; print label template.

Gold files live in tests/fixtures/gold/typical_curves/*.json (schema example
in that directory). The three acceptance metrics:

  axis_label_exact  — printed axis labels matched exactly (>= 0.95)
  series_count_exact — series count per plot matched (>= 0.90)
  point_error_pct   — sampled-point error <= 2% of the y-axis span

With --emit-template a cold-labeling skeleton is written for a given PDF +
page so a human (or a second model) labels from the render, never from
extractor output.
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

from harness.electronics.typical_curves import (  # noqa: E402
    anchor_check,
    validate_curve_payload,
)


def _points(raw) -> list[dict]:
    out = []
    for item in raw or []:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            out.append({"x": float(item[0]), "y": float(item[1])})
        elif isinstance(item, dict) and "x" in item and "y" in item:
            out.append({"x": float(item["x"]), "y": float(item["y"])})
    return out


def _normalize_plot(plot: dict) -> dict:
    series = []
    for entry in plot.get("series") or []:
        entry = dict(entry)
        entry["points"] = _points(entry.get("points"))
        series.append(entry)
    out = dict(plot)
    out["series"] = series
    return out


def load_gold(gold_dir: Path) -> list[dict]:
    plots = []
    for path in sorted(gold_dir.glob("*.json")):
        if path.name.startswith("_"):
            continue
        record = json.loads(path.read_text())
        for plot in record.get("plots") or []:
            normalized = _normalize_plot(plot)
            normalized["_source_artifact"] = record.get("source_artifact")
            normalized["_page_1based"] = record.get("page_1based")
            normalized["_file"] = path.name
            plots.append(normalized)
    return plots


def _pair(gold: list[dict], predictions: list[dict]) -> list[tuple[dict, dict]]:
    by_page: dict[tuple[str, int], list[dict]] = {}
    for predicted in predictions:
        source = str(predicted.get("source_path") or "").lower()
        stem = source.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        page = int(predicted.get("page_1based") or 0)
        by_page.setdefault((stem, page), []).append(predicted)
    for members in by_page.values():
        members.sort(key=lambda row: int(row.get("figure_index") or 0))
    pairs = []
    cursor: dict[tuple[str, int], int] = {}
    for expected in gold:
        artifact = str(expected.get("_source_artifact") or "").lower()
        stem = artifact.rsplit(".", 1)[0]
        page = int(expected.get("_page_1based") or 0)
        key = (stem, page)
        members = by_page.get(key) or []
        index = cursor.get(key, 0)
        if index < len(members):
            cursor[key] = index + 1
            pairs.append((expected, members[index]))
    return pairs


def _interp(points: list[dict], x: float) -> float | None:
    if not points:
        return None
    ordered = sorted(points, key=lambda p: p["x"])
    xs = [p["x"] for p in ordered]
    if x < xs[0] - 1e-9 or x > xs[-1] + 1e-9:
        return None
    for left, right in zip(ordered, ordered[1:]):
        if left["x"] <= x <= right["x"] and right["x"] != left["x"]:
            return left["y"] + (right["y"] - left["y"]) * (x - left["x"]) / (right["x"] - left["x"])
    return ordered[0]["y"] if x <= xs[0] else ordered[-1]["y"]


_UNIT_TOKENS = {"mhz", "khz", "hz", "v", "mv", "ma", "ua", "na", "a", "c",
                "volt", "volts", "amp", "amps", "micro", "milli", "microamp",
                "milliamp"}


def _label_key(text: str) -> str:
    """Semantic label key: unit suffixes and typographic noise dropped."""

    tokens = re.findall(r"[a-z0-9]+", (text or "").lower())
    return "".join(token for token in tokens if token not in _UNIT_TOKENS)


def score(pairs: list[tuple[dict, dict]]) -> dict:
    axis_total = axis_hit = 0
    key_total = key_hit = 0
    series_total = series_hit = 0
    point_errors: list[float] = []
    for expected, predicted in pairs:
        exp_axes = expected.get("axes") or {}
        pred_axes = predicted.get("axes") or {}
        for side in ("x", "y"):
            exp_label = (exp_axes.get(side) or {}).get("label")
            pred_label = (pred_axes.get(side) or {}).get("label")
            if exp_label:
                axis_total += 1
                if exp_label == pred_label:
                    axis_hit += 1
                key_total += 1
                if _label_key(exp_label) == _label_key(pred_label):
                    key_hit += 1
        exp_series = expected.get("series") or []
        pred_series = predicted.get("series") or []
        series_total += 1
        if len(exp_series) == len(pred_series):
            series_hit += 1
        y_axis = exp_axes.get("y") or {}
        span = None
        if y_axis.get("min") is not None and y_axis.get("max") is not None:
            span = abs(float(y_axis["max"]) - float(y_axis["min"])) or 1.0
        for exp_entry, pred_entry in zip(exp_series, pred_series):
            pred_points = _normalize_plot({"points": pred_entry.get("points") or []})["points"]
            for point in exp_entry.get("points") or []:
                if not pred_points or span is None:
                    continue
                estimate = _interp(pred_points, float(point["x"]))
                if estimate is None:
                    continue
                delta = abs(estimate - point["y"]) / span * 100.0
                point_errors.append(delta)
    return {
        "plots_scored": len(pairs),
        "axis_label_exact": (axis_hit / axis_total) if axis_total else None,
        "axis_label_key_match": (key_hit / key_total) if key_total else None,
        "series_count_exact": (series_hit / series_total) if series_total else None,
        "point_error_pct_max": max(point_errors) if point_errors else None,
        "point_error_pct_mean": (
            sum(point_errors) / len(point_errors) if point_errors else None
        ),
        "points_compared": len(point_errors),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--gold-dir", type=Path, default=ROOT / "tests/fixtures/gold/typical_curves")
    ap.add_argument("--predictions", type=Path, help="extract_typical_curves --out JSONL")
    ap.add_argument("--emit-template", type=Path)
    ap.add_argument("--source-artifact", default="")
    ap.add_argument("--page", type=int, default=0)
    args = ap.parse_args()

    if args.emit_template:
        template = {
            "schema": "harness.electronics-typical-curves-gold.v1",
            "source_artifact": args.source_artifact,
            "source": "Written cold from the printed plot before scoring — never from extractor output.",
            "document_sha256": None,
            "page_1based": args.page,
            "plots": [
                {
                    "title": None,
                    "axes": {
                        "x": {"label": None, "unit": None, "min": None, "max": None},
                        "y": {"label": None, "unit": None, "min": None, "max": None},
                    },
                    "series": [{"name": None, "condition": None, "points": []}],
                }
            ],
            "notes": "Points as [x, y] pairs at identifiable features only.",
        }
        args.emit_template.write_text(json.dumps(template, indent=2) + "\n")
        print(f"wrote {args.emit_template}")
        return 0

    gold = load_gold(args.gold_dir)
    if not args.predictions:
        print(json.dumps({"gold_plots": len(gold)}))
        return 0
    predictions = []
    for line in args.predictions.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        payload = _normalize_plot(row.get("payload") or row)
        payload["source_path"] = row.get("source_path")
        payload["page_1based"] = row.get("page_1based")
        payload["figure_index"] = row.get("figure_index")
        predictions.append(payload)
    for expected in gold:
        problems = validate_curve_payload(expected)
        if problems:
            print(f"gold invalid: {expected.get('_file')}: {problems}", file=sys.stderr)
            return 1
    pairs = _pair(gold, predictions)
    result = score(pairs)
    result["pairs"] = len(pairs)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
