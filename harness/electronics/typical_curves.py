"""Typical-characteristics curve lane (figure-digitization contracts).

The text substrate has no reach into printed plots; this lane gives the
vision fabric a typed contract and keeps the result honest with two
deterministic checks:

1. grounding — axis labels and legend text must exist in the page word
   list (the plot reads words the document printed);
2. cross-substrate anchor — digitized points are compared against the same
   part's EC-table typ values at the same printed condition. Agreement
   rate is the lane's qualification metric; a curve that contradicts the
   table is a hold, not a product.

Plot regions prefer caption/axis-tick/legend anchors over full-page
renders so token cost tracks plot size, per the crop-don't-page rule.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence


CURVE_SCHEMA = "harness.electronics-typical-curves.v1"

_CAPTION = re.compile(
    r"(?:^|\b)(?:figure|fig\.?)\s*\d+\s*[.:–-]?\s*.{0,80}", re.I
)
_AXIS_TICK = re.compile(r"^\s*[+-]?\d+(?:\.\d+)?\s*$")
_LEGEND_ENTRY = re.compile(r"[A-Za-z]")
_PAD = 12.0


def _word_text(page: Any) -> list[tuple[float, float, float, float, str]]:
    try:
        words = page.get_text("words")
    except Exception:  # pragma: no cover - PyMuPDF internals
        return []
    return [
        (float(w[0]), float(w[1]), float(w[2]), float(w[3]), str(w[4]))
        for w in words
    ]


def _line_anchors(
    words: Sequence[tuple[float, float, float, float, str]],
) -> list[tuple[float, float, float, float, str]]:
    """Caption lines: words joined per y-band x-run so multi-word captions
    match without gluing side-by-side captions into one string."""

    lines: dict[int, list[tuple[float, float, float, float, str]]] = {}
    for word in words:
        lines.setdefault(int(word[1] // 4), []).append(word)
    out = []
    for band in sorted(lines):
        members = sorted(lines[band], key=lambda w: w[0])
        runs: list[list[tuple[float, float, float, float, str]]] = []
        for word in members:
            if runs and word[0] - runs[-1][-1][2] > 24.0:
                runs.append([word])
            elif runs:
                runs[-1].append(word)
            else:
                runs.append([word])
        for run in runs:
            joined = " ".join(word[4] for word in run)
            if _CAPTION.search(joined):
                out.append(
                    (
                        min(word[0] for word in run),
                        min(word[1] for word in run),
                        max(word[2] for word in run),
                        max(word[3] for word in run),
                        joined,
                    )
                )
    return out


def _rect_gap(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    dx = max(0.0, max(a[0] - b[2], b[0] - a[2]))
    dy = max(0.0, max(a[1] - b[3], b[1] - a[3]))
    return (dx * dx + dy * dy) ** 0.5


def _cluster_anchors(
    words: Sequence[tuple[float, float, float, float, str]],
    gap: float = 28.0,
) -> list[list[tuple[float, float, float, float, str]]]:
    """One cluster per figure, seeded by its caption. Every figure prints a
    caption; nearest-caption assignment keeps side-by-side and stacked
    figures apart, where proximity chaining would merge them."""

    captions = [word for word in words if _CAPTION.search(word[4])]
    bodies = [word for word in words if word not in captions]
    if not captions:
        captions = [min(bodies, key=lambda w: (w[1], w[0]))] if bodies else []
        bodies = [word for word in bodies if word not in captions]
    clusters: list[list[tuple[float, float, float, float, str]]] = [
        [caption] for caption in captions
    ]
    for word in bodies:
        target = min(
            range(len(clusters)),
            key=lambda index: _rect_gap(word[:4], clusters[index][0][:4]),
        )
        clusters[target].append(word)
    return clusters


def _drawing_plot_frames(
    page: Any,
    ticks: Sequence[tuple[float, float, float, float, str]],
) -> list[tuple[float, float, float, float]]:
    """Vector plot frames: drawn rects big enough to hold a plot that contain
    axis-tick words. Exact bounds — captions, tables and page furniture sit
    outside the frame by construction."""

    try:
        drawings = page.get_drawings()
    except Exception:  # pragma: no cover - PyMuPDF internals
        return []
    frames = []
    for drawing in drawings:
        rect = drawing.get("rect")
        if rect is None or rect.width < 80 or rect.height < 80:
            continue
        box = (float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1))
        inside = sum(
            1
            for word in ticks
            if box[0] - 2 <= word[0] and word[2] <= box[2] + 2
            and box[1] - 2 <= word[1] and word[3] <= box[3] + 2
        )
        if inside >= 2:
            frames.append(box)
    frames.sort(key=lambda box: (box[1], box[0]))
    kept: list[tuple[float, float, float, float]] = []
    for box in frames:
        if any(
            box[0] >= prev[0] - 2 and box[1] >= prev[1] - 2
            and box[2] <= prev[2] + 2 and box[3] <= prev[3] + 2
            for prev in kept
        ):
            continue
        kept.append(box)
    return kept


def plot_regions_for_page(page: Any) -> list[dict[str, Any]]:
    """Anchor-first plot regions — one per figure; full page only when
    nothing anchors. Multi-figure pages must never merge plots into one
    crop: the digitizer would mix axes across figures. Vector plot frames
    win when present; caption-seeded clusters cover figure-only scans."""

    words = _word_text(page)
    if not words:
        return [
            {
                "bbox": (0.0, 0.0, float(page.rect.width), float(page.rect.height)),
                "source": "fullpage",
            }
        ]
    ticks = [word for word in words if _AXIS_TICK.match(word[4])]
    frames = _drawing_plot_frames(page, ticks)
    if frames:
        label_margin = 50.0
        grown = [list(box) for box in frames]
        for word in words:
            gaps = [_rect_gap(word[:4], tuple(box)) for box in frames]
            best = min(range(len(frames)), key=lambda index: gaps[index])
            if gaps[best] > label_margin:
                continue
            others = [g for i, g in enumerate(gaps) if i != best]
            if others and gaps[best] * 2.0 > min(others):
                continue
            box = grown[best]
            box[0] = min(box[0], word[0])
            box[1] = min(box[1], word[1])
            box[2] = max(box[2], word[2])
            box[3] = max(box[3], word[3])
        return [
            {
                "bbox": (
                    max(0.0, box[0] - _PAD),
                    max(0.0, box[1] - _PAD),
                    min(float(page.rect.width), box[2] + _PAD),
                    min(float(page.rect.height), box[3] + _PAD),
                ),
                "source": "drawings",
            }
            for box in grown
        ]
    caption_lines = _line_anchors(words)
    anchors = caption_lines + ticks
    if not anchors:
        return [
            {
                "bbox": (0.0, 0.0, float(page.rect.width), float(page.rect.height)),
                "source": "fullpage",
            }
        ]
    regions = []
    for cluster in _cluster_anchors(anchors):
        xs0 = min(word[0] for word in cluster)
        ys0 = min(word[1] for word in cluster)
        xs1 = max(word[2] for word in cluster)
        ys1 = max(word[3] for word in cluster)
        bbox = (
            max(0.0, xs0 - _PAD),
            max(0.0, ys0 - _PAD),
            min(float(page.rect.width), xs1 + _PAD),
            min(float(page.rect.height), ys1 + _PAD),
        )
        regions.append({"bbox": bbox, "source": "anchors"})
    regions.sort(key=lambda region: (region["bbox"][1], region["bbox"][0]))
    return regions


def canonicalize_labels(
    payload: Mapping[str, Any],
    page_words: Sequence[str],
) -> list[str]:
    """Rewrite model labels to the verbatim printed word run.

    Cloud vision digitizes numerically to spec but normalizes label
    spelling (unit suffixes, subscripts). Provenance law wants the printed
    string; a normalized-equality match against the page word runs recovers
    it deterministically. Labels with no printed match stay as-is and the
    grounding gate then fails them honestly. Returns the repair log.
    """

    normalized = [re.sub(r"[^a-z0-9]", "", word.lower()) for word in page_words]
    runs: dict[str, str] = {}
    for width in (1, 2, 3, 4, 5):
        for start in range(len(normalized) - width + 1):
            key = "".join(normalized[start : start + width])
            if key and key not in runs:
                runs[key] = " ".join(page_words[start : start + width])
    repaired: list[str] = []

    def _best(key: str) -> str | None:
        if not key:
            return None
        exact = [text for run_key, text in runs.items() if run_key == key]
        if exact:
            return max(exact, key=len)
        best_text = None
        best_len = -1
        for run_key, run_text in runs.items():
            if key in run_key or run_key in key:
                if len(run_text) > len(key) * 2 + 20:
                    continue
                if len(run_text) > best_len:
                    best_len = len(run_text)
                    best_text = run_text
        return best_text

    def _fix(container: Any, field: str) -> None:
        if not isinstance(container, dict):
            return
        label = container.get(field)
        if not label:
            return
        key = re.sub(r"[^a-z0-9]", "", str(label).lower())
        printed = _best(key)
        if printed is not None and printed != label:
            repaired.append(f"{label!r}->{printed!r}")
            container[field] = printed

    axes = payload.get("axes") or {}
    for side in ("x", "y"):
        _fix(axes.get(side), "label")
    for series in payload.get("series") or []:
        _fix(series, "name")
    return repaired


def axis_labels_grounded(
    payload: Mapping[str, Any],
    page_words: Sequence[str],
) -> tuple[bool, list[str]]:
    """Every non-null axis label and series name must appear in the words."""

    normalized = [re.sub(r"[^a-z0-9]", "", word.lower()) for word in page_words]
    haystack = {word for word in normalized if word}
    passage = "".join(normalized)

    def _present(text: str) -> bool:
        key = re.sub(r"[^a-z0-9]", "", str(text).lower())
        return not key or key in haystack or key in passage

    missing: list[str] = []
    axes = payload.get("axes") or {}
    for side in ("x", "y"):
        axis = axes.get(side) or {}
        label = axis.get("label")
        if label and not _present(label):
            missing.append(f"axis_{side}:{label}")
    for index, series in enumerate(payload.get("series") or []):
        name = (series or {}).get("name")
        if name and not _present(name):
            missing.append(f"series[{index}]:{name}")
    return (not missing, missing)


def _in_axis_range(value: float, axis: Mapping[str, Any]) -> bool:
    low = axis.get("min")
    high = axis.get("max")
    if low is not None and value < float(low) - 1e-9:
        return False
    if high is not None and value > float(high) + 1e-9:
        return False
    return True


def validate_curve_payload(payload: Mapping[str, Any]) -> list[str]:
    """Structural rejects: empty series, out-of-range points, junk axes."""

    problems: list[str] = []
    axes = payload.get("axes") or {}
    for side in ("x", "y"):
        axis = axes.get(side)
        if not isinstance(axis, Mapping):
            problems.append(f"axis_{side}_missing")
            continue
        low, high = axis.get("min"), axis.get("max")
        if low is not None and high is not None and float(low) >= float(high):
            problems.append(f"axis_{side}_range_inverted")
    series_list = payload.get("series") or []
    if not series_list:
        problems.append("series_empty")
    for index, series in enumerate(series_list):
        points = (series or {}).get("points") or []
        if not points:
            problems.append(f"series[{index}]_points_empty")
            continue
        for point in points:
            x, y = point.get("x"), point.get("y")
            if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
                problems.append(f"series[{index}]_point_not_numeric")
                break
            if not _in_axis_range(float(x), axes.get("x") or {}):
                problems.append(f"series[{index}]_point_x_out_of_range")
                break
            if not _in_axis_range(float(y), axes.get("y") or {}):
                problems.append(f"series[{index}]_point_y_out_of_range")
                break
    return problems


def _normal_unit(unit: str | None) -> str | None:
    if not unit:
        return None
    text = str(unit).replace("μ", "µ").replace("uA", "µA").strip()
    text = re.sub(r"\s+", "", text)
    return text.lower() or None


_UNIT_SCALE_TO_A = {
    "a": 1.0,
    "ma": 1e-3,
    "µa": 1e-6,
    "ua": 1e-6,
    "na": 1e-9,
    "pa": 1e-12,
}


_GENERIC_TOKENS = {
    "mode",
    "modes",
    "current",
    "currents",
    "consumption",
    "typ",
    "typical",
    "min",
    "max",
    "value",
    "supply",
    "power",
    "draw",
    "level",
    "levels",
    "vs",
    "versus",
    "and",
    "the",
    "with",
    "for",
    "per",
    "nom",
    "ua",
    "ma",
    "na",
    "mv",
    "mhz",
    "khz",
    "hz",
}


def _fact_tokens(fact: Mapping[str, Any]) -> set[str]:
    text = f"{fact.get('field') or fact.get('parameter') or ''} {fact.get('condition_verbatim') or ''}"
    return {
        token
        for token in re.findall(r"[a-z0-9]{2,}", text.lower())
        if token not in _GENERIC_TOKENS
    }


_NUM_UNIT = re.compile(
    r"(\d+(?:\.\d+)?)\s*(mhz|khz|hz|v|mv|ma|µa|ua|na|c|°c)", re.I
)
_UNIT_FAMILY = {
    "mhz": "freq",
    "khz": "freq",
    "hz": "freq",
    "v": "volt",
    "mv": "volt",
    "ma": "amp",
    "µa": "amp",
    "ua": "amp",
    "na": "amp",
    "c": "temp",
    "°c": "temp",
}


def _num_sig(text: str) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for match in _NUM_UNIT.finditer(text):
        value = match.group(1).rstrip("0").rstrip(".")
        unit = match.group(2).lower().replace("°", "")
        out.setdefault(_UNIT_FAMILY.get(unit, unit), set()).add(f"{value}{unit}")
    return out


def _series_matches(fact: Mapping[str, Any], series_text: str) -> bool:
    """Token overlap, but printed quantities must not contradict: a 1 MHz
    fact never matches a 16 MHz series on the shared 'fDCO' token alone."""

    fact_text = f"{fact.get('field') or fact.get('parameter') or ''} {fact.get('condition_verbatim') or ''}".lower()
    fact_plain = _fact_tokens(fact)
    series_plain = set(re.findall(r"[a-z0-9]{2,}", series_text)) - _GENERIC_TOKENS
    fact_nums = _num_sig(fact_text)
    series_nums = _num_sig(series_text)
    for family in set(fact_nums) & set(series_nums):
        if not (fact_nums[family] & series_nums[family]):
            return False
    if fact_plain and series_plain and not (fact_plain & series_plain):
        return False
    return bool(fact_plain or fact_nums or series_plain or series_nums)


_X_VOLTAGE = re.compile(r"\bV(?:DD|CC|IN|supply|oltage)?\b|\bvolts?\b|\bV\b", re.I)
_X_FREQ = re.compile(r"\bf\b|freq|MCLK|HCLK|CPU\s*clock|\bMHz\b|\bkHz\b", re.I)
_SUPPLY_ALIASES = ("VDD", "VCC", "VIN", "DVCC", "AVCC", "VDDS")
_ANCHOR_FREQ = re.compile(
    r"(?:f\s*(?:DCO|HCLK|CPU|CLK|SYS|OSC|MASTER)?|CPU\s*clock|MCLK|HCLK|"
    r"system\s*clock)\s*=?\s*(\d+(?:\.\d+)?)\s*(MHz|kHz)",
    re.I,
)
_IDENT = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,10}")
_NUM = re.compile(r"(\d+(?:\.\d+)?)")


def _x_axis_kind(x_axis: Mapping[str, Any]) -> str | None:
    label = f"{x_axis.get('label') or ''} {x_axis.get('unit') or ''}"
    if _X_VOLTAGE.search(label) or (x_axis.get("unit") or "").strip() in ("V", "mV"):
        return "voltage"
    if _X_FREQ.search(label) or re.search(r"(MHz|kHz|Hz)", label):
        return "frequency"
    return None


def _x_anchor_value(condition: str, x_axis: Mapping[str, Any]) -> float | None:
    """The fact must name the same variable the plot puts on its x axis."""

    label = str(x_axis.get("label") or "")
    kind = _x_axis_kind(x_axis)
    variables = []
    for token in _IDENT.findall(label):
        if re.fullmatch(r"(?:VDD|VCC|VIN|DVCC|AVCC|VDDS|VOL|VOH|VDS|VGS|MCLK|HCLK|fDCO|fCLK)", token, re.I):
            variables.append(token)
    if not variables and kind == "voltage":
        variables = list(_SUPPLY_ALIASES)
    for variable in variables:
        match = re.search(
            rf"{re.escape(variable)}\s*=?\s*{_NUM.pattern}", condition, re.I
        )
        if match:
            return float(match.group(1))
    if kind == "frequency":
        match = _ANCHOR_FREQ.search(condition)
        if match:
            value = float(match.group(1))
            return value / 1000.0 if match.group(2).lower() == "khz" else value
    return None


def _interpolate(
    points: Sequence[Mapping[str, Any]], x_value: float
) -> float:
    """Linear read between digitized samples — the polyline is the curve."""

    ordered = sorted(points, key=lambda point: float(point["x"]))
    for left, right in zip(ordered, ordered[1:]):
        x0, y0 = float(left["x"]), float(left["y"])
        x1, y1 = float(right["x"]), float(right["y"])
        if x0 <= x_value <= x1 and x1 != x0:
            return y0 + (y1 - y0) * (x_value - x0) / (x1 - x0)
    return float(ordered[0]["y"]) if x_value <= float(ordered[0]["x"]) else float(ordered[-1]["y"])


def anchor_check(
    payload: Mapping[str, Any],
    *,
    table_facts: Sequence[Mapping[str, Any]],
    x_tolerance_pct: float = 2.0,
    y_tolerance_pct: float = 2.0,
) -> dict[str, Any]:
    """Compare digitized points to EC-table typ values at matched conditions.

    A fact matches a series when the series name/condition text carries the
    fact's field tokens and the fact's condition carries the x-axis anchor
    (typically VDD). The nearest point by x is compared within tolerance of
    the axis span; unit disagreement is an abstain, never a guess.
    """

    axes = payload.get("axes") or {}
    y_axis = axes.get("y") or {}
    x_axis = axes.get("x") or {}
    y_unit = _normal_unit(y_axis.get("unit"))
    x_unit = _normal_unit(x_axis.get("unit"))
    x_span = None
    if x_axis.get("min") is not None and x_axis.get("max") is not None:
        x_span = abs(float(x_axis["max"]) - float(x_axis["min"])) or 1.0
    checks: list[dict[str, Any]] = []
    facts_without_value = 0
    facts_without_anchor = 0
    facts_no_series_match = 0
    facts_unit_mismatch = 0
    for fact in table_facts:
        fact_value = fact.get("typ", fact.get("value"))
        if not isinstance(fact_value, (int, float)):
            facts_without_value += 1
            continue
        fact_unit = _normal_unit(fact.get("unit"))
        if y_unit and fact_unit and y_unit != fact_unit:
            scale_a = _UNIT_SCALE_TO_A.get(fact_unit or "")
            scale_b = _UNIT_SCALE_TO_A.get(y_unit or "")
            if not (scale_a and scale_b):
                facts_unit_mismatch += 1
                continue
            fact_value = float(fact_value) * scale_a / scale_b
            fact_unit = y_unit
        condition = str(fact.get("condition_verbatim") or "") + " " + str(
            fact.get("field") or fact.get("parameter") or ""
        )
        x_value = _x_anchor_value(condition, x_axis)
        if x_value is None or x_span is None:
            facts_without_anchor += 1
            continue
        matched_series = False
        for index, series in enumerate(payload.get("series") or []):
            text = f"{(series or {}).get('name') or ''} {(series or {}).get('condition') or ''}".lower()
            if not _series_matches(fact, text):
                continue
            matched_series = True
            points = sorted(
                (series or {}).get("points") or [],
                key=lambda point: float(point.get("x", 0.0)),
            )
            if not points:
                continue
            xs = [float(point["x"]) for point in points]
            if x_value < xs[0] - 1e-9 or x_value > xs[-1] + 1e-9:
                checks.append(
                    {
                        "series_index": index,
                        "fact_field": fact.get("field") or fact.get("parameter"),
                        "verdict": "abstain_no_x_support",
                        "x_value": x_value,
                    }
                )
                continue
            y_value = _interpolate(points, x_value)
            dx = min(abs(x - x_value) for x in xs)
            if x_tolerance_pct and x_span and dx > x_span * x_tolerance_pct / 100.0:
                if len(points) < 2:
                    checks.append(
                        {
                            "series_index": index,
                            "fact_field": fact.get("field") or fact.get("parameter"),
                            "verdict": "abstain_no_x_support",
                            "x_value": x_value,
                        }
                    )
                    continue
            y_span = None
            if y_axis.get("min") is not None and y_axis.get("max") is not None:
                y_span = abs(float(y_axis["max"]) - float(y_axis["min"])) or 1.0
            delta_pct = (
                abs(y_value - float(fact_value)) / abs(float(fact_value)) * 100.0
                if float(fact_value) != 0
                else (0.0 if y_value == 0 else 100.0)
            )
            checks.append(
                {
                    "series_index": index,
                    "fact_field": fact.get("field") or fact.get("parameter"),
                    "fact_value": fact_value,
                    "fact_unit": fact_unit,
                    "x_value": x_value,
                    "point": {"x": x_value, "y": y_value},
                    "interpolated": dx > 0.0,
                    "delta_pct": round(delta_pct, 4),
                    "verdict": "agree"
                    if delta_pct <= y_tolerance_pct
                    else "disagree",
                }
            )
        if not matched_series:
            facts_no_series_match += 1
    agreed = sum(1 for check in checks if check["verdict"] == "agree")
    comparable = sum(
        1 for check in checks if check["verdict"] in ("agree", "disagree")
    )
    return {
        "schema": CURVE_SCHEMA,
        "checks": checks,
        "comparable": comparable,
        "agreed": agreed,
        "facts_considered": len(table_facts),
        "abstain_no_value": facts_without_value,
        "abstain_unit_mismatch": facts_unit_mismatch,
        "abstain_no_x_anchor": facts_without_anchor,
        "abstain_no_series_match": facts_no_series_match,
        "agreement_rate": (agreed / comparable) if comparable else None,
    }


__all__ = [
    "CURVE_SCHEMA",
    "anchor_check",
    "axis_labels_grounded",
    "canonicalize_labels",
    "plot_regions_for_page",
    "validate_curve_payload",
]
