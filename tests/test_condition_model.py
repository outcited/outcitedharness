"""Condition-model gold tests and comparator contract.

Gold: tests/fixtures/gold/condition_model_v1.jsonl (167 hand-labeled real
corpus strings, sampled from burn-power-v1 by scripts/sample_condition_gold.py,
labeled by scripts/build_condition_gold_fixture.py). Misparse here is a
fixture-priority failure: the label wins, the parser is fixed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness.electronics.condition_model import (
    condition_covers,
    parse_condition,
)

FIXTURE = Path(__file__).parent / "fixtures" / "gold" / "condition_model_v1.jsonl"


def _load():
    rows = [json.loads(l) for l in FIXTURE.read_text().splitlines() if l.strip()]
    assert rows, "fixture missing"
    return rows


def _close(a, b) -> bool:
    if isinstance(a, list) != isinstance(b, list):
        return False
    if isinstance(a, list):
        return len(a) == len(b) and all(_close(x, y) for x, y in zip(a, b))
    return abs(float(a) - float(b)) <= 1e-9 * max(1.0, abs(float(a)), abs(float(b)))


def _compare(parsed: dict, expected: dict) -> list[str]:
    errs = []
    if "reject" in expected:
        if parsed.get("reject") != expected["reject"]:
            errs.append(f"reject {parsed.get('reject')!r} != {expected['reject']!r}")
        return errs
    if "reject" in parsed:
        errs.append(f"unexpected reject {parsed['reject']!r}")
        return errs
    pk, ek = parsed.get("keys", {}), expected.get("keys", {})
    for k in sorted(set(pk) | set(ek)):
        if k not in pk:
            errs.append(f"keys.{k}: missing (expected {ek[k]!r})")
        elif k not in ek:
            errs.append(f"keys.{k}: unexpected {pk[k]!r}")
        elif not _close(pk[k], ek[k]):
            errs.append(f"keys.{k}: {pk[k]!r} != {ek[k]!r}")
    if (parsed.get("mode") or None) != (expected.get("mode") or None):
        errs.append(f"mode {parsed.get('mode')!r} != {expected.get('mode')!r}")
    for field in ("approx", "sym", "bare", "comparators", "residue"):
        pv = parsed.get(field) or []
        ev = expected.get(field) or []
        if sorted(map(str, pv)) != sorted(map(str, ev)):
            errs.append(f"{field} {pv!r} != {ev!r}")
    return errs


@pytest.mark.parametrize("row", _load(), ids=lambda r: r["condition_verbatim"][:60])
def test_gold_condition(row):
    parsed = parse_condition(row["condition_verbatim"])
    errs = _compare(parsed, row["expected"])
    assert not errs, f"{row['condition_verbatim']!r}: {'; '.join(errs)}"


def test_gold_population_shape():
    rows = _load()
    rejects = [r for r in rows if "reject" in r["expected"]]
    typed = [r for r in rows if "keys" in r["expected"]
             and r["expected"]["keys"]]
    assert len(rows) == 167
    assert len(rejects) == 26
    assert len(typed) >= 135


# --- comparator contract ------------------------------------------------------

def test_covers_exact():
    assert condition_covers({"vgs_v": 0.0, "vds_v": 25.0, "f_hz": 1e6},
                            {"vgs_v": 0.0, "vds_v": 25.0, "f_hz": 1e6}) == "exact"


def test_covers_superset():
    assert condition_covers({"vgs_v": 0.0, "vds_v": 25.0, "f_hz": 1e6},
                            {"vgs_v": 0.0}) == "covers"


def test_covers_partial():
    # claim published without temperature; requirement asks 85°C
    assert condition_covers({"vgs_v": 0.0},
                            {"vgs_v": 0.0, "ta_c": 85.0}) == "partial"


def test_covers_mismatch():
    # spec law: efficiency at 25°C evidence vs 85°C requirement -> mismatch,
    # reported as condition mismatch, never as value failure
    assert condition_covers({"ta_c": 25.0}, {"ta_c": 85.0}) == "mismatch"


def test_covers_absent():
    assert condition_covers(None, {"ta_c": 85.0}) == "absent"
    assert condition_covers({}, {"ta_c": 85.0}) == "absent"
    assert condition_covers({"vgs_v": 0.0}, {"ta_c": 85.0}) == "absent"


def test_covers_range_overlap():
    assert condition_covers({"vgs_v": [0.0, 10.0]}, {"vgs_v": 4.5}) == "covers"
    assert condition_covers({"vgs_v": [0.0, 10.0]}, {"vgs_v": 12.0}) == "mismatch"
    assert condition_covers({"vgs_v": [0.0, 18.0]}, {"vgs_v": [0.0, 10.0]}) == "covers"


def test_spec_example_efficiency_condition():
    """The spec section-7 case: 94% at 25C only, requirement at 85C."""
    claim = parse_condition("VIN=24 V, VOUT=5 V, IOUT=3 A, TA=25°C")
    assert claim["keys"]["ta_c"] == 25.0
    assert condition_covers(claim["keys"], {"ta_c": 85.0}) == "mismatch"
    # and the untyped vendor string is honest about what it lacks
    untyped = parse_condition("-")
    assert untyped == {"reject": "dash"}
    assert condition_covers(untyped.get("keys"), {"ta_c": 85.0}) == "absent"
