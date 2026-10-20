#!/usr/bin/env python3
"""S6 — Spine telemetry: measure and publish the downstream pipeline stages.

Updates extraction-telemetry/m5.json downstream fields from the actual
spine output files. Every number is measured, never declared.

usage: python3 spine_telemetry.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TELEMETRY = Path("/Users/samkim/extraction-telemetry/m5.json")
NORMALIZED_DIR = Path("/Users/samkim/lane-out/normalized")
BOUND_DIR = Path("/Users/samkim/lane-out/bound")
VERIFIED_DIR = Path("/Users/samkim/lane-out/verified")
COMPARABLE = Path("/Users/samkim/lane-out/comparable.json")


def count_jsonl(path: Path, predicate=None) -> int:
    n = 0
    for line in open(path, errors="replace"):
        try:
            o = json.loads(line)
            if predicate is None or predicate(o):
                n += 1
        except Exception:
            continue
    return n


def main() -> int:
    is_verified = lambda o: (o.get("_verify") or {}).get("verdict") == "verified"
    is_normalized = lambda o: (o.get("_norm") or {}).get("canonical_value") is not None
    is_bound = lambda o: bool(o.get("part_identity"))

    verified = sum(count_jsonl(f, is_verified) for f in VERIFIED_DIR.glob("*.jsonl"))
    normalized = sum(count_jsonl(f, is_normalized) for f in NORMALIZED_DIR.glob("*.jsonl"))
    bound = sum(count_jsonl(f, is_bound) for f in BOUND_DIR.glob("*.jsonl"))
    raw = sum(count_jsonl(f) for f in VERIFIED_DIR.glob("*.jsonl"))
    comp = json.load(open(COMPARABLE)) if COMPARABLE.exists() else {}

    downstream = {
        "raw_rows_unnormalized": raw,
        "verified_claims": verified,
        "normalized_claims": normalized,
        "identity_bound": bound,
        "comparable_accepted": comp.get("total_comparable_pairs"),
        "comparable_parts": comp.get("unique_parts"),
        "comparable_axes": comp.get("axes_with_pairs"),
        "measured_at": datetime.now(timezone.utc).isoformat(),
    }

    # update the telemetry file
    tel = json.load(open(TELEMETRY))
    tel["downstream"] = downstream
    tel["downstream"]["note"] = "measured from lane-out spine stages (S1-S5)"
    TELEMETRY.write_text(json.dumps(tel, indent=1, default=str))

    print(json.dumps(downstream, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
