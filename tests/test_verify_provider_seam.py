"""RUST-CUTOVER-02 rollback and failure tests for the verify seam.

Covers the four required demonstrations:
1. Rust verification failure fails CLOSED (recorded disposition, no silent
   fallthrough to unverified claims).
2. No claim disappears without a recorded disposition.
3. No source SHA or page locator changes (the seam cannot touch them).
4. The Python path restores without migration (env flip only).

Runs against the worktree's seam + the release dw-verify binary; the
reference comparison uses harness.pipeline.quote_verify directly.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

DW_VERIFY = REPO / "designwins/target/release/dw-verify"

CLAIMS = [
    {"symbol": "VIN", "value": 16, "unit": "V",
     "quote": "16-V VIN, 25-V FET", "column_header": "Features",
     "row_header": "r", "doc_sha256": "a" * 64, "page": "3"},
    {"symbol": "BAD", "value": 99, "quote": "absent quote"},
    {"symbol": "UNQUOTED", "value": 5, "quote": ""},
    {"symbol": "NOVALUE", "value": None, "quote": "16-V VIN"},
]
TEXT = "Features: 16-V VIN, 25-V FET support"


def _seam():
    from harness.pipeline import verify_provider
    return verify_provider


def test_default_engine_is_python_without_migration():
    seam = _seam()
    assert seam.active_engine() == "python"
    # and the python path is the reference, field for field
    from harness.pipeline import quote_verify as ref
    got = seam._verdicts_python(CLAIMS, TEXT)
    want = []
    for i, c in enumerate(CLAIMS):
        if not isinstance(c, dict) or c.get("value") is None:
            continue
        q = c.get("quote") or ""
        if not q:
            want.append({"i": i, "verdict": "UNSUPPORTED",
                         "reason": "unquoted", "severity": "P0"})
            continue
        ok = ref.find_quote(q, TEXT, field=seam._alias_field(c.get("symbol", ""))) \
            and ref.value_in_quote(c.get("value"), q)
        want.append({"i": i, "verdict": "SUPPORTED" if ok else "UNSUPPORTED",
                     "reason": None if ok else "quote_not_in_doc",
                     "severity": "P2" if ok else "P0"})
    assert got == want


def test_rust_engine_matches_python_on_probe_corpus():
    seam = _seam()
    if not DW_VERIFY.exists():
        pytest.skip("release dw-verify not built")
    py = seam._verdicts_python(CLAIMS, TEXT)
    os.environ["BURN_VERIFY_ENGINE"] = "rust"
    try:
        ru = seam.verdicts(CLAIMS, TEXT)
        q_py = seam._quick_ok_python(CLAIMS[0], TEXT)
        q_ru = seam.quick_ok(CLAIMS[0], TEXT)
    finally:
        del os.environ["BURN_VERIFY_ENGINE"]
    assert py == ru
    assert q_py == q_ru


def test_rust_failure_fails_closed_with_recorded_disposition():
    """A broken dw-verify must raise — and the burn_doc caller wraps that
    into the error skip row exactly like any pipeline exception. Claims
    never pass unverified."""
    seam = _seam()
    os.environ["BURN_VERIFY_ENGINE"] = "rust"
    broken = REPO / "designwins/target/release/dw-verify-broken-missing"
    original = seam.DW_VERIFY
    seam.DW_VERIFY = broken  # does not exist
    try:
        with pytest.raises(seam.VerifyProviderError):
            seam.verdicts(CLAIMS, TEXT)
        # the burn_doc wrapper behavior (run_doc's except clause):
        try:
            seam.verdicts(CLAIMS, TEXT)
        except Exception as exc:  # noqa: BLE001 — mirrors run_doc
            row = {"stem": "X", "skip": f"error:{str(exc)[:60]}"}
        assert row["skip"].startswith("error:")
        assert "dw-verify" in row["skip"] or "not found" in row["skip"]
    finally:
        seam.DW_VERIFY = original
        del os.environ["BURN_VERIFY_ENGINE"]


def test_malformed_dw_verify_output_fails_closed():
    if not DW_VERIFY.exists():
        pytest.skip("release dw-verify not built")
    seam = _seam()
    os.environ["BURN_VERIFY_ENGINE"] = "rust"
    try:
        with pytest.raises(seam.VerifyProviderError):
            # mode that exits non-zero (unknown mode) -> exit 2
            seam._rust_call("bogus_mode", CLAIMS, TEXT)
    finally:
        del os.environ["BURN_VERIFY_ENGINE"]


def test_every_valued_claim_gets_exactly_one_disposition():
    """No claim disappears: verdict count == valued-claim count, indices
    unique, and P0+P2 partition the verdicts (nothing unrecorded)."""
    seam = _seam()
    for engine in ("python", "rust"):
        if engine == "rust" and not DW_VERIFY.exists():
            pytest.skip("release dw-verify not built")
        os.environ["BURN_VERIFY_ENGINE"] = engine
        try:
            vs = seam.verdicts(CLAIMS, TEXT)
        finally:
            del os.environ["BURN_VERIFY_ENGINE"]
        valued = [i for i, c in enumerate(CLAIMS)
                  if isinstance(c, dict) and c.get("value") is not None]
        assert sorted(v["i"] for v in vs) == valued
        assert {v["severity"] for v in vs} <= {"P0", "P2"}
        # burn_doc's invariant: claims == surviving + p0
        assert len(vs) == sum(1 for v in vs if v["severity"] == "P2") \
            + sum(1 for v in vs if v["severity"] == "P0")


def test_seam_cannot_touch_sha_or_page():
    """The verdict schema carries ONLY i/verdict/reason/severity — the
    seam has no channel to alter doc_sha/page, and page attribution stays
    in burn_doc (Python spans geometry, byte-pinned by replay parity)."""
    seam = _seam()
    if not DW_VERIFY.exists():
        pytest.skip("release dw-verify not built")
    os.environ["BURN_VERIFY_ENGINE"] = "rust"
    try:
        vs = seam.verdicts(CLAIMS, TEXT)
    finally:
        del os.environ["BURN_VERIFY_ENGINE"]
    for v in vs:
        assert set(v) == {"i", "verdict", "reason", "severity"}
    # and dw-verify itself never receives or returns provenance fields
    proc = subprocess.run(
        [str(DW_VERIFY)],
        input=json.dumps({"mode": "verdicts", "claims": CLAIMS, "text": TEXT}),
        capture_output=True, text=True, check=True,
    )
    out = json.loads(proc.stdout)
    for v in out["verdicts"]:
        assert set(v) == {"i", "verdict", "reason", "severity"}


def test_restore_python_by_env_flip_only():
    """Reversibility: unset the env and the reference path is active —
    no state, no migration, byte-equal verdicts."""
    seam = _seam()
    os.environ["BURN_VERIFY_ENGINE"] = "rust"
    os.environ.pop("BURN_VERIFY_ENGINE")
    assert seam.active_engine() == "python"
    assert seam.verdicts(CLAIMS, TEXT) == seam._verdicts_python(CLAIMS, TEXT)
