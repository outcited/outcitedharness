#!/usr/bin/env python3
"""Regenerate the frozen API fixtures from the live service.

Run from the repo root:
    .venv/bin/python contracts/engineering-decision-api-v1/generate_fixtures.py

Fixtures are the byte-truth of the contract: tests/test_curve08b.py
fails if the service output drifts from them (deterministic by design —
request_id hashes the request + release identity; no wall-clock).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from harness.electronics.decision_api import (  # noqa: E402
    ApiError,
    handle_decision_request,
)

OUT = Path(__file__).parent / "examples"

JOURNEYS = {
    "journey-a": {
        "category": "power",
        "subcategory": "buck-converters",
        "requirements": {"vin_v": 48, "vout_v": 5, "iout_a": 0.5},
        "comparison": {
            "metric": "efficiency",
            "mode": "condition_matched",
        },
        "evidence_policy": "machine_verified_advisory",
        "include_unknowns": True,
    },
    "journey-b": {
        "category": "power",
        "subcategory": "buck-converters",
        "requirements": {"vin_v": 48, "vout_v": 5, "iout_a": 3.0},
        "comparison": {
            "metric": "efficiency",
            "mode": "condition_matched",
        },
        "evidence_policy": "machine_verified_advisory",
        "include_unknowns": True,
    },
    "journey-c": {
        "category": "power",
        "subcategory": "buck-converters",
        "requirements": {"vin_v": 24, "vout_v": 3.3, "iout_a": 1.0},
        "comparison": {"metric": "efficiency"},
        "evidence_policy": "machine_verified_advisory",
        "include_unknowns": True,
    },
    "ablation-arm-a-parametric-only": {
        "category": "power",
        "subcategory": "buck-converters",
        "requirements": {"vin_v": 48, "vout_v": 5, "iout_a": 0.5},
        "evidence_policy": "machine_verified_advisory",
    },
}

ERRORS = {
    "error-unsupported-metric": {
        "category": "power", "subcategory": "buck-converters",
        "requirements": {"vin_v": 48, "vout_v": 5, "iout_a": 0.5},
        "comparison": {"metric": "ripple"}},
    "error-unsupported-category": {
        "category": "mcu", "subcategory": "buck-converters",
        "requirements": {"vin_v": 48}},
    "error-unknown-candidate": {
        "category": "power", "requirements": {"vin_v": 48},
        "cohort": ["SiC461", "NOT_A_PART"]},
    "error-operating-point-required": {
        "category": "power", "requirements": {"vin_v": 48, "vout_v": 5},
        "comparison": {"metric": "efficiency"}},
    "error-invalid-request": {
        "category": "power", "requirements": {}},
}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, request in JOURNEYS.items():
        (OUT / f"request-{name}.json").write_text(
            json.dumps(request, indent=2) + "\n")
        status, body = handle_decision_request(request)
        assert status == 200, (name, status, body)
        (OUT / f"response-{name}.json").write_text(
            json.dumps(body, indent=2, sort_keys=False) + "\n")
        print(f"froze {name}: {status}")
    for name, request in ERRORS.items():
        try:
            handle_decision_request(request)
        except ApiError as e:
            (OUT / f"{name}.json").write_text(
                json.dumps({"http_status": e.status,
                            "request": request, **e.body()},
                           indent=2) + "\n")
            print(f"froze {name}: {e.status} {e.code}")
        else:
            raise SystemExit(f"{name} did not raise")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
