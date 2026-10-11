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


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "condition"
    if mode != "condition":
        print(f"unknown mode: {mode}", file=sys.stderr)
        return 2

    # Late import so the reference is only loaded when used.
    sys.path.insert(0, ".")
    from harness.electronics.condition_model import parse_condition

    for line in sys.stdin:
        subject = line.rstrip("\n")
        if subject == "":
            # The contract is one output per input, including empty strings.
            sys.stdout.write(json.dumps(parse_condition(""), ensure_ascii=False) + "\n")
            continue
        out = parse_condition(subject)
        sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
