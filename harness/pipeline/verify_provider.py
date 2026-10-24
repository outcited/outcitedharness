#!/usr/bin/env python3
"""The verification provider seam (RUST-CUTOVER-02).

The single, isolated integration point through which burn_aisle's
deterministic verification runs. Two engines behind one function set:

- engine "python" (DEFAULT, byte-identical to the pre-seam inline code):
  harness.pipeline.quote_verify kernels called exactly as burn_doc did.
- engine "rust": shells out to the dw-verify bin (dw-truth kernels —
  replacement-gate proven + RUST-CUTOVER-01 frozen-replay parity 127/127).

Activation: BURN_VERIFY_ENGINE=rust (env, per run). No data migration in
either direction; flipping back is unsetting the env. The Rust path FAILS
CLOSED: any subprocess problem raises VerifyProviderError, which
propagates like any other pipeline exception (the doc lands in the error
disposition — never a silent fallthrough to unverified claims).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from harness.pipeline.quote_verify import (
    column_position_binds as _py_column_position_binds,
    find_quote as _py_find_quote,
    value_in_quote as _py_value_in_quote,
)

REPO = Path(__file__).resolve().parents[2]
DW_VERIFY = REPO / "designwins/target/release/dw-verify"


class VerifyProviderError(RuntimeError):
    """The active verification engine failed — fail closed."""


def active_engine() -> str:
    return os.environ.get("BURN_VERIFY_ENGINE", "python")


def _alias_field(symbol):
    # verbatim burn_aisle._alias_field (the shared alias table)
    s = (symbol or "").lower()
    if "rds" in s:
        return "v_abs_max"
    if "vds" in s or "vin" in s:
        return "vin_max_v"
    if "id" == s.strip() or "iout" in s or "id_" in s:
        return "iout_max_a"
    if "tj" in s or "temp" in s:
        return "temp_max_c"
    return ""


def _quick_ok_python(claim, text):
    return bool(claim.get("quote")) and _py_find_quote(
        claim["quote"], text, field=_alias_field(claim.get("symbol", ""))
    )


def _verdicts_python(claims, text):
    """The pre-seam burn_doc verdict loop, verbatim."""
    verdicts = []
    for i, c in enumerate(claims):
        if not isinstance(c, dict) or c.get("value") is None:
            continue
        q = c.get("quote") or ""
        if not q:
            verdicts.append(
                {"i": i, "verdict": "UNSUPPORTED", "reason": "unquoted",
                 "severity": "P0"})
            continue
        ok = _py_find_quote(
            q, text, field=_alias_field(c.get("symbol", ""))
        ) and _py_value_in_quote(c.get("value"), q)
        if ok and (c.get("column_header") or c.get("row_header")):
            ordinal = _py_column_position_binds(c, q, text)
            if ordinal is False:
                ok = False
        verdicts.append(
            {"i": i, "verdict": "SUPPORTED" if ok else "UNSUPPORTED",
             "reason": None if ok else (
                 "wrong_cell" if not ok and c.get("column_header")
                 else "quote_not_in_doc"),
             "severity": "P2" if ok else "P0"})
    return verdicts


def _dw_verify_binary() -> Path:
    if DW_VERIFY.exists():
        return DW_VERIFY
    raise VerifyProviderError(
        f"dw-verify binary not found at {DW_VERIFY} (build: cargo build -p "
        "dw-truth --bin dw-verify)"
    )


def _rust_call(mode, claims, text, single_claim=None):
    payload = {
        "mode": mode,
        "claims": [single_claim] if single_claim is not None else claims,
        "text": text,
    }
    try:
        proc = subprocess.run(
            [str(_dw_verify_binary())],
            input=json.dumps(payload, ensure_ascii=False).encode(),
            capture_output=True,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise VerifyProviderError(f"dw-verify subprocess: {exc}") from exc
    if proc.returncode != 0:
        raise VerifyProviderError(
            f"dw-verify exit {proc.returncode}: "
            f"{proc.stderr.decode(errors='replace')[:200]}"
        )
    try:
        out = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise VerifyProviderError(f"dw-verify output: {exc}") from exc
    return out


def quick_ok(claim, text) -> bool:
    """Repair-round bad-claim probe through the active engine."""
    if active_engine() == "rust":
        return bool(_rust_call("quick_ok", [], text, single_claim=claim)["ok"])
    return _quick_ok_python(claim, text)


def verdicts(claims, text):
    """burn_doc's verdict loop through the active engine."""
    if active_engine() == "rust":
        return _rust_call("verdicts", claims, text)["verdicts"]
    return _verdicts_python(claims, text)


def engines_available() -> dict:
    return {
        "python": True,
        "rust": DW_VERIFY.exists(),
    }
