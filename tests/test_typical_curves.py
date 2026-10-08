from __future__ import annotations

from harness.electronics.typical_curves import (
    anchor_check,
    axis_labels_grounded,
    plot_regions_for_page,
    validate_curve_payload,
)


class _Rect:
    width = 500.0
    height = 700.0


class _Page:
    rect = _Rect()

    def __init__(self, words):
        self._words = words

    def get_text(self, mode):
        assert mode == "words"
        return self._words


def test_plot_regions_split_side_by_side_figures():
    # Layout of the MSP430G2744 p15 render: two figures sharing y-bands,
    # captions as word runs (Figure / 5-2. / title words).
    words = [
        (30, 560, 60, 572, "8.0"),   # left ticks
        (30, 300, 60, 312, "4.0"),
        (250, 560, 280, 572, "5.0"),  # right ticks
        (250, 300, 280, 312, "2.0"),
        (30, 640, 70, 652, "Figure"),
        (74, 640, 100, 652, "5-2."),
        (104, 640, 200, 652, "Active-mode"),
        (204, 640, 230, 652, "Current"),
        (280, 640, 320, 652, "Figure"),
        (324, 640, 350, 652, "5-3."),
        (354, 640, 450, 652, "Active-Mode"),
        (454, 640, 500, 652, "Frequency"),
    ]
    regions = plot_regions_for_page(_Page(words))
    assert len(regions) == 2
    left, right = regions
    assert left["bbox"][0] < right["bbox"][0]
    assert left["bbox"][2] < right["bbox"][0] + 60


def _payload():
    return {
        "title": "Figure 12. Current consumption vs VDD",
        "axes": {
            "x": {"label": "VDD", "unit": "V", "min": 1.7, "max": 3.6},
            "y": {"label": "IDD", "unit": "mA", "min": 0.0, "max": 10.0},
        },
        "series": [
            {
                "name": "Run mode",
                "condition": "fHCLK = 64 MHz",
                "points": [
                    {"x": 1.8, "y": 3.2},
                    {"x": 2.4, "y": 3.4},
                    {"x": 3.3, "y": 3.6},
                ],
            }
        ],
    }


def test_validate_curve_payload_accepts_clean_digitization():
    assert validate_curve_payload(_payload()) == []


def test_validate_curve_payload_rejects_structural_junk():
    payload = _payload()
    payload["series"] = []
    assert "series_empty" in validate_curve_payload(payload)

    payload = _payload()
    payload["series"][0]["points"] = [{"x": 99.0, "y": 3.2}]
    assert any(
        problem.endswith("point_x_out_of_range")
        for problem in validate_curve_payload(payload)
    )

    payload = _payload()
    payload["axes"]["x"] = {"label": "VDD", "unit": "V", "min": 3.6, "max": 1.7}
    assert "axis_x_range_inverted" in validate_curve_payload(payload)


def test_axis_labels_grounded_against_page_words():
    words = ["Figure", "12.", "VDD", "(V)", "IDD", "(mA)", "Run", "mode"]
    ok, missing = axis_labels_grounded(_payload(), words)
    assert ok and missing == []

    payload = _payload()
    payload["axes"]["y"]["label"] = "IQ"
    ok, missing = axis_labels_grounded(payload, words)
    assert not ok and missing == ["axis_y:IQ"]


def test_anchor_check_agreement_and_tolerance():
    facts = [
        {
            "field": "IDD Run mode",
            "typ": 3.4,
            "unit": "mA",
            "condition_verbatim": "VDD = 2.4 V, fHCLK = 64 MHz",
        },
        {
            "field": "IDD Run mode",
            "typ": 3.6,
            "unit": "mA",
            "condition_verbatim": "VDD = 3.3 V, fHCLK = 64 MHz",
        },
    ]
    result = anchor_check(_payload(), table_facts=facts, y_tolerance_pct=2.0)
    assert result["comparable"] == 2
    assert result["agreed"] == 2
    assert result["agreement_rate"] == 1.0
    assert all(check["verdict"] == "agree" for check in result["checks"])


def test_anchor_check_disagreement_and_unit_conversion():
    facts = [
        {
            "field": "IDD Run mode",
            "typ": 5.9,
            "unit": "mA",
            "condition_verbatim": "VDD = 2.4 V",
        },
        {
            "field": "IDD Run mode",
            "typ": 3400.0,
            "unit": "µA",
            "condition_verbatim": "VDD = 2.4 V",
        },
    ]
    result = anchor_check(_payload(), table_facts=facts, y_tolerance_pct=2.0)
    verdicts = [check["verdict"] for check in result["checks"]]
    assert "disagree" in verdicts
    # 3400 µA = 3.4 mA — unit-normalized fact must land on the curve point
    agree = [check for check in result["checks"] if check["verdict"] == "agree"]
    assert agree and agree[0]["fact_value"] == 3.4


def test_anchor_check_abstains_without_x_support():
    facts = [
        {
            "field": "IDD Run mode",
            "typ": 3.4,
            "unit": "mA",
            "condition_verbatim": "VDD = 2.4 V",
        },
    ]
    payload = _payload()
    payload["series"][0]["points"] = [{"x": 3.3, "y": 3.6}]
    result = anchor_check(payload, table_facts=facts, x_tolerance_pct=0.5)
    assert result["checks"][0]["verdict"] == "abstain_no_x_support"
    assert result["comparable"] == 0
