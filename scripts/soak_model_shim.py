#!/usr/bin/env python3
"""Thin model-call shim for the dw-soak Rust worker (M5 Rust factory soak).

All model behavior — prompts, endpoint chain, parsing, repair protocol — is
imported from the LIVE scripts/burn_aisle.py in the main checkout (the exact
module the Python factory soak runs), so the Rust soak's model lane is
byte-identical to Python's. This process NEVER touches durable state: the
queue, leases, verification, page attribution and outputs all live in the
Rust controller (dw-controller). It is the "thin provider HTTP shell" the
dw-model handoff names — Python only because the HTTP endpoints already
have exactly one client implementation.

Protocol: argv[1] in {propose, repair}; one JSON payload on stdin; one JSON
result on stdout. Exit 0 even on model failure (the nulls carry the
fail-closed semantics); exit 2 on malformed use.
"""

from __future__ import annotations

import importlib.util
import json
import sys

BURN_AISLE = "/Users/samkim/Harnessv1/scripts/burn_aisle.py"

_spec = importlib.util.spec_from_file_location("burn_aisle", BURN_AISLE)
burn = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(burn)


def propose(payload):
    part, text = payload["part"], payload["text"]
    try:
        claims = burn.call(
            burn.STUDENT,
            "power-tables-student-v1",
            [
                {"role": "system", "content": burn.STUDENT_PROMPT},
                {
                    "role": "user",
                    "content": f"Part: {part}\n\nDatasheet pages:\n{text}",
                },
            ],
        )
        tier = "student" if isinstance(claims, list) else None
    except Exception:
        claims, tier = None, None
    if not isinstance(claims, list):
        claims, tier = burn.propose(text, part)
    return {"claims": claims, "tier": tier}


def repair(payload):
    bad, claims, text = payload["bad"], payload["claims"], payload["text"]
    try:
        fixes = burn.call(
            burn.V41,
            "deepseek-v4.1-flash",
            [
                {
                    "role": "system",
                    "content": burn.REPAIR
                    % json.dumps(
                        [{"i": i, "reason": "quote_mismatch"} for i in bad]
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Claims:\n{json.dumps([claims[i] for i in bad])}"
                        f"\n\nText:\n{text}"
                    ),
                },
            ],
        )
    except Exception:
        fixes = None
    if isinstance(fixes, list):
        for f in fixes:
            if isinstance(f, dict) and "i" in f:
                claims[f["i"]] = {
                    **claims[f["i"]],
                    **{k: v for k, v in f.items() if k != "i"},
                }
    return {"claims": claims}


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in ("propose", "repair"):
        print("usage: soak_model_shim.py {propose|repair} < payload.json", file=sys.stderr)
        return 2
    payload = json.load(sys.stdin)
    result = propose(payload) if sys.argv[1] == "propose" else repair(payload)
    sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
