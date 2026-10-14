"""E4/T-N7 — stamps only from measured coverage.

The law: a stamp is a pure function of the measured fraction. Nothing else
may raise it (no authored confidence, no wishful cohorts) and extraction
growth flips it mechanically at the floor.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]
                       / "designwins/crates/dw-extract/tools"))
from stamps_from_universe import ACTIVATION_FLOOR, stamp_from_coverage  # noqa: E402


def test_floor_mechanics():
    assert stamp_from_coverage(0.5, "at floor") == "ProvenFit"
    assert stamp_from_coverage(0.9, "rich") == "ProvenFit"
    assert stamp_from_coverage(0.4999, "just below") == "Blocked(just below)"
    assert stamp_from_coverage(0.0, "nothing") == "Blocked(nothing)"


def test_negative_no_stamp_exceeds_its_coverage():
    # The measured chips (phrase.rs seed table) as negative controls:
    # bluetooth at 0.186 stays Blocked even though "almost unmarked" reads close.
    assert stamp_from_coverage(0.186, "nordic almost unmarked").startswith("Blocked")
    # 0.506 was the closest to the floor that still cleared it.
    assert stamp_from_coverage(0.506, "closest") == "ProvenFit"


def test_growth_flips_mechanically():
    before = stamp_from_coverage(0.186, "x")
    after = stamp_from_coverage(0.52, "x")  # extraction growth
    assert before.startswith("Blocked") and after == "ProvenFit"


def test_floor_constant_is_the_laws():
    assert ACTIVATION_FLOOR == 0.5
