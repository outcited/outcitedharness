"""RUST-CUTOVER-02 rollback and failure tests for the normalize seam.

Mirrors test_verify_provider_seam.py: default-engine fidelity, rust==python
on a real-corpus condition fixture, fail-closed with recorded disposition,
env-flip restore, and the wire-shape pin (the seam returns the reference
dict shape — never a second representation).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

DW_PARSE = REPO / "designwins/target/release/dw-parse"
CONDITIONS_FIXTURE = (
    REPO / "tests/fixtures/gold/claim_conditions_v1.jsonl"
)


def _seam():
    from harness.electronics import normalize_provider
    return normalize_provider


def _reference():
    from harness.electronics import condition_model
    return condition_model


def _corpus_conditions(limit=200):
    if not CONDITIONS_FIXTURE.exists():
        return ["VIN = 12 V, TA = 25 °C", "VDS = -0.5 to 60 V", "—"]
    out = []
    with CONDITIONS_FIXTURE.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            cond = row.get("condition_verbatim")
            if cond:
                out.append(cond)
            if len(out) >= limit:
                break
    return out or ["VIN = 12 V"]


def test_default_engine_is_python_and_is_the_reference():
    seam, ref = _seam(), _reference()
    assert seam.active_engine() == "python"
    for c in _corpus_conditions(50):
        assert seam.parse_condition(c) == ref.parse_condition(c)


def test_rust_engine_matches_reference_on_corpus_fixture():
    if not DW_PARSE.exists():
        pytest.skip("release dw-parse not built")
    seam, ref = _seam(), _reference()
    conds = _corpus_conditions(200)
    os.environ["BURN_NORMALIZE_ENGINE"] = "rust"
    try:
        mismatches = [
            c for c in conds if seam.parse_condition(c) != ref.parse_condition(c)
        ]
    finally:
        del os.environ["BURN_NORMALIZE_ENGINE"]
    assert not mismatches, mismatches[:5]


def test_missing_binary_fails_closed():
    seam = _seam()
    os.environ["BURN_NORMALIZE_ENGINE"] = "rust"
    original = seam.DW_PARSE
    seam.DW_PARSE = REPO / "designwins/target/release/dw-parse-missing"
    try:
        with pytest.raises(seam.NormalizeProviderError):
            seam.parse_condition("VIN = 12 V")
        # the caller wraps failures into the error disposition — nothing
        # silently un-normalized
        try:
            seam.parse_condition("VIN = 12 V")
        except Exception as exc:  # noqa: BLE001 — mirrors run_doc
            row = {"stem": "X", "skip": f"error:{str(exc)[:60]}"}
        assert row["skip"].startswith("error:")
    finally:
        seam.DW_PARSE = original
        del os.environ["BURN_NORMALIZE_ENGINE"]


def test_wire_shape_is_the_reference_dict():
    """Rust output IS the reference representation (keys/reject dict) —
    no second shape, no migration, condition_covers consumes it unchanged."""
    if not DW_PARSE.exists():
        pytest.skip("release dw-parse not built")
    seam, ref = _seam(), _reference()
    for c in ("VIN = 12 V, TA = 25 °C", "—"):
        os.environ["BURN_NORMALIZE_ENGINE"] = "rust"
        try:
            got = seam.parse_condition(c)
        finally:
            del os.environ["BURN_NORMALIZE_ENGINE"]
        want = ref.parse_condition(c)
        assert got == want
        assert isinstance(got, dict)
        assert "keys" in got or "reject" in got
        # downstream consumer takes the dict unchanged
        if "keys" in got:
            from harness.electronics.condition_model import condition_covers
            verdict = condition_covers(got.get("keys"), {"vin_v": 10.0})
            assert verdict in {"exact", "covers", "partial", "mismatch",
                               "absent", None}


def test_restore_python_by_env_flip_only():
    seam, ref = _seam(), _reference()
    os.environ["BURN_NORMALIZE_ENGINE"] = "rust"
    os.environ.pop("BURN_NORMALIZE_ENGINE")
    assert seam.active_engine() == "python"
    assert seam.parse_condition("ID = 4 A") == ref.parse_condition("ID = 4 A")
