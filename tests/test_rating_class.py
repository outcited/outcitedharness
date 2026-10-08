"""Rating-class gold tests and margin-comparison contract.

Gold: tests/fixtures/gold/rating_class_v1.jsonl — 63 hand-labeled rows
from the ifx2/rohm-si power grids. The label wins; the projection is
fixed to it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness.electronics.rating_class import (
    RATING_CLASSES,
    compare_with_margin,
    rating_class,
)

FIXTURE = Path(__file__).parent / "fixtures" / "gold" / "rating_class_v1.jsonl"


def _load():
    return [json.loads(l) for l in FIXTURE.read_text().splitlines() if l.strip()]


@pytest.mark.parametrize("row", _load(),
                         ids=lambda r: f"{r.get('symbol') or '-'}|"
                                       f"{str(r.get('parameter'))[:32]}")
def test_gold_rating_class(row):
    got = rating_class(row.get("symbol"), row.get("parameter"),
                       row.get("table_kind"), row.get("quantity_qualifier"))
    assert got == row["expected_rating_class"], (
        f"{row.get('symbol')!r} {str(row.get('parameter'))[:40]!r} "
        f"kind={row.get('table_kind')} qq={row.get('quantity_qualifier')}: "
        f"{got} != {row['expected_rating_class']}")


def test_gold_population_shape():
    rows = _load()
    assert len(rows) == 63
    dist = {}
    for r in rows:
        dist[r["expected_rating_class"]] = dist.get(
            r["expected_rating_class"], 0) + 1
    assert dist["absolute_maximum"] >= 15
    assert dist["transient"] >= 5
    assert "surge" in dist and "recommended" in dist and "typical" in dist


# --- the spec section-43 law ----------------------------------------------------

def test_absmax_never_satisfies_operating():
    """40 V absolute maximum must never read as an operating 36 V answer.

    The projection marks it absolute_maximum; the evaluator (Phase 1)
    consumes that as 'cannot satisfy operating atoms' — the dangerous
    elimination is structurally prevented here.
    """
    cls = rating_class("VDSS", "Drain-source voltage",
                       "absolute_maximum", "absolute_maximum")
    assert cls == "absolute_maximum"
    assert cls not in ("recommended", "rated_limit")


def test_pulse_symbols_project_even_in_absmax():
    assert rating_class("IDM", "Pulsed drain current", "absolute_maximum",
                        "absolute_maximum") == "transient"
    assert rating_class("EAS", "", "absolute_maximum",
                        "absolute_maximum") == "transient"
    assert rating_class("ISM", "Peak reverse drain current",
                        "characteristics", None) == "surge"


def test_continuous_vetoes_pulse_symbol():
    """Glued composite symbol containing IDM, parameter Continuous."""
    assert rating_class("ID@TC=25°CID@TC=100°CIDM",
                        "Continuous Drain Current, VGS @ 10V",
                        "absolute_maximum", "absolute_maximum") \
        == "absolute_maximum"


def test_thermal_table_leak_stays_unknown():
    assert rating_class("VGS(on)", "", "thermal", None) == "unknown"


# --- margin comparison (spec section 42) -----------------------------------------

def test_margin_applies_to_requirement():
    out = compare_with_margin(40.0, 36.0, "min_rating", margin_pct=20.0)
    assert out["effective_requirement"] == pytest.approx(43.2)
    assert out["verdict"] == "NEAR_MISS"          # raw yes, margin no
    assert out["satisfies_raw_requirement"] is True


def test_margin_never_silent():
    out = compare_with_margin(40.0, 36.0, "min_rating")
    assert out["verdict"] == "PASS"
    assert out["margin_pct_applied"] is None       # disclosed, not invented
    assert out["effective_requirement"] == 36.0


def test_near_miss_band():
    assert compare_with_margin(34.0, 36.0, "min_rating",
                               near_miss_pct=10.0)["verdict"] == "NEAR_MISS"
    assert compare_with_margin(20.0, 36.0, "min_rating",
                               near_miss_pct=10.0)["verdict"] == "FAIL"


def test_symmetric_magnitude():
    assert compare_with_margin([-20.0, 20.0], 18.0,
                               "min_rating")["verdict"] == "PASS"
    assert compare_with_margin(17.0, 18.0, "min_rating",
                               near_miss_pct=10.0)["verdict"] == "NEAR_MISS"


def test_max_rating_direction():
    # RDS(on) claim must be <= requirement
    assert compare_with_margin(2.0, 3.0, "max_rating")["verdict"] == "PASS"
    assert compare_with_margin(3.2, 3.0, "max_rating",
                               near_miss_pct=10.0)["verdict"] == "NEAR_MISS"
    assert compare_with_margin(5.0, 3.0, "max_rating")["verdict"] == "FAIL"


def test_inside_direction():
    assert compare_with_margin([4.5, 16.0], [4.5, 17.0],
                               "inside")["verdict"] == "PASS"
    assert compare_with_margin([4.5, 18.0], [4.5, 17.0], "inside",
                               near_miss_pct=10.0)["verdict"] == "NEAR_MISS"
    assert compare_with_margin([2.0, 30.0], [4.5, 17.0],
                               "inside")["verdict"] == "FAIL"


def test_unparseable_is_unknown():
    assert compare_with_margin(None, 36.0, "min_rating")["verdict"] == "UNKNOWN"
    assert compare_with_margin("40", 36.0, "min_rating")["verdict"] == "UNKNOWN"


def test_closed_class_set():
    assert set(RATING_CLASSES) == {
        "absolute_maximum", "recommended", "rated_limit", "typical",
        "transient", "surge", "unknown"}
