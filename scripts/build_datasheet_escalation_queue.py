#!/usr/bin/env python3
"""Build the deterministic escalation queue for ambiguous extraction residue.

Reads one local pillar run directory and one independent external-model
results file for the same work items, classifies every work item with
mechanical, source-derived rules only (frozen labels are never read), and
emits an immutable escalation bundle:

- queue.json       per-work-item decision record
- candidates.jsonl sealed harness.electronics-frontier-candidate.v1 entries
  ready for scripts/datasheet_frontier_batch.py prepare

Escalation flags (no ground truth involved):
- cross_source_disagreement: local and external pin sets differ
- external_model_failure: external output missing or unparsable
- terminal_local_attempt: a local attempt ended in a terminal status
- package_undercount: returned physical rows are fewer than the printed
  package pin count, which may mean the focused crop is a partial table

This tool only prepares evidence; it never submits a batch and never
writes training pairs.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harness.electronics.frontier_batch import (  # noqa: E402
    COMPLETED_LOCAL_STATUSES,
    TERMINAL_LOCAL_STATUSES,
    FrontierCandidate,
    FrontierEvidence,
    LocalAttempt,
    candidate_identity_payload,
    verify_candidate_identity,
)
from harness.electronics.local_model import RESPONSE_SCHEMAS  # noqa: E402

SCRIPT_ROOT = Path(__file__).resolve().parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from compare_datasheet_frontier import _pairs  # noqa: E402

QUEUE_SCHEMA = "harness.datasheet-escalation-queue.v1"
CANDIDATES_SCHEMA = "harness.datasheet-escalation-candidates.v1"

CAPABILITY_RULES = {
    "pin_or_ball": (
        "Return one row per visibly printed physical pin or ball. Never "
        "combine multiple identifiers into one row and never fill a sequence "
        "that is not printed. Keep package variants isolated. Set type and "
        "dir to null and functions to [] unless those values are printed in "
        "the same selected table."
    ),
    "pin_semantics": (
        "Return one row per visibly printed physical pin or ball. Never "
        "combine multiple identifiers into one row. Copy type, direction, "
        "functions, and descriptions only when printed for that same row; "
        "otherwise use null or an empty list. Keep package variants isolated."
    ),
}


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text().splitlines()
        if line.strip()
    ]


def _candidate_pairs(value: dict[str, Any] | None) -> set[tuple[str, str]]:
    if not isinstance(value, dict):
        return set()
    return _pairs(value.get("pins") or [])


def classify(
    *,
    local_pins: set[tuple[str, str]],
    external_pins: set[tuple[str, str]],
    external_ok: bool,
    local_statuses: set[str],
) -> tuple[bool, list[str]]:
    """Mechanical, source-derived escalation flags.

    Frozen labels are never read. Deterministic geometry row counts are not
    used as row-count floors because focused crops legitimately show a
    subset of a package's rows; only set disagreement, terminal local
    attempts, and external failures route to the teacher batch.
    """
    flags: list[str] = []
    if not external_ok:
        flags.append("external_model_failure")
    local_terminal = sorted(local_statuses & TERMINAL_LOCAL_STATUSES)
    if local_terminal:
        flags.append("terminal_local_attempt:" + "|".join(local_terminal))
    if local_pins and external_pins and local_pins != external_pins:
        flags.append("cross_source_disagreement")
    return bool(flags), flags


def build_candidate(
    *,
    work_id: str,
    capability: str,
    document_sha256: str,
    package: str,
    expected_package_pins: int | None,
    image_meta: dict[str, Any],
    image_path: Path,
    attempts: list[dict[str, Any]],
) -> FrontierCandidate:
    rules = CAPABILITY_RULES[capability]
    scope_sentence = (
        f"Package scope: the printed page heading and structural analysis "
        f"indicate the requested package is {package}"
    )
    if expected_package_pins:
        scope_sentence += (
            f" ({expected_package_pins} printed pin positions for the full "
            "package; the focused crop may show a contiguous subset of rows)"
        )
    scope_sentence += "."
    prompt = (
        f"Extract capability={capability} from the supplied datasheet page. "
        "Use only printed evidence. Preserve units and package identity. Use "
        "null for absent scalar values. Return JSON only.\n\n"
        f"Capability rules: {rules}\n\n{scope_sentence}"
    )
    image_bytes = image_path.read_bytes()
    meta = image_meta
    width = float(meta.get("width") or (meta.get("clip_bbox") or [0, 0, 0, 0])[2] - (meta.get("clip_bbox") or [0, 0, 0, 0])[0])
    height = float(meta.get("height"))
    image_tokens = int(width * height / 750)
    estimated = len(prompt) // 4 + image_tokens + 64
    local_attempt_models = [
        LocalAttempt(
            provider="local",
            model=str(row["model"]),
            status=str(row["status"]),
            receipt_sha256=str(row["receipt_sha256"]),
            output_sha256=row.get("output_sha256"),
            reason=str(row.get("reason") or f"{row.get('stage')} attempt receipt"),
        )
        for row in attempts
        if row.get("status") in COMPLETED_LOCAL_STATUSES
    ]
    bbox = meta.get("clip_bbox")
    evidence = [
        FrontierEvidence(
            path=image_path,
            sha256=str(meta["sha256"]),
            media_type="image/png",
            page_1based=int(meta["page_1based"]),
            bbox=tuple(bbox) if bbox else None,
        )
    ]
    provisional = FrontierCandidate(
        candidate_id="candidate-" + "0" * 32,
        capability=capability,
        document_sha256=document_sha256,
        entity_hint=work_id,
        prompt=prompt,
        response_schema=RESPONSE_SCHEMAS[capability],
        evidence=evidence,
        local_attempts=local_attempt_models,
        estimated_input_tokens=max(estimated, 1),
        max_output_tokens=8192,
    )
    payload = candidate_identity_payload(provisional)
    real_id = "candidate-" + hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    ).hexdigest()[:32]
    candidate = provisional.model_copy(update={"candidate_id": real_id})
    verify_candidate_identity(candidate)
    return candidate


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pillar-dir", type=Path, required=True)
    parser.add_argument("--external-results", type=Path, required=True)
    parser.add_argument("--work-queue", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    args = parser.parse_args()

    pillar = args.pillar_dir.resolve()
    local_results = {
        row["work_id"]: row
        for row in _load_jsonl(pillar / "local-results.jsonl")
    }
    attempts = _load_jsonl(pillar / "attempts.jsonl")
    attempts_by_work: dict[str, list[dict[str, Any]]] = {}
    for row in attempts:
        attempts_by_work.setdefault(row["work_id"], []).append(row)
    manifest = json.loads((pillar / "manifest.json").read_text())
    images_by_sha = {
        meta["sha256"]: (name, meta)
        for name, meta in manifest["images"].items()
    }
    work_queue = json.loads(args.work_queue.read_text())
    scope_by_work = {
        item["page_evidence_sha256"]: item for item in work_queue["work"]
    }
    external = {
        row["work_id"]: row
        for row in json.loads(args.external_results.read_text())
    }

    decisions: list[dict[str, Any]] = []
    escalated_ids: list[str] = []
    for work_id, local_row in sorted(local_results.items()):
        scope = scope_by_work.get(local_row.get("page_evidence_sha256"))
        if scope is None:
            continue
        package_scope = (scope.get("structural_evidence") or {}).get(
            "package_scope"
        ) or {}
        expected = package_scope.get("expected_package_pins")
        package = package_scope.get("package")
        statuses = {
            row["status"]
            for row in attempts_by_work.get(work_id, [])
            if row.get("status")
            and row.get("receipt_sha256")
            in set(local_row.get("attempt_receipts") or [])
        } or {
            row["status"]
            for row in attempts_by_work.get(work_id, [])
            if row.get("status")
        }
        local_pins = _candidate_pairs(local_row.get("result"))
        ext_row = external.get(work_id) or {}
        ext_ok = bool(ext_row.get("glm_output"))
        ext_pins = _candidate_pairs(ext_row.get("glm_output"))
        escalate, flags = classify(
            local_pins=local_pins,
            external_pins=ext_pins,
            external_ok=ext_ok,
            local_statuses=statuses,
        )
        if not package_scope.get("column_header"):
            flags.append("note:unlocalized_package_column")
        decisions.append(
            {
                "work_id": work_id,
                "capability": local_row["capability"],
                "package": package,
                "expected_package_pins": expected,
                "local_stage": local_row["local_pillar_stage"],
                "local_attempt_statuses": sorted(statuses),
                "local_rows": len(local_pins),
                "external_rows": len(ext_pins) if ext_ok else None,
                "agreement": (
                    "exact"
                    if local_pins and local_pins == ext_pins
                    else ("disagreement" if local_pins and ext_pins else "unresolved")
                ),
                "escalate": escalate,
                "flags": flags,
            }
        )
        if escalate:
            escalated_ids.append(work_id)

    out_dir = args.output_directory.resolve()
    if out_dir.exists() or out_dir.is_symlink():
        raise SystemExit(f"immutable output already exists: {out_dir}")
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir()

    candidates = []
    candidate_rows = []
    for work_id in escalated_ids:
        local_row = local_results[work_id]
        ext_row = external[work_id]
        ev_sha = local_row.get("evidence_image_sha256")
        name, meta = images_by_sha[ev_sha]
        image_path = pillar / "images" / name
        candidate = build_candidate(
            work_id=work_id,
            capability=local_row["capability"],
            document_sha256=str(scope_by_work[local_row["page_evidence_sha256"]]["document_sha256"]),
            package=str(scope_by_work[local_row["page_evidence_sha256"]]["structural_evidence"]["package_scope"]["package"]),
            expected_package_pins=scope_by_work[local_row["page_evidence_sha256"]]["structural_evidence"]["package_scope"].get("expected_package_pins"),
            image_meta=meta,
            image_path=image_path,
            attempts=attempts_by_work.get(work_id, []),
        )
        candidates.append(candidate)
        candidate_rows.append(candidate.model_dump(mode="json", by_alias=True))

    queue = {
        "schema": QUEUE_SCHEMA,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "pillar_dir": str(pillar),
        "external_results": str(args.external_results.resolve()),
        "classification_rules": (
            "mechanical source-derived flags only; frozen labels never read"
        ),
        "items": decisions,
            "summary": {
                "work_items": len(decisions),
                "escalated": len(escalated_ids),
                "replaced_by_agreement": sum(
                    1 for d in decisions if not d["escalate"]
                ),
                "unlocalized_package_column_notes": sum(
                    1
                    for d in decisions
                    if any(f.startswith("note:") for f in d["flags"])
                ),
                "escalation_flag_counts": {
                    flag.split(":")[0]: sum(
                        1
                        for d in decisions
                        if d["escalate"]
                        and any(
                            f.split(":")[0] == flag.split(":")[0]
                            for f in d["flags"]
                        )
                    )
                    for flag in sorted(
                        {f for d in decisions for f in d["flags"] if not f.startswith("note:")}
                    )
                },
            },
    }
    (out_dir / "queue.json").write_text(
        json.dumps(queue, indent=2, sort_keys=True) + "\n"
    )
    (out_dir / "candidates.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in candidate_rows)
    )
    print(json.dumps(queue["summary"], sort_keys=True))
    print(f"escalated work items: {len(escalated_ids)}")
    print(f"candidates -> {out_dir / 'candidates.jsonl'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
