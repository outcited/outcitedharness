"""Freeze M4 decision-engine journey answers as immutable contract fixtures.

DISCOVERY-01 §3.1/§9: the M4 decision API (:8793, CURVE-08B OpenAPI) is NOT
available on this machine; what IS available is the CURVE-08 engine
(branch curve/08-decision-engine @ 23b17634) as a library over its frozen
evidence bundle (curve_evidence_bundle_v3). This script consumes that
frozen artifact read-only — no merge, no modification of the M4 worktree —
and pins the journey answers as DISCOVERY-01 contract fixtures.

Journey values (SiC461 93.94 etc.) are REGENERATED here, never hardcoded in
production logic. Re-running against the same M4 commit + bundle must
produce byte-identical fixtures (determinism asserted by tests).

Usage:
  scripts/discovery01_freeze_m4_journeys.py \
      --m4-root /Users/samkim/Harnessv1-curve08
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

QUESTION_A = {
    "requirements": {"vin_min": 48.0, "vin_max": 48.0, "iout_min": 0.5,
                     "vout": 5.0},
    "conditions": {"vin_v": 48.0, "vout_v": 5.0},
    "operating_point": {"x": 0.5, "unit": "A"},
    "phenomenon": "efficiency_vs_load",
}
QUESTION_B = {**QUESTION_A,
              "requirements": {**QUESTION_A["requirements"],
                               "iout_min": 3.0},
              "operating_point": {"x": 3.0, "unit": "A"}}
QUESTION_C = {
    "requirements": {"vin_min": 24.0, "vin_max": 24.0, "iout_min": 1.0,
                     "vout": 3.3},
    "conditions": {"vin_v": 24.0, "vout_v": 3.3},
    "operating_point": {"x": 1.0, "unit": "A"},
    "phenomenon": "efficiency_vs_load",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--m4-root",
                        default="/Users/samkim/Harnessv1-curve08")
    parser.add_argument("--out", default=str(
        Path(__file__).resolve().parents[1] /
        "tests/fixtures/discovery01/m4_frozen_journeys_v1.json"))
    args = parser.parse_args()

    m4_root = Path(args.m4_root).resolve()
    if not (m4_root / "harness/electronics/decision_engine.py").exists():
        print(f"M4 engine not found at {m4_root}", file=sys.stderr)
        return 2
    sys.path.insert(0, str(m4_root))
    from harness.electronics.decision_engine import (  # noqa: E402
        DECISION_SCHEMA, answer_question)

    commit = subprocess.run(
        ["git", "-C", str(m4_root), "rev-parse", "HEAD"],
        capture_output=True, text=True).stdout.strip()
    branch = subprocess.run(
        ["git", "-C", str(m4_root), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True).stdout.strip()
    bundle_path = m4_root / "tests/fixtures/m4-handoff" / \
        "curve_evidence_bundle_v3.jsonl"
    manifest_path = m4_root / "tests/fixtures/m4-handoff" / \
        "curve_evidence_bundle_v3_manifest.json"
    bundle_sha = hashlib.sha256(bundle_path.read_bytes()).hexdigest()
    manifest = json.loads(manifest_path.read_text())

    journeys = {}
    for name, question in (("A_light_load_0p5A", QUESTION_A),
                           ("B_load_step_3A", QUESTION_B),
                           ("C_insufficient_comparative", QUESTION_C)):
        journeys[name] = {
            "question": question,
            "answer": answer_question(question, repo_root=m4_root),
        }

    fixture = {
        "schema": "harness.discovery01-m4-frozen-journeys.v1",
        "provenance": {
            "m4_repo_root": str(m4_root),
            "m4_branch": branch,
            "m4_commit": commit,
            "decision_schema": DECISION_SCHEMA,
            "bundle_file": bundle_path.name,
            "bundle_sha256": bundle_sha,
            "bundle_id": manifest.get("bundle_id") or manifest.get("id"),
            "regenerated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                            time.gmtime()),
            "note": "generated read-only from the M4 worktree; M4 remains "
                    "sole owner of eligibility and curve mathematics",
        },
        "journeys": journeys,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    canonical = json.dumps(fixture, indent=2, sort_keys=True)
    out.write_text(canonical + "\n")
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    print(json.dumps({
        "out": str(out), "fixture_sha256": digest,
        "m4_commit": commit, "bundle_sha256": bundle_sha[:16],
        "journey_summaries": {
            name: {
                "eligible": len(j["answer"].get("eligible", [])),
                "ineligible": len(j["answer"].get("ineligible", [])),
                "unknown": len(j["answer"].get("unknown", [])),
                "comparisons": len(j["answer"].get("comparison", [])
                                   or j["answer"].get("comparisons", [])),
            } for name, j in journeys.items()},
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
