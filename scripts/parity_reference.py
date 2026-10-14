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


def _claims():
    """`verify_claims` over the whole live catalog (Gate N4 — the 87K claim diff).

    Corpus paths come from env so both engines read identical evidence:
      N4_CLAIMS   path to claims.jsonl   (one claim per line, keyed by `id`)
      N4_DOCTEXT  path to doctext.json   ({sha256: full document text})

    Each subject on stdin is a claim `id`.
    """
    import os

    sys.path.insert(0, ".")
    from harness.pipeline.quote_verify import verify_claims

    claims_path = os.environ["N4_CLAIMS"]
    doctext_path = os.environ["N4_DOCTEXT"]
    by_id: dict[str, dict] = {}
    with open(claims_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            by_id[rec["id"]] = rec
    doctext = json.loads(open(doctext_path, encoding="utf-8").read())

    for line in sys.stdin:
        subject = line.rstrip("\n")
        if not subject:
            sys.stdout.write("[]\n")
            continue
        rec = by_id.get(subject)
        if rec is None:
            sys.stdout.write("[]\n")
            continue
        doc_text = doctext.get(rec["doc_sha256"], "")
        verdicts = verify_claims({rec["field"]: rec["claim"]}, doc_text)
        sys.stdout.write(json.dumps(verdicts, ensure_ascii=False, sort_keys=True) + "\n")


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "condition"
    if mode == "condition":
        _condition()
        return 0
    if mode == "quote":
        _quote()
        return 0
    if mode == "claims":
        _claims()
        return 0
    print(f"unknown mode: {mode}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
