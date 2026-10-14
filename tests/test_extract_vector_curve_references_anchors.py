"""Unit tests for the gridline-anchored axis fit added to
extract_vector_curve_references.py (POWER-DATA-01). Pure helpers only —
the anchored path is exercised end to end by the two committed fixtures in
tests/fixtures/gold/curve_evidence_power_data_01/, and the strict
word-center path is pinned unchanged by the frozen pilot gates."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from scripts.extract_vector_curve_references import (
    _GRIDLINE_MAX_RESID,
    _anchor_positions,
    _fit_axis,
    _gridline_anchors,
    _snap_pairs,
)


class _Rect:
    def __init__(self, x0, y0, x1, y1):
        self.x0, self.y0, self.x1, self.y1 = x0, y0, x1, y1

    @property
    def width(self):
        return self.x1 - self.x0

    @property
    def height(self):
        return self.y1 - self.y0


BOX = (100.0, 400.0, 300.0, 500.0)


def _drawings(vx=(), hy=()):
    out = []
    for x in vx:
        out.append({"rect": _Rect(x, 405.0, x + 0.8, 495.0)})
    for y in hy:
        out.append({"rect": _Rect(105.0, y, 295.0, y + 0.8)})
    return out


def test_gridline_anchors_internal_only():
    vx, hy = _gridline_anchors(_drawings(vx=[150.0, 150.3, 200.0], hy=[450.0]), BOX)
    assert vx == [150.0, 200.0]
    assert hy == [450.0]


def test_gridline_anchors_ignore_short_strokes_and_outside():
    drawings = _drawings(vx=[150.0])
    drawings.append({"rect": _Rect(150.0, 410.0, 150.8, 430.0)})  # short: not grid
    drawings.append({"rect": _Rect(400.0, 405.0, 400.8, 495.0)})  # outside frame
    vx, _ = _gridline_anchors(drawings, BOX)
    assert vx == [150.0]


def test_anchor_positions_edges_only_when_gridlines_insufficient():
    gx = _gridline_anchors(_drawings(vx=[150.0]), BOX)[0]
    assert _anchor_positions(gx, BOX, "x") == [100.0, 150.0, 300.0]
    gy = _gridline_anchors(_drawings(hy=[430.0, 450.0, 470.0]), BOX)[1]
    assert _anchor_positions(gy, BOX, "y") == [430.0, 450.0, 470.0]


def test_snap_pairs_takes_majority_and_monotone():
    anchors = [100.0, 150.0, 200.0, 250.0, 300.0]
    pairs = [
        (100.4, 0.0),
        (150.2, 0.5), (149.8, 0.5),
        (200.1, 1.0),
        (250.3, 1.5),
        (300.2, 2.0),
    ]
    snapped = _snap_pairs(pairs, anchors)
    assert snapped == [
        (100.0, 0.0), (150.0, 0.5), (200.0, 1.0), (250.0, 1.5), (300.0, 2.0),
    ]


def test_snap_pairs_refuses_conflicting_values_on_one_anchor():
    # two different values claim the same gridline: geometry cannot decide
    assert _snap_pairs([(150.2, 0.5), (150.4, 1.0), (100.1, 0.0),
                        (200.2, 2.0)], [100.0, 150.0, 200.0]) is None


def test_snap_pairs_refuses_non_monotone_values():
    assert _snap_pairs([(100.1, 1.0), (150.2, 0.0), (200.2, 2.0)],
                       [100.0, 150.0, 200.0]) is None


def test_snap_pairs_refuses_far_words():
    # word 40pt away from every anchor: nothing snaps
    assert _snap_pairs([(140.0, 0.0)], [100.0, 200.0, 250.0]) is None


def test_relaxed_bar_only_on_anchored_path():
    # 0.4%-of-span jitter: refused by the default bar, accepted relaxed
    pairs = [(100.0, 0.0), (150.0, 0.5), (200.0, 1.0),
             (250.0, 1.5), (300.0 + 0.8, 2.0)]
    assert _fit_axis(pairs) is None
    relaxed = _fit_axis(pairs, max_resid=_GRIDLINE_MAX_RESID)
    assert relaxed is not None and relaxed[0] == "linear"
    assert 1e-3 <= relaxed[3] < _GRIDLINE_MAX_RESID


def test_log_axis_accepts_decades_relaxed():
    pairs = [(100.0, 0.001), (150.0, 0.01), (200.0, 0.1), (250.0, 1.0)]
    fit = _fit_axis(pairs, max_resid=_GRIDLINE_MAX_RESID)
    assert fit[0] == "log10"
    mid = 10 ** (fit[1] * 175.0 + fit[2])
    assert math.isclose(mid, math.sqrt(0.01 * 0.1), rel_tol=1e-9)


def test_committed_fixtures_present_and_shaped():
    root = Path(__file__).parent / "fixtures" / "gold"
    for name, sha, page in [
        ("tps54360_p35.json",
         "c2743e51e9f180e3efd7afb2ce1ac792906576207f4e318f9b6075f3ab5c56a8", 35),
        ("lm5005_p25.json",
         "7999d4c9d51f51d7643523b16770c33928404d0e1eb6447bf43c9a4b3e9b58fe", 25),
    ]:
        record = __import__("json").loads(
            (root / "curve_evidence_power_data_01" / name).read_text()
        )
        assert record["document_sha256"] == sha
        assert record["page_1based"] == page
        assert record["schema"] == "harness.electronics-typical-curves-gold.v1"
        assert record["plots"], name
        for plot in record["plots"]:
            assert plot["series"], name
            for series in plot["series"]:
                assert len(series["points"]) >= 10
            anchor = plot.get("_axis_anchor") or {}
            for axis in ("x", "y"):
                assert anchor.get(axis) in ("word_centers", "gridlines")
                assert (plot["_numeric_quality"]
                        [f"{axis}_fit_residual_pct_of_span"]) < 0.5
