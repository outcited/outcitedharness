#!/usr/bin/env python3
"""emit_spine_telemetry.py — stage counters for the Director (seven).

Every counter carries: observation time, exact denominator, source identity.
Absent stage = UNKNOWN, never zero. Output: extraction-telemetry/spine.json
(the directory seven's mirror already consumes).
"""
import json, time
from collections import Counter
from pathlib import Path

SPINE = Path("/Users/samkim/lane-out/spine")
OUT = Path("/Users/samkim/extraction-telemetry/spine.json")


def count_lines(p):
    try:
        with open(p, "rb") as fh:
            return sum(1 for _ in fh)
    except OSError:
        return None


def verdicts(p):
    c = Counter()
    try:
        for line in open(p):
            try:
                c[json.loads(line).get("verdict", "?")] += 1
            except json.JSONDecodeError:
                c["unparsable"] += 1
        return dict(c)
    except OSError:
        return None


def main():
    l1 = count_lines(SPINE / "claims-l1.jsonl")
    l2_pop = l1
    l2_verdicts = verdicts(SPINE / "full" / "l2-ledger.jsonl") or \
                  verdicts(SPINE / "pilot2" / "l2-ledger.jsonl")
    l3 = count_lines(SPINE / "full" / "l3-normalized.jsonl") or \
         count_lines(SPINE / "pilot2" / "l3-normalized.jsonl")
    l4 = SPINE / "full" / "l4-bound.jsonl"
    if not l4.exists():
        l4 = SPINE / "pilot2" / "l4-bound.jsonl"
    tiers = Counter()
    if l4.exists():
        for line in l4.open():
            try:
                tiers[json.loads(line).get("tier", "?")] += 1
            except json.JSONDecodeError:
                pass
    rec = {
        "schema": "harness.spine-telemetry.v1",
        "observed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "stages": {
            "l1_raw_unique": {"count": l1, "denominator": "input_rows_deduped",
                              "source": "spine/claims-l1.jsonl"},
            "l2_verification": {
                "population": l2_pop,
                "processed": sum(l2_verdicts.values()) if l2_verdicts else None,
                "verdicts": l2_verdicts or "UNKNOWN",
                "source": "spine/full/l2-ledger.jsonl (fallback pilot2)"},
            "l3_normalization": {
                "count": l3,
                "denominator": (l2_verdicts or {}).get("verified"),
                "source": "spine/{full,pilot2}/l3-normalized.jsonl"},
            "l4_identity": {
                "tiers": dict(tiers) or "UNKNOWN",
                "denominator": l3,
                "source": str(l4)},
            "l5_comparability": {"count": "UNKNOWN", "note": "fixtures pending pilot pairs"},
        },
        "notes": ["absent stages reported UNKNOWN, not zero",
                  "l2 verdicts carry reasons per claim in the ledger"],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    # MERGE, never wholesale-overwrite: wave2_checkpoint._telemetry and other
    # lane writers keep sibling keys in this same feed file (a wholesale write
    # here erased the wave2_expansion state on 2026-10-21 — observed live).
    prev = json.loads(OUT.read_text()) if OUT.exists() else {}
    prev.update(rec)
    OUT.write_text(json.dumps(prev, indent=1, sort_keys=True))
    print(json.dumps(rec["stages"], indent=1, sort_keys=True))


if __name__ == "__main__":
    main()
