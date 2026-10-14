#!/usr/bin/env python3
"""Generate dw-model bundle gold from the LIVE
`harness.electronics.frontier_batch.prepare_batch_bundle`.

Every case is computed by the reference itself, end to end: candidates are
constructed through the real Pydantic models (so construction-time
validators run), the bundle is written to a FIXED materialisation root
(`/tmp/gold_model_bundle_fixed`) — digests cover root-containing bytes, so
the Rust test materialises the identical tree at the identical path and the
bytes compare exactly — and both the sealed artifacts and the exact
ValueError/FileNotFoundError strings are captured.

Cases pin the traps: exclusive destination, candidate identity
re-derivation, duplicate candidate/custom ids, evidence resolution
(missing file / not regular / outside roots / SHA mismatch), the
spend-cap arithmetic with Python `.6f` message formatting, the empty-batch
guard, and the manifest's indent-2 sorted JSON with Python float repr.

Usage: python3 scripts/gold_model_bundle.py > tests/fixtures/gold/model_bundle_v1.json
"""
from __future__ import annotations

import shutil
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from harness.electronics.frontier_batch import (  # noqa: E402
    FrontierCandidate,
    candidate_id,
    prepare_batch_bundle,
)

FIXED_ROOT = Path("/tmp/gold_model_bundle_fixed")
# A controlled regular file that exists but sits OUTSIDE the allowed root
# (platform-stable, unlike /etc/hostname which macOS lacks).
ALT_ROOT = Path("/tmp/gold_model_bundle_fixed_alt")
OUTSIDE_PATH = ALT_ROOT / "ev" / "outside.png"
OUTSIDE_BYTES = b"outside-roots fixture\n"

PDF_BYTES = b"%PDF-1.4 fixture evidence 002\n"
PNG_BYTES = b"\x89PNG fixture evidence 001 (not parsed, only hashed)\n"


def _wire(**fields: object) -> dict:
    return dict(fields)


def base_candidate_kwargs(root: Path) -> dict:
    return {
        "schema": "harness.electronics-frontier-candidate.v1",
        "purpose": "local_training_pair_generation",
        "document_sha256": "ab" * 32,
        "entity_hint": "LMG1020",
    }


def pin_candidate(root: Path) -> dict:
    kwargs = base_candidate_kwargs(root)
    kwargs.update(
        capability="pin_semantics",
        prompt="Extract pin semantics for LMG1020 page 4 from the rendered page.",
        response_schema={
            "type": "object",
            "required": ["pins"],
            "properties": {"pins": {"type": "array"}},
        },
        evidence=[
            {
                "path": str(root / "ev" / "doc.pdf"),
                "sha256": _sha(PDF_BYTES),
                "media_type": "application/pdf",
                "page_1based": 4,
                "bbox": None,
            },
            {
                "path": str(root / "ev" / "page.png"),
                "sha256": _sha(PNG_BYTES),
                "media_type": "image/png",
                "page_1based": 4,
                "bbox": [1.0, 2.0, 400.0, 300.0],
            },
        ],
        local_attempts=[
            {
                "provider": "local",
                "model": "deepseek-v4.1-flash",
                "status": "passed_evidence_gate",
                "receipt_sha256": "cd" * 32,
                "output_sha256": None,
                "reason": "evidence gate passed",
            }
        ],
        estimated_input_tokens=12000,
        max_output_tokens=6000,
    )
    kwargs["candidate_id"] = candidate_id(_without_id(kwargs))
    return kwargs


def parametrics_candidate(root: Path) -> dict:
    kwargs = base_candidate_kwargs(root)
    kwargs.update(
        capability="parametrics",
        prompt="Extract parametric table rows for LMG1020 page 2.",
        response_schema={
            "type": "object",
            "required": ["facts"],
            "properties": {"facts": {"type": "array"}},
        },
        evidence=[
            {
                "path": str(root / "ev" / "page.png"),
                "sha256": _sha(PNG_BYTES),
                "media_type": "image/png",
                "page_1based": 2,
                "bbox": [10.5, 20.25, 110.5, 220.75],
            }
        ],
        local_attempts=[
            {
                "provider": "local",
                "model": "deepseek-v4.1-flash",
                "status": "schema_failed",
                "receipt_sha256": "ef" * 32,
                "output_sha256": None,
                "reason": "response failed schema gate",
            },
            {
                "provider": "local",
                "model": "deepseek-v4.1-flash",
                "status": "passed_evidence_gate",
                "receipt_sha256": "12" * 32,
                "output_sha256": "34" * 32,
                "reason": "gate passed on retry",
            },
        ],
        estimated_input_tokens=8000,
        max_output_tokens=3000,
    )
    kwargs["candidate_id"] = candidate_id(_without_id(kwargs))
    return kwargs


def _sha(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


def _without_id(wire: dict) -> dict:
    value = dict(wire)
    value.pop("candidate_id", None)
    return value


def materialise(root: Path, files: list[dict]) -> None:
    wipe(root)
    for entry in files:
        target = root / entry["rel"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(bytes(entry["bytes"]))


def wipe(root: Path) -> None:
    """Remove the fixed root, unlocking any sealed bundle left behind.

    A sealed bundle is 0555/0444 by design, so plain rmtree fails silently
    under ignore_errors. Unlock first (the reference's own error path does
    the same: chmod 0755 then rmtree).
    """
    shutil.rmtree(root, ignore_errors=True)
    if root.exists():
        for path in sorted(root.rglob("*"), reverse=True):
            path.chmod(0o755 if path.is_dir() else 0o644)
        root.chmod(0o755)
        shutil.rmtree(root)


def standard_files() -> list[dict]:
    return [
        {"rel": "ev/doc.pdf", "bytes": list(PDF_BYTES)},
        {"rel": "ev/page.png", "bytes": list(PNG_BYTES)},
    ]


def run_case(name: str, files: list[dict], candidates: list[dict], **kwargs) -> dict:
    materialise(FIXED_ROOT, files)
    dest = FIXED_ROOT / kwargs.pop("dest_rel", "bundle")
    params = {
        "model": kwargs.get("model", "qwen3.8-max"),
        "input_price_per_million": kwargs.get("input_price_per_million", 3.0),
        "output_price_per_million": kwargs.get("output_price_per_million", 15.0),
        "batch_discount": kwargs.get("batch_discount", 0.5),
        "spend_cap_usd": kwargs.get("spend_cap_usd", 25.0),
    }
    # Complete input block for BOTH branches so the Rust test can replay any
    # case with its exact triggering conditions.
    case = {
        "kind": "error",
        "name": name,
        "input": {
            "files": files,
            "dest_rel": dest.name,
            "created_at": "2026-10-14T01:02:03",
            "candidates": candidates,
            **params,
        },
    }
    models = [FrontierCandidate.model_validate(c) for c in candidates]
    try:
        prepare_batch_bundle(
            destination=dest,
            candidates=models,
            allowed_roots=[Path(kwargs.get("allowed_root", str(FIXED_ROOT)))],
            created_at=kwargs.get("created_at", datetime(2026, 10, 14, 1, 2, 3)),
            **params,
        )
    except (ValueError, FileNotFoundError, FileExistsError) as exc:
        case["expected_msg"] = f"{type(exc).__name__}: {exc}"
    else:
        case["kind"] = "happy"
        case["expected"] = {
            "manifest": (dest / "manifest.json").read_text(),
            "candidates": (dest / "candidates.jsonl").read_text(),
            "requests": (dest / "requests.jsonl").read_text(),
        }
    case["dest_absent_after"] = not dest.exists()
    return case


def main() -> int:
    import json

    pin = pin_candidate(FIXED_ROOT)
    par = parametrics_candidate(FIXED_ROOT)

    cases: list[dict] = []

    cases.append(run_case("two_candidates_sealed", standard_files(), [pin, par]))

    # Destination guard: pre-existing directory.
    materialise(FIXED_ROOT, standard_files())
    (FIXED_ROOT / "bundle").mkdir()
    dest = FIXED_ROOT / "bundle"
    try:
        prepare_batch_bundle(
            destination=dest,
            candidates=[FrontierCandidate.model_validate(pin)],
            model="qwen3.8-max",
            allowed_roots=[FIXED_ROOT],
            input_price_per_million=3.0,
            output_price_per_million=15.0,
            batch_discount=0.5,
            spend_cap_usd=25.0,
            created_at=datetime(2026, 10, 14, 1, 2, 3),
        )
        raise AssertionError("expected DestinationExists")
    except (ValueError, FileExistsError) as exc:
        cases.append(
            {
                "kind": "error",
                "name": "destination_exists",
                "input": {
                    "files": standard_files(),
                    "dest_rel": "bundle",
                    "precreate_dest": True,
                    "created_at": "2026-10-14T01:02:03",
                    "candidates": [pin],
                    "model": "qwen3.8-max",
                    "input_price_per_million": 3.0,
                    "output_price_per_million": 15.0,
                    "batch_discount": 0.5,
                    "spend_cap_usd": 25.0,
                },
                "expected_msg": f"{type(exc).__name__}: {exc}",
                "dest_absent_after": not dest.exists(),
            }
        )

    cases.append(
        run_case("blank_model", standard_files(), [pin], model="  ")
    )
    cases.append(
        run_case(
            "invalid_price",
            standard_files(),
            [pin],
            input_price_per_million=-1.0,
        )
    )
    cases.append(
        run_case("invalid_discount", standard_files(), [pin], batch_discount=1.5)
    )
    cases.append(run_case("empty_batch", standard_files(), []))
    cases.append(
        run_case("duplicate_candidate", standard_files(), [pin, dict(pin)])
    )

    tampered = dict(pin)
    tampered["candidate_id"] = (
        tampered["candidate_id"][:-1] + ("e" if tampered["candidate_id"][-1] != "e" else "f")
    )
    cases.append(run_case("identity_mismatch", standard_files(), [tampered]))

    wrong_sha = dict(pin)
    wrong_sha["evidence"] = [
        {**pin["evidence"][0], "sha256": "00" * 32},
        pin["evidence"][1],
    ]
    wrong_sha["candidate_id"] = candidate_id(_without_id(wrong_sha))
    cases.append(run_case("evidence_sha_mismatch", standard_files(), [wrong_sha]))

    outside = dict(pin)
    outside["evidence"] = [
        {**pin["evidence"][0], "path": str(OUTSIDE_PATH)},
        pin["evidence"][1],
    ]
    outside["candidate_id"] = candidate_id(_without_id(outside))
    # Materialise the alt-root file too (run_case only wipes FIXED_ROOT).
    wipe(ALT_ROOT)
    (ALT_ROOT / "ev").mkdir(parents=True, exist_ok=True)
    OUTSIDE_PATH.write_bytes(OUTSIDE_BYTES)
    cases.append(run_case("evidence_outside_roots", standard_files(), [outside]))

    not_file = dict(pin)
    not_file["evidence"] = [
        {**pin["evidence"][0], "path": str(FIXED_ROOT)},
        pin["evidence"][1],
    ]
    not_file["candidate_id"] = candidate_id(_without_id(not_file))
    cases.append(run_case("evidence_not_regular_file", standard_files(), [not_file]))

    missing = dict(pin)
    missing["evidence"] = [
        {**pin["evidence"][0], "path": str(FIXED_ROOT / "ev" / "nope.png")},
        pin["evidence"][1],
    ]
    missing["candidate_id"] = candidate_id(_without_id(missing))
    cases.append(run_case("evidence_missing", standard_files(), [missing]))

    cases.append(
        run_case(
            "cost_cap_exceeded",
            standard_files(),
            [pin, par],
            spend_cap_usd=0.000001,
        )
    )

    print(
        json.dumps(
            {
                "fixed_root": str(FIXED_ROOT),
                "alt_root": str(ALT_ROOT),
                "alt_file": {
                    "rel": "ev/outside.png",
                    "bytes": list(OUTSIDE_BYTES),
                },
                "cases": cases,
            },
            indent=1,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
