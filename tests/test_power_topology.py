"""Power-topology gold tests.

Gold: tests/fixtures/gold/power_topology_v1.jsonl — 105 hand-labeled
parts sampled across vendor selector aisles (scripts/sample_power_
topology_gold.py), labeled against printed front-matter evidence only
(scripts/build_power_topology_fixture.py). The label wins; the reader is
fixed to it, never the reverse.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness.electronics.power_topology import read_topology

FIXTURE = Path(__file__).parent / "fixtures" / "gold" / "power_topology_v1.jsonl"


def _load():
    rows = [json.loads(l) for l in FIXTURE.read_text().splitlines() if l.strip()]
    assert rows
    return rows


def _pages_from(row):
    return [{"page": 1, "text": row["front_text"]}]


@pytest.mark.parametrize("row", _load(), ids=lambda r: r["part_number"])
def test_gold_topology(row):
    out = read_topology(_pages_from(row), part_number=row["part_number"])
    exp = row["expected"]
    assert sorted(out["topologies"]) == sorted(exp["topologies"]), (
        f"{row['part_number']}: topologies {out['topologies']} != {exp['topologies']}")
    assert out["integration_class"] == exp["integration_class"], (
        f"{row['part_number']}: integration {out['integration_class']} "
        f"!= {exp['integration_class']}")
    assert out["isolated"] == exp["isolated"], (
        f"{row['part_number']}: isolated {out['isolated']} != {exp['isolated']}")
    # every non-absent claim must carry evidence with quote + page
    claimed = set()
    if out["topologies"]:
        claimed.add("topology")
    if out["integration_class"]:
        claimed.add("integration_class")
    if out["isolated"] is not None:
        claimed.add("isolated")
    fields = {e["field"] for e in out["evidence"]}
    assert claimed <= fields, f"{row['part_number']}: claim without evidence"
    for e in out["evidence"]:
        assert e.get("quote") and e.get("page") is not None


def test_gold_population_shape():
    rows = _load()
    assert len(rows) == 105
    assert sum(1 for r in rows if r["expected"]["topologies"]) == 71
    assert sum(1 for r in rows if r["expected"]["integration_class"]) == 35


# --- named traps (regression guards for the rules) ------------------------------

def test_trap_figure_boost_word():
    """A stray 'boost' next to figure text must not claim topology."""
    text = ("LM2673 SIMPLIFIED 5V/3Acout 33 feedback boost SR305 ground "
            "current vin softstart LM2673 - 5.0 output switch")
    out = read_topology([{"page": 1, "text": text}], part_number="LM2673")
    assert "boost" not in out["topologies"]


def test_trap_body_controller_word():
    """'Remote controller' in applications must not set integration."""
    text = ("TLV61070A 2.5A Boost Converter With 0.5V Ultra-low Input "
            "Voltage Applications Electronic shelf label Remote controller")
    out = read_topology([{"page": 1, "text": text}], part_number="TLV61070A")
    assert out["integration_class"] is None
    assert out["topologies"] == ["boost"]


def test_trap_boot_recharge_fet():
    out = read_topology(
        [{"page": 1, "text":
          "TPS54561 Step-Down DC-DC Converter Low dropout at light loads "
          "with integrated BOOT recharge FET Adjustable UVLO"}],
        part_number="TPS54561")
    assert out["integration_class"] is None


def test_non_isolated_prints_false():
    out = read_topology(
        [{"page": 1, "text":
          "PTH04T241W 10-A POWER MODULE NON-ISOLATED, WIDE-OUTPUT"}],
        part_number="PTH04T241W")
    assert out["isolated"] is False
    assert out["integration_class"] == "module"


def test_topology_introducer():
    out = read_topology(
        [{"page": 1, "text":
          "UCC24612 Synchronous Rectifier Controller Supports Topologies "
          "such as Active Clamp Flyback, QR, DCM, CCM Flyback and LLC"}],
        part_number="UCC24612")
    assert "flyback" in out["topologies"] and "llc" in out["topologies"]
    assert out["integration_class"] == "controller"


def test_compound_buck_boost_claims_components():
    out = read_topology(
        [{"page": 1, "text":
          "TPS63060 HIGH INPUT VOLTAGE BUCK-BOOST CONVERTER WITH 2A SWITCH"}],
        part_number="TPS63060")
    assert set(out["topologies"]) >= {"buck-boost", "buck", "boost"}
