"""dw.finding.v1 — load, validate, digest, and map to a Gas City bead.

Phase 1 of the NEO<->Gas City loop. A finding is an UNTRUSTED work definition
(law inherited from director gh_workloop.py): validation failures downgrade to
refusal with named gaps, never silent acceptance.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "dw.finding.v1"
TASK_ID_RE = re.compile(r"^([A-Z][A-Z0-9]+(?:-[A-Z0-9]+)+[0-9]*)\s*[—-]")
ALLOWLISTED_REPOS = ("outcited/outcitedharness", "outcited/designwins-v2")
NEO_LABEL_PREFIX = "neo-finding:"


class FindingError(ValueError):
    """Validation refusal — .gaps names every missing/invalid field."""


def canonical_bytes(finding: dict[str, Any]) -> bytes:
    """Stable canonical form for digests: sorted keys, no whitespace."""
    return json.dumps(finding, sort_keys=True, separators=(",", ":")).encode()


def digest(finding: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_bytes(finding)).hexdigest()


def validate(finding: dict[str, Any]) -> list[str]:
    """Return list of gaps (empty = valid). Mirrors dw.finding.v1.json."""
    gaps: list[str] = []
    req = ["schema", "finding_id", "task_id", "source_url", "repository",
           "severity", "summary", "reproduction", "expected_behavior",
           "regression_tests", "authorization_state", "owner"]
    for k in req:
        if k not in finding or finding[k] in (None, "", [], {}):
            gaps.append(f"missing required field: {k}")
    if gaps:
        return gaps
    if finding["schema"] != SCHEMA_VERSION:
        gaps.append(f"schema must be {SCHEMA_VERSION}")
    if not re.match(r"^DW-NEO-[A-Z0-9]+-[0-9]+$", str(finding["finding_id"])):
        gaps.append("finding_id must match DW-NEO-<TOPIC>-<NN>")
    if not TASK_ID_RE.match(finding["task_id"] + " —"):
        gaps.append("task_id must match gh_workloop TASK_ID_RE")
    if finding["repository"] not in ALLOWLISTED_REPOS:
        gaps.append(f"repository not allowlisted: {finding['repository']}")
    if finding["severity"] not in ("blocker", "critical", "major", "minor", "info"):
        gaps.append("severity invalid")
    if len(finding["summary"]) > 72:
        gaps.append("summary exceeds 72 chars")
    rt = finding["regression_tests"]
    if not isinstance(rt, dict) or not rt.get("positive") or not rt.get("negative"):
        gaps.append("regression_tests require non-empty positive AND negative lists")
    if finding["authorization_state"] not in ("proposal", "authorized", "refused", "superseded"):
        gaps.append("authorization_state invalid")
    return gaps


def load(path: Path) -> dict[str, Any]:
    f = json.loads(Path(path).read_text())
    gaps = validate(f)
    if gaps:
        raise FindingError("; ".join(gaps))
    return f


def bead_labels(finding: dict[str, Any]) -> list[str]:
    return [
        f"{NEO_LABEL_PREFIX}{finding['finding_id']}",
        f"gh:{finding['repository'].split('/')[1]}",
        f"severity:{finding['severity']}",
        "neo",
    ]


def task_id_slug(finding: dict[str, Any]) -> str:
    """Pure TASK-ID portion ('DW-X-01') from a task_id that may carry a descriptor."""
    m = TASK_ID_RE.match(str(finding["task_id"]) + " —")
    return m.group(1) if m else str(finding["task_id"])


def to_bead_payload(finding: dict[str, Any]) -> dict[str, Any]:
    """Map a validated finding to supervisor BeadCreateInputBody fields.

    The bead is created OPEN and UNASSIGNED — creation queues work; it never
    dispatches an agent (dispatch is gc sling, a separate authorized step).
    """
    return {
        "title": f"{task_id_slug(finding)} — {finding['summary']}",
        "description": (
            f"source: {finding['source_url']}\n"
            f"finding: {finding['finding_id']} (digest {digest(finding)[:16]})\n"
            f"impact: {finding.get('engineering_impact','')}\n\n"
            f"expected: {finding['expected_behavior']}\n\n"
            "reproduction:\n"
            + "\n".join(f"  {i+1}. {s}" for i, s in enumerate(finding["reproduction"]))
            + "\n\nregression (must pass): " + "; ".join(finding["regression_tests"]["positive"])
            + "\nregression (must fail pre-fix): " + "; ".join(finding["regression_tests"]["negative"])
        ),
        "labels": bead_labels(finding),
        "metadata": {
            "neo-finding-id": finding["finding_id"],
            "neo-task-id": task_id_slug(finding),
            "neo-authorization-state": finding["authorization_state"],
            "neo-digest": digest(finding),
        },
    }


def idempotency_key(finding: dict[str, Any], authorization_ref: str) -> str:
    """Deterministic key: same finding + same authorization => same key.

    Distinct authorizations for the same finding intentionally produce
    distinct keys (a re-authorization after refusal is a new submission).
    """
    h = hashlib.sha256(
        f"{finding['finding_id']}|{digest(finding)}|{authorization_ref}".encode()
    ).hexdigest()
    return f"neo-{h[:40]}"
