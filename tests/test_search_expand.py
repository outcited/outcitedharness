"""Intent expansion tests (PRD-SEARCH-01 R3)."""

from __future__ import annotations

from harness.search.expand import axis_queries, expand_intent


def test_prd_example_low_power_wireless_sensor():
    result = expand_intent("Low-power wireless industrial sensor")
    labels = {i["label"] for i in result["interpretations"]}
    assert "low sleep / standby current" in labels
    assert "integrated wireless MCU / transceiver" in labels
    assert "industrial communications compatibility" in labels


def test_original_wording_preserved_verbatim():
    result = expand_intent("Low-power wireless industrial sensor")
    assert result["original"] == "Low-power wireless industrial sensor"


def test_interpretations_are_hypotheses_with_confidence():
    result = expand_intent("battery ble sensor node")
    assert result["interpretations"]
    for interp in result["interpretations"]:
        assert interp["status"] == "hypothesis"
        assert 0.0 < interp["confidence"] <= 1.0
        assert interp["axes"]


def test_cap_on_interpretation_count():
    result = expand_intent(
        "low power wireless industrial battery high temperature converter")
    assert len(result["interpretations"]) <= 6


def test_no_match_returns_empty_not_forced():
    result = expand_intent("kitchen sink")
    assert result["interpretations"] == []
    assert result["original"] == "kitchen sink"


def test_axis_queries_give_auxiliary_phrasings():
    aux = axis_queries("low power wireless sensor")
    assert aux
    assert all("sleep" in a or "energy" in a or "wireless" in a or "mode" in a
               for a in aux[:2])
