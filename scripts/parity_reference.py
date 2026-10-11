#!/usr/bin/env python3
"""The Python reference, served over stdin/stdout for the replacement gate.

Reads one subject per line on stdin, writes one JSON value per line on stdout,
same order. `dw-parity::replace::CommandReference` calls exactly this.

That contract is what makes the replacement gate honest: both engines see the
same inputs in the same run, and neither can share state with the other.

Usage::

    echo 'VGS = 0 V' | python3 scripts/parity_reference.py condition
"""
from __future__ import annotations

import json
import sys


CASES = "tests/fixtures/gold/quote_parity"


def _condition():
    sys.path.insert(0, ".")
    from harness.electronics.condition_model import parse_condition

    for line in sys.stdin:
        subject = line.rstrip("\n")
        out = parse_condition(subject)
        sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")


def _quote():
    """`verify_claims` over prepared cases.

    Each subject is a case path relative to `tests/fixtures/gold/quote_parity/`.
    Both engines read the same case file, so neither can be looking at different
    evidence — which is the whole point of the replacement gate.
    """
    sys.path.insert(0, ".")
    from harness.pipeline.quote_verify import verify_claims

    for line in sys.stdin:
        subject = line.rstrip("\n")
        if not subject:
            sys.stdout.write("[]\n")
            continue
        with open(f"{CASES}/{subject}", encoding="utf-8") as fh:
            case = json.load(fh)
        claims = {}
        for row in case["claims"]:
            claims[row["field"]] = row["claim"]
        verdicts = verify_claims(claims, case["doc_text"])
        sys.stdout.write(json.dumps(verdicts, ensure_ascii=False, sort_keys=True) + "\n")


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "condition"
    if mode == "condition":
        _condition()
        return 0
    if mode == "quote":
        _quote()
        return 0
    print(f"unknown mode: {mode}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
