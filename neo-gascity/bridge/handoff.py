"""Authorized, idempotent finding -> Gas City handoff with acceptance receipts.

Phase 3 laws (from the integration brief, enforced here):
  * A GitHub comment / finding / TaskStore state is NEVER an authorization.
    Only a Sam approval artifact (schema inherited from seven's
    /etc/harness-director/approvals/) upgrades a finding to dispatchable.
  * Submission is idempotent: deterministic Idempotency-Key + pre-check by
    label + independent read-back. Re-polling or replay cannot duplicate.
  * Acceptance = the READ-BACK bead plus the event-seq receipt, persisted.
    The POST response alone is not a receipt.
  * Bounded retries (network/5xx only), within the same idempotency key.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from . import finding as F
from .gascity_client import ApiError, GasCityClient, now_iso

REQUIRED_APPROVAL_FIELDS = ("project", "action", "recipient", "task_ref",
                            "issuer", "expires_at", "max_actions")
MAX_ATTEMPTS = 3


class Refusal(Exception):
    """Handoff refused pre-API. .reason is machine-readable."""

    def __init__(self, reason: str, detail: str = ""):
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}: {detail}")


def load_approval(path: Path) -> dict[str, Any]:
    try:
        a = json.loads(Path(path).read_text())
    except (FileNotFoundError, json.JSONDecodeError) as e:
        raise Refusal("invalid-approval", f"unreadable artifact {path}: {e}") from None
    missing = [k for k in REQUIRED_APPROVAL_FIELDS if k not in a]
    if missing:
        raise Refusal("invalid-approval", f"missing fields: {missing}")
    return a


def check_authorization(finding: dict, approval: dict, city: str) -> None:
    """Raise Refusal unless the approval covers THIS finding and THIS city."""
    if approval["issuer"] != "sam":
        raise Refusal("unauthorized-issuer", f"issuer={approval['issuer']}")
    if approval["action"] != "gascity-ingest":
        raise Refusal("unauthorized-action", f"action={approval['action']}")
    if approval["recipient"] != city:
        raise Refusal("unauthorized-recipient", f"recipient={approval['recipient']} != city={city}")
    if approval["task_ref"] != finding["task_id"]:
        raise Refusal("task-mismatch", f"approval task_ref={approval['task_ref']} != finding task_id={finding['task_id']}")
    if int(approval.get("expires_at", 0)) < time.time():
        raise Refusal("approval-expired", str(approval.get("expires_at")))
    if int(approval.get("max_actions", 0)) < 1:
        raise Refusal("approval-exhausted", "max_actions < 1")
    if approval.get("payload_digest") not in (None, F.digest(finding)):
        raise Refusal("digest-mismatch", "approval digest does not cover this finding body")
    if finding["authorization_state"] != "authorized":
        raise Refusal("finding-not-authorized",
                      f"finding authorization_state={finding['authorization_state']}")


def existing_bead(client: GasCityClient, city: str, finding: dict) -> dict | None:
    body, _ = client.beads(city, label=f"{F.NEO_LABEL_PREFIX}{finding['finding_id']}")
    items = body.get("items") or []
    return items[0] if items else None


def submit(finding: dict, approval_path: Path, city: str,
           client: GasCityClient, receipts_dir: Path) -> dict[str, Any]:
    """Full handoff. Returns a receipt dict (also written to receipts_dir).

    Receipt states: refused | duplicate | accepted | api-error.
    Only `accepted` means Gas City durably holds the work.
    """
    receipt: dict[str, Any] = {
        "schema": "dw.gascity-acceptance.v1",
        "finding_id": finding["finding_id"],
        "task_id": finding["task_id"],
        "city": city,
        "authorization_ref": "",
        "idempotency_key": "",
        "at": now_iso(),
        "state": "refused",
        "attempts": 0,
    }
    try:
        approval = load_approval(approval_path)
        receipt["authorization_ref"] = str(approval_path)
        check_authorization(finding, approval, city)
    except Refusal as r:
        receipt["refusal"] = {"reason": r.reason, "detail": r.detail}
        return _write(receipt, receipts_dir)

    receipt["idempotency_key"] = F.idempotency_key(finding, str(approval_path))

    # 1. dedup pre-check — an accepted finding is returned, never resubmitted
    try:
        prior = existing_bead(client, city, finding)
    except ApiError as e:
        if e.status == 404:
            prior = None
        else:
            return _api_error(receipt, receipts_dir, e)
    if prior:
        receipt.update(state="duplicate", bead_id=prior.get("id"),
                       readback=_readback(client, city, prior.get("id")))
        return _write(receipt, receipts_dir)

    # 2. idempotent create with bounded retries (network/5xx only)
    payload = F.to_bead_payload(finding)
    last_err: ApiError | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        receipt["attempts"] = attempt
        try:
            body, index = client.create_bead(city, payload, receipt["idempotency_key"])
            bead_id = body.get("id")
            # 3. independent read-back — the POST response is not a receipt
            rb = _readback(client, city, bead_id)
            # 4. event receipt — seq at/after create, from the response cursor
            receipt.update(
                state="accepted", bead_id=bead_id, create_index=index,
                readback=rb,
                events=_event_receipt(client, city, index),
            )
            return _write(receipt, receipts_dir)
        except ApiError as e:
            last_err = e
            if e.status >= 500 or e.status == 429:
                time.sleep(1.5 * attempt)
                continue
            break
    return _api_error(receipt, receipts_dir, last_err)


def _readback(client: GasCityClient, city: str, bead_id: str | None) -> dict | None:
    if not bead_id:
        return None
    try:
        b = client.bead(city, bead_id)
        return {"id": b.get("id"), "status": b.get("status"),
                "title": (b.get("title") or "")[:80],
                "labels": b.get("labels")}
    except ApiError:
        return None


def _event_receipt(client: GasCityClient, city: str, index: str | None) -> dict | None:
    try:
        ev = client.events(city, limit=10)
        items = ev.get("items") or []
        created = [e for e in items if e.get("type") == "bead.created"]
        return {"observed_total": len(items),
                "last_bead_created_seq": created[-1].get("seq") if created else None}
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
