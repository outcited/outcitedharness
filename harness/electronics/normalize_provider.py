#!/usr/bin/env python3
"""The normalization provider seam (RUST-CUTOVER-02).

The single, isolated integration point through which the discovery
evaluator's condition parsing runs. Mirrors verify_provider exactly:

- engine "python" (DEFAULT): harness.electronics.condition_model
  .parse_condition, imported and called unchanged.
- engine "rust": shells out to the dw-parse bin (dw-normalize's
  parse_condition — replacement-gate proven over 10,270 live conditions,
  0 diffs; ParseOutcome's serde shape IS the reference wire shape).

Activation: BURN_NORMALIZE_ENGINE=rust. Fail-closed on any subprocess
problem (NormalizeProviderError propagates; nothing silently un-normalized
or re-derived). Rollback: unset the env. No data migration.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from harness.electronics import condition_model

REPO = Path(__file__).resolve().parents[2]
DW_PARSE = REPO / "designwins/target/release/dw-parse"


class NormalizeProviderError(RuntimeError):
    """The active normalization engine failed — fail closed."""


def active_engine() -> str:
    return os.environ.get("BURN_NORMALIZE_ENGINE", "python")


def parse_condition(text):
    """The reference symbol, through the active engine."""
    if active_engine() == "rust":
        return _parse_rust(text)
    return condition_model.parse_condition(text)


def _binary() -> Path:
    if DW_PARSE.exists():
        return DW_PARSE
    raise NormalizeProviderError(
        f"dw-parse binary not found at {DW_PARSE} (build: cargo build "
        "--release -p dw-normalize --bin dw-parse)"
    )


def _parse_rust(text):
    payload = json.dumps({"condition": text or ""}, ensure_ascii=False)
    try:
        proc = subprocess.run(
            [str(_binary())],
            input=payload.encode(),
            capture_output=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise NormalizeProviderError(f"dw-parse subprocess: {exc}") from exc
    if proc.returncode != 0:
        raise NormalizeProviderError(
            f"dw-parse exit {proc.returncode}: "
            f"{proc.stderr.decode(errors='replace')[:200]}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise NormalizeProviderError(f"dw-parse output: {exc}") from exc
