#!/usr/bin/env python3
"""Prove a candidate embedder is byte-equivalent to production before trusting it.

Restoring weights is not the same as restoring the service: a stock BAAI/bge-m3
answers the same routes with the same 1024 dims and produces *different*
vectors, which silently invalidates every index built with the fine-tune. This
is the discriminator. It POSTs the same texts to both endpoints and requires
bit-identical output.

Usage:
  python3 verify_equivalence.py \
      --production http://100.81.201.24:8800/v1/embeddings \
      --candidate  http://127.0.0.1:8801/v1/embeddings

Exit 0 only if every vector matches exactly.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import urllib.request

# Datasheet-shaped probes: part numbers, conditions, units, pinouts, the legacy
# API label itself, a one-token string, and a long absolute-maxings paragraph.
TEXTS = [
    "TPS548C26 4.5-V to 18-V input, 8-A synchronous step-down converter",
    "Recommended operating conditions: TA = -40C to 125C, VIN = 12 V",
    "RDS(on) typ 4.5 mohm at VGS = 10 V, ID = 20 A",
    "The device operates from a single 3.3-V supply and draws 120 uA in standby mode.",
    "PIN CONFIGURATION (TOP VIEW) 1 VOUT 2 GND 3 EN 4 VIN",
    "bge-m3-cr-tapes-v1",
    "OK",
    "Absolute maximum ratings: storage temperature -65 to 150 degrees Celsius, "
    "junction temperature 150 degrees Celsius, ESD HBM 2000 V.",
]

EXPECTED_DIM = 1024


def embed(url: str, timeout: float = 120.0) -> tuple[list[list[float]], str]:
    body = json.dumps({"input": TEXTS, "model": "bge-m3-cr-tapes-v1"}).encode()
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.load(resp)
    rows = sorted(data["data"], key=lambda row: row["index"])
    return [row["embedding"] for row in rows], str(data.get("model", "?"))


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--production", default="http://100.81.201.24:8800/v1/embeddings")
    ap.add_argument("--candidate", required=True)
    args = ap.parse_args()

    prod, pmodel = embed(args.production)
    cand, cmodel = embed(args.candidate)

    failures: list[str] = []
    if len(prod) != len(cand):
        failures.append(f"vector count {len(prod)} != {len(cand)}")
    if len(prod[0]) != EXPECTED_DIM:
        failures.append(f"production dim {len(prod[0])} != {EXPECTED_DIM}")
    if len(cand[0]) != len(prod[0]):
        failures.append(f"candidate dim {len(cand[0])} != production {len(prod[0])}")

    exact = sum(1 for a, b in zip(prod, cand) if a == b)
    maxdiff = max(
        (max(abs(x - y) for x, y in zip(a, b)) for a, b in zip(prod, cand)),
        default=float("nan"))
    mincos = min((cosine(a, b) for a, b in zip(prod, cand)), default=0.0)

    print(f"production : {args.production} model={pmodel}")
    print(f"candidate  : {args.candidate} model={cmodel}")
    print(f"dims       : production={len(prod[0])} candidate={len(cand[0])}")
    print(f"texts      : {len(prod)}")
    print(f"identical  : {exact}/{len(prod)}")
    print(f"max |diff| : {maxdiff:.3e}")
    print(f"min cosine : {mincos:.12f}")

    if exact != len(prod):
        failures.append(f"only {exact}/{len(prod)} vectors bit-identical")
    if pmodel != cmodel:
        failures.append(f"model label differs: {pmodel!r} vs {cmodel!r}")

    if failures:
        print("VERDICT: MISMATCH — do not point any lane at this candidate")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("VERDICT: IDENTICAL — candidate is byte-equivalent to production")
    return 0


if __name__ == "__main__":
    sys.exit(main())
