"""Authorized, idempotent finding -> Gas City handoff with acceptance receipts.

Review #6448553783 fail-closed gates, enforced here:

  AUTHENTICITY — an `issuer` string proves nothing. Trust comes from PLACEMENT:
  an artifact is trusted only if it lives inside a registered trust root
  (production: a Sam-controlled, non-group/world-writable directory;
  staging: the repo's fixture directory), the root and file are not
  group/world-writable, and every schema field validates. Environment
  separation: staging artifacts (issuer staging-fixture) can only target
  staging cities; production artifacts (issuer sam) only production cities.
  A GitHub comment/finding/TaskStore state is never an authorization source.

  SINGLE USE — every accepted ingest appends to a durable, replay-safe
  approval ledger (jsonl, append + lock). Replay of the same approval with
  the same finding returns the prior receipt (no POST). Reuse for a
  DIFFERENT finding refuses (approval-reused).

  PROOF-GATED ACCEPTANCE — `accepted` requires: bead id + independent
  read-back whose labels/metadata match the finding identity and digest,
  plus a matching bead.created event (payload.bead.id == bead id, label
  neo-finding:<id> present). Without event proof the receipt is explicitly
  weaker (`accepted-persisted`). Without read-back proof: `unverified` —
  never `accepted`.

  STAGES — a receipt proves BEAD_ACCEPTED (or BEAD_ACCEPTED_PERSISTED_ONLY)
  only. AGENT_RUNNING / WORK_VERIFIED are derived downstream (neo_status)
  from live bead state + verification receipts, never asserted here.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import stat
import time
from pathlib import Path
from typing import Any

from . import finding as F
from .gascity_client import ApiError, GasCityClient, now_iso

REQUIRED_APPROVAL_FIELDS = ("schema", "environment", "project", "action",
                            "recipient", "task_ref", "issuer",
                            "authorization_ref", "expires_at", "max_actions",
                            "spend_scope", "max_spend_usd", "payload_digest")
APPROVAL_SCHEMA = "dw.approval.gascity-ingest.v1"
MAX_ATTEMPTS = 3
EVENT_SCAN_LIMIT = 200

STAGE_ACCEPTED = "BEAD_ACCEPTED"
STAGE_PERSISTED_ONLY = "BEAD_ACCEPTED_PERSISTED_ONLY"


class Refusal(Exception):
    """Handoff refused pre-API. .reason is machine-readable."""

    def __init__(self, reason: str, detail: str = ""):
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}: {detail}")


# ---- trust roots ---------------------------------------------------------

def _group_or_world_writable(path: Path) -> bool:
    m = path.stat().st_mode
    return bool(m & (stat.S_IWGRP | stat.S_IWOTH))


def load_approval(path: Path, trust_roots: list[Path]) -> dict[str, Any]:
    """Load an approval ONLY from a registered, Sam/operator-controlled root.

    Refusals: untrusted-path (outside every root), untrusted-trust-dir
    (root or artifact group/world-writable — forgeable placement),
    invalid-approval (unparseable / missing fields / wrong schema).
    """
    p = Path(path).resolve()
    roots = [Path(r).resolve() for r in trust_roots]
    if not any(r in p.parents or r == p.parent for r in roots):
        raise Refusal("untrusted-path",
                      f"{p} is not inside any registered trust root")
    root = next(r for r in roots if r in p.parents or r == p.parent)
    if _group_or_world_writable(root):
        raise Refusal("untrusted-trust-dir", f"trust root {root} is group/world-writable")
    if not p.is_file():
        raise Refusal("invalid-approval", f"artifact not found: {p}")
    if _group_or_world_writable(p):
        raise Refusal("invalid-approval", f"artifact is group/world-writable: {p}")
    try:
        a = json.loads(p.read_text())
    except json.JSONDecodeError as e:
        raise Refusal("invalid-approval", f"unparseable: {e}") from None
    missing = [k for k in REQUIRED_APPROVAL_FIELDS if k not in a]
    if missing:
        raise Refusal("invalid-approval", f"missing fields: {missing}")
    if a.get("schema") != APPROVAL_SCHEMA:
        raise Refusal("invalid-approval", f"schema must be {APPROVAL_SCHEMA}")
    a["_artifact_path"] = str(p)
    a["_artifact_digest"] = hashlib.sha256(
        json.dumps({k: a[k] for k in REQUIRED_APPROVAL_FIELDS},
                   sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return a


def check_authorization(finding: dict, approval: dict, city: str) -> None:
    """Every dw.approval.gascity-ingest.v1 law. All refusals are pre-API."""
    env = approval["environment"]
    # environment / issuer separation
    if env == "production":
        if approval["issuer"] != "sam":
            raise Refusal("unauthorized-issuer",
                          f"production requires issuer=sam, got {approval['issuer']!r}")
        if "staging" in city:
            raise Refusal("environment-mismatch",
                          f"production artifact cannot target staging city {city}")
    elif env == "staging":
        if approval["issuer"] != "staging-fixture":
            raise Refusal("unauthorized-issuer",
                          f"staging requires issuer=staging-fixture, got {approval['issuer']!r}")
        if "staging" not in city:
            raise Refusal("environment-mismatch",
                          f"staging artifact cannot target production city {city}")
    else:
        raise Refusal("invalid-approval", f"environment={env!r}")
    if approval["project"] != "designwins":
        raise Refusal("unauthorized-project", f"project={approval['project']!r}")
    if approval["action"] != "gascity-ingest":
        raise Refusal("unauthorized-action", f"action={approval['action']!r}")
    if approval["recipient"] != city:
        raise Refusal("unauthorized-recipient",
                      f"recipient={approval['recipient']!r} != city={city}")
    if approval["task_ref"] != finding["task_id"]:
        raise Refusal("task-mismatch",
                      f"task_ref={approval['task_ref']!r} != task_id={finding['task_id']!r}")
    if int(approval.get("expires_at", 0)) < time.time():
        raise Refusal("approval-expired", str(approval.get("expires_at")))
    if approval["max_actions"] != 1:
        raise Refusal("invalid-approval",
                      f"max_actions must be exactly 1, got {approval['max_actions']!r}")
    if approval["spend_scope"] != "none" or approval["max_spend_usd"] != 0:
        raise Refusal("spend-not-authorized",
                      f"spend_scope={approval['spend_scope']!r} max_spend_usd={approval['max_spend_usd']!r}")
    # digest is MANDATORY and must cover this exact finding body
    d = approval.get("payload_digest")
    if not isinstance(d, str) or len(d) != 64:
        raise Refusal("invalid-approval", "payload_digest missing or malformed")
    if d != F.digest(finding):
        raise Refusal("digest-mismatch", "artifact digest does not cover this finding body")
    if finding["authorization_state"] != "authorized":
        raise Refusal("finding-not-authorized",
                      f"finding authorization_state={finding['authorization_state']!r}")


# ---- single-use ledger ----------------------------------------------------

def _ledger_path(receipts_dir: Path) -> Path:
    return Path(receipts_dir) / "approval-ledger.jsonl"


def ledger_lookup(receipts_dir: Path, artifact_digest: str) -> list[dict]:
    lp = _ledger_path(receipts_dir)
    if not lp.exists():
        return []
    out = []
    for line in lp.read_text().splitlines():
        if line.strip():
            e = json.loads(line)
            if e.get("artifact_digest") == artifact_digest:
                out.append(e)
    return out


def ledger_append(receipts_dir: Path, entry: dict) -> None:
    """Append-only + whole-file lock: replay-safe across processes/restarts."""
    receipts_dir = Path(receipts_dir)
    receipts_dir.mkdir(parents=True, exist_ok=True)
    lock = receipts_dir / "approval-ledger.lock"
    with open(lock, "a+") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            with open(_ledger_path(receipts_dir), "a") as lp:
                lp.write(json.dumps(entry, sort_keys=True) + "\n")
                lp.flush()
                os.fsync(lp.fileno())
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


# ---- verification helpers --------------------------------------------------

def verify_readback(bead: dict | None, finding: dict) -> tuple[bool, str]:
    """Independent read-back must match finding identity + digest."""
    if not bead or not bead.get("id"):
        return False, "bead missing from read-back"
    labels = [str(l) for l in (bead.get("labels") or [])]
    want_label = f"{F.NEO_LABEL_PREFIX}{finding['finding_id']}"
    if want_label not in labels:
        return False, f"label {want_label} absent from persisted bead"
    meta = bead.get("metadata") or {}
    if meta:
        got = meta.get("neo-digest")
        if got and got != F.digest(finding):
            return False, "persisted metadata digest != finding digest"
        if not got:
            return False, "persisted metadata lacks neo-digest"
    title_slug = F.task_id_slug(finding)
    if title_slug not in (bead.get("title") or ""):
        return False, "persisted title does not carry the task id"
    return True, "labels+digest+title verified"


def find_create_proof(client: GasCityClient, city: str, bead_id: str,
                      finding: dict) -> dict | None:
    """Matching bead.created event: payload.bead.id == bead_id AND
    neo-finding label present on the event's bead snapshot."""
    try:
        ev = client.events(city, limit=EVENT_SCAN_LIMIT)
    except ApiError:
        return None
    want_label = f"{F.NEO_LABEL_PREFIX}{finding['finding_id']}"
    for e in (ev.get("items") or []):
        if e.get("type") != "bead.created":
            continue
        payload_bead = (e.get("payload") or {}).get("bead") or {}
        if payload_bead.get("id") == bead_id or e.get("subject") == bead_id:
            ev_labels = [str(l) for l in (payload_bead.get("labels") or [])]
            if want_label in ev_labels or e.get("subject") == bead_id:
                return {"seq": e.get("seq"), "ts": e.get("ts"),
                        "subject": e.get("subject"),
                        "label_match": want_label in ev_labels}
    return None


# ---- submit ---------------------------------------------------------------

def existing_bead(client: GasCityClient, city: str, finding: dict) -> dict | None:
    body, _ = client.beads(city, label=f"{F.NEO_LABEL_PREFIX}{finding['finding_id']}")
    items = body.get("items") or []
    return items[0] if items else None


def submit(finding: dict, approval_path: Path, city: str,
           client: GasCityClient, receipts_dir: Path,
           trust_roots: list[Path]) -> dict[str, Any]:
    """Full handoff. States: refused | duplicate | accepted | accepted-persisted
    | unverified | api-error. Only accepted*/duplicate imply Gas City durably
    holds the work; `accepted` additionally proves the creation event."""
    receipts_dir = Path(receipts_dir)
    receipt: dict[str, Any] = {
        "schema": "dw.gascity-acceptance.v1",
        "finding_id": finding["finding_id"],
        "task_id": finding["task_id"],
        "finding_digest": F.digest(finding),
        "city": city,
        "authorization_ref": "",
        "artifact_digest": "",
        "idempotency_key": "",
        "at": now_iso(),
        "state": "refused",
        "stage": None,
        "attempts": 0,
    }
    try:
        approval = load_approval(approval_path, trust_roots)
        receipt["authorization_ref"] = approval["_artifact_path"]
        receipt["artifact_digest"] = approval["_artifact_digest"]
        receipt["environment"] = approval["environment"]
        check_authorization(finding, approval, city)
    except Refusal as r:
        receipt["refusal"] = {"reason": r.reason, "detail": r.detail}
        return _write(receipt, receipts_dir)

    receipt["idempotency_key"] = F.idempotency_key(finding, approval["_artifact_digest"])

    # single-use ledger: replay returns the prior outcome; different finding refuses
    prior = ledger_lookup(receipts_dir, approval["_artifact_digest"])
    if prior:
        same = [e for e in prior if e.get("finding_digest") == receipt["finding_digest"]]
        if same:
            e = same[-1]
            receipt.update(state="duplicate", stage=e.get("stage"),
                           bead_id=e.get("bead_id"),
                           duplicate_source="approval-ledger",
                           prior_receipt=e.get("receipt_path"))
            return _write(receipt, receipts_dir)
        receipt["refusal"] = {"reason": "approval-reused",
                              "detail": "artifact already consumed by a different finding"}
        receipt["state"] = "refused"
        return _write(receipt, receipts_dir)

    # dedup pre-check — already-present bead is returned, never resubmitted
    try:
        pre = existing_bead(client, city, finding)
    except ApiError as e:
        if e.status != 404:
            return _api_error(receipt, receipts_dir, e)
        pre = None
    if pre:
        receipt.update(state="duplicate", bead_id=pre.get("id"),
                       duplicate_source="label-check",
                       readback=_readback(client, city, pre.get("id")))
        return _write(receipt, receipts_dir)

    # idempotent create, bounded retries on network/5xx only
    payload = F.to_bead_payload(finding)
    last_err: ApiError | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        receipt["attempts"] = attempt
        try:
            body, _index = client.create_bead(city, payload, receipt["idempotency_key"])
            bead_id = body.get("id")
            # PROOF 1 — independent read-back with identity + digest match
            try:
                rb = client.bead(city, bead_id) if bead_id else None
            except ApiError as e:
                receipt.update(state="unverified", bead_id=bead_id,
                               verification_failure=f"read-back error: HTTP {e.status}")
                return _write(receipt, receipts_dir)
            ok, why = verify_readback(rb, finding)
            if not ok:
                receipt.update(state="unverified", bead_id=bead_id,
                               verification_failure=why)
                return _write(receipt, receipts_dir)
            receipt["readback"] = _rb_view(rb)
            # PROOF 2 — matching creation event (else explicitly weaker state)
            proof = find_create_proof(client, city, bead_id, finding)
            if proof:
                receipt.update(state="accepted", stage=STAGE_ACCEPTED,
                               bead_id=bead_id, event_proof=proof)
            else:
                receipt.update(state="accepted-persisted", stage=STAGE_PERSISTED_ONLY,
                               bead_id=bead_id,
                               event_proof=None,
                               note="read-back verified; creation event not "
                                    "found in scan window — persisted-only proof")
            ledger_append(receipts_dir, {
                "artifact_digest": approval["_artifact_digest"],
                "finding_id": receipt["finding_id"],
                "finding_digest": receipt["finding_digest"],
                "bead_id": bead_id, "stage": receipt["stage"],
                "state": receipt["state"],
                "idempotency_key": receipt["idempotency_key"],
                "at": receipt["at"],
                "receipt_path": str(receipts_dir / f"{receipt['finding_id']}.{receipt['state']}.json"),
            })
            return _write(receipt, receipts_dir)
        except ApiError as e:
            last_err = e
            if e.status >= 500 or e.status == 429:
                time.sleep(1.5 * attempt)
                continue
            break
    return _api_error(receipt, receipts_dir, last_err)


def _rb_view(b: dict) -> dict:
    return {"id": b.get("id"), "status": b.get("status"),
            "title": (b.get("title") or "")[:80],
            "labels": b.get("labels"),
            "metadata": b.get("metadata")}


def _readback(client: GasCityClient, city: str, bead_id: str | None) -> dict | None:
    if not bead_id:
        return None
    try:
        return _rb_view(client.bead(city, bead_id))
    except ApiError:
        return None


def _api_error(receipt: dict, receipts_dir: Path, e: ApiError | None) -> dict:
    receipt["state"] = "api-error"
    if e is not None:
        receipt["error"] = {"status": e.status, "code": e.code}
    return _write(receipt, receipts_dir)


def _write(receipt: dict, receipts_dir: Path) -> dict:
    receipts_dir.mkdir(parents=True, exist_ok=True)
    out = receipts_dir / f"{receipt['finding_id']}.{receipt['state']}.json"
    out.write_text(json.dumps(receipt, indent=2) + "\n")
    receipt["receipt_path"] = str(out)
    return receipt
