"""Authorized, idempotent finding -> Gas City handoff with acceptance receipts.

Review #6449336556 gates, enforced here:

  PROVENANCE (P0) — trust is an ownership chain, not a string:
  * an artifact is honored only inside a registered trust root declared in
    bridge/trust.json (staging) or the GC_INGEST_TRUST_PROD root (production);
  * PRODUCTION roots: every ancestor directory is owned by root or the root's
    owner, none is group/world-writable, the root's owner uid MUST differ
    from this process's uid (bridge/agents run as the service user and can
    therefore neither create nor modify approvals), the artifact is a
    regular file owned by the root's owner, and no path component is a
    symlink;
  * STAGING roots: root + artifact owned by the invoking uid (fixtures),
    not group/world-writable, no symlinks;
  * the COMPLETE JSON Schema is applied via bridge.schema (const/enum/
    required/additionalProperties/pattern/allOf/if-then) — not a field list;
  * issuer/placement/mode each remain necessary but none is sufficient.

  ATOMIC SINGLE USE (P2) — lookup, reservation and consumption happen inside
  one flock: concurrent submissions serialize; the first appends a `reserve`
  (the consumption), later same-finding attempts `resume` (same deterministic
  idempotency key -> native Gas City dedup -> at most one bead), completed
  attempts replay the prior receipt with no POST, and any attempt for a
  DIFFERENT finding under the same artifact refuses (approval-reused).
  Crashes between POST and receipt persistence leave the reserve: re-use by
  another finding is impossible; retry of the same finding resumes safely.

  PROOF (P1) — read-back requires non-empty persisted metadata with the
  EXACT finding id, task slug and sha256 digest; event proof requires an
  independently correlated bead.created whose payload bead id matches AND
  whose labels carry neo-finding:<id> (subject-only matches are NOT proof).
  Without event proof: explicitly weaker BEAD_ACCEPTED_PERSISTED_ONLY.
  Without read-back proof: `unverified`, never accepted.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import stat
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from . import finding as F
from . import schema as SC
from .gascity_client import ApiError, GasCityClient, now_iso

APPROVAL_SCHEMA_NAME = "dw.approval.gascity-ingest.v1.json"
FINDING_SCHEMA_NAME = "dw.finding.v1.json"
MAX_ATTEMPTS = 3
EVENT_SCAN_LIMIT = 200

STAGE_ACCEPTED = "BEAD_ACCEPTED"
STAGE_PERSISTED_ONLY = "BEAD_ACCEPTED_PERSISTED_ONLY"

TRUST_CONFIG = Path(__file__).resolve().parent.parent / "bridge" / "trust.json"
PROD_ROOT_ENV = "GC_INGEST_TRUST_PROD"


class Refusal(Exception):
    """Handoff refused pre-API. .reason is machine-readable."""

    def __init__(self, reason: str, detail: str = ""):
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}: {detail}")


# ---- trust configuration ---------------------------------------------------

def load_trust_config() -> dict:
    """Registered trust roots. Production roots may also arrive via env
    (operator-set, not agent-writable): a single absolute path."""
    cfg = {"roots": []}
    if TRUST_CONFIG.exists():
        raw = json.loads(TRUST_CONFIG.read_text())
        cfg["roots"] = raw.get("roots", [])
    prod = os.environ.get(PROD_ROOT_ENV)
    if prod:
        cfg["roots"].append({"path": prod, "environment": "production"})
    return cfg


def _ancestors(path: Path) -> list[Path]:
    out, cur = [], path.resolve()
    while cur != cur.parent:
        out.append(cur)
        cur = cur.parent
    out.append(cur)
    return list(reversed(out))  # /, /Users, ..., root


def _has_symlink_component(root: Path, artifact: Path) -> bool:
    """True if any component from root down to the artifact is a symlink."""
    cur = root.resolve()
    for part in artifact.resolve().relative_to(cur).parts:
        cur = cur / part
        if cur.is_symlink():
            return True
    return artifact.is_symlink() and not artifact.samefile(root)


def _check_root_chain(root: Path, environment: str) -> None:
    """Ownership + permission verification of the trust chain."""
    euid = os.geteuid()
    root = root.resolve()
    if not root.is_dir():
        raise Refusal("untrusted-trust-dir", f"trust root {root} is not a directory")
    st = root.stat()
    if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise Refusal("untrusted-trust-dir", f"trust root {root} is group/world-writable")
    owner = st.st_uid
    if environment == "production":
        # full ancestor chain: owned by root or the trust-root owner, never
        # group/world-writable, and the root owner MUST differ from the
        # bridge/agent service user (euid) — agents cannot write approvals
        if owner == euid:
            raise Refusal("untrusted-trust-dir",
                          f"production root {root} is owned by the invoking uid {euid}; "
                          "approvals must live where service identities cannot write")
        for anc in _ancestors(root)[1:]:
            ast = anc.stat()
            if ast.st_uid not in (0, owner):
                raise Refusal("untrusted-trust-dir",
                              f"ancestor {anc} owned by uid {ast.st_uid}, not {owner}/root")
            if ast.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
                raise Refusal("untrusted-trust-dir",
                              f"ancestor {anc} is group/world-writable")
    else:  # staging fixtures are created and owned by the invoking uid
        if owner != euid:
            raise Refusal("untrusted-trust-dir",
                          f"staging root {root} owned by uid {owner} != invoking {euid}")


def load_approval(path: Path, trust_roots: list[Path] | None = None) -> dict[str, Any]:
    """Load an approval ONLY from a verified trust root, fully schema-checked."""
    p = Path(path)
    # symlink detection FIRST, on the original path (a symlink pointing into
    # the trust root from outside, or aliasing within it, is an untrusted path)
    if p.is_symlink():
        raise Refusal("untrusted-path", f"{p} is a symlink")
    try:
        rp = p.resolve(strict=True)
    except (FileNotFoundError, RuntimeError) as e:
        raise Refusal("invalid-approval", f"unresolvable artifact {p}: {e}") from None
    roots = trust_roots or [Path(r["path"]) for r in load_trust_config()["roots"]]
    matched_env = None
    root_obj = None
    cfg = load_trust_config()
    for r in [Path(x["path"]) for x in cfg["roots"]] + list(roots):
        rr = Path(r).resolve()
        if rr == rp.parent or rr in rp.parents:
            matched_env = next((x["environment"] for x in cfg["roots"]
                                if Path(x["path"]).resolve() == rr), None)
            if matched_env is None:
                matched_env = "production" if "staging" not in rr.name else "staging"
            root_obj = rr
            break
    if root_obj is None:
        raise Refusal("untrusted-path", f"{p} is not inside any registered trust root")

    _check_root_chain(root_obj, matched_env)

    lst = p.lstat()
    if not stat.S_ISREG(lst.st_mode):
        raise Refusal("invalid-approval", "artifact is not a regular file")
    if _has_symlink_component(root_obj, rp):
        raise Refusal("untrusted-path", "symlink in trust path — refusing")
    if lst.st_uid != root_obj.stat().st_uid:
        raise Refusal("untrusted-trust-dir",
                      f"artifact owner uid {lst.st_uid} != trust root owner "
                      f"{root_obj.stat().st_uid}")
    if lst.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise Refusal("invalid-approval", "artifact is group/world-writable")

    try:
        a = json.loads(rp.read_text())
    except json.JSONDecodeError as e:
        raise Refusal("invalid-approval", f"unparseable: {e}") from None

    # COMPLETE schema enforcement (bridge.schema applies every keyword)
    errors = SC.validate(a, APPROVAL_SCHEMA_NAME)
    if errors:
        raise Refusal("invalid-approval", "schema violations: " + "; ".join(errors[:6]))

    a["_artifact_path"] = str(rp)
    a["_environment"] = a["environment"]
    a["_artifact_digest"] = hashlib.sha256(json.dumps(
        {k: a[k] for k in sorted(a) if not k.startswith("_")},
        sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return a


def check_authorization(finding: dict, approval: dict, city: str) -> None:
    """Semantic laws the schema cannot express (cross-field binding)."""
    env = approval["environment"]
    if env == "production":
        if "staging" in city:
            raise Refusal("environment-mismatch",
                          f"production artifact cannot target staging city {city}")
    else:
        if "staging" not in city:
            raise Refusal("environment-mismatch",
                          f"staging artifact cannot target production city {city}")
    if approval["recipient"] != city:
        raise Refusal("unauthorized-recipient",
                      f"recipient={approval['recipient']!r} != city={city}")
    if approval["task_ref"] != finding["task_id"]:
        raise Refusal("task-mismatch",
                      f"task_ref={approval['task_ref']!r} != task_id={finding['task_id']!r}")
    if int(approval.get("expires_at", 0)) < time.time():
        raise Refusal("approval-expired", str(approval.get("expires_at")))
    if approval["payload_digest"] != F.digest(finding):
        raise Refusal("digest-mismatch", "artifact digest does not cover this finding body")
    if finding["authorization_state"] != "authorized":
        raise Refusal("finding-not-authorized",
                      f"finding authorization_state={finding['authorization_state']!r}")


# ---- atomic single-use ledger ----------------------------------------------

LEDGER = "approval-ledger.jsonl"


def _ledger_file(receipts_dir: Path) -> Path:
    return Path(receipts_dir) / LEDGER


@contextmanager
def _ledger_lock(receipts_dir: Path):
    receipts_dir = Path(receipts_dir)
    receipts_dir.mkdir(parents=True, exist_ok=True)
    lf = open(receipts_dir / "approval-ledger.lock", "a+")
    try:
        fcntl.flock(lf, fcntl.LOCK_EX)
        yield lf
    finally:
        fcntl.flock(lf, fcntl.LOCK_UN)
        lf.close()


def _read_ledger(receipts_dir: Path) -> list[dict]:
    lp = _ledger_file(receipts_dir)
    if not lp.exists():
        return []
    out = []
    for line in lp.read_text().splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # torn tail line from a crash mid-append
    return out


def _append_locked(receipts_dir: Path, entry: dict) -> None:
    with open(_ledger_file(receipts_dir), "a") as lp:
        lp.write(json.dumps(entry, sort_keys=True) + "\n")
        lp.flush()
        os.fsync(lp.fileno())


def consume_approval(receipts_dir: Path, approval: dict, finding: dict,
                     idempotency_key: str) -> tuple[str, dict | None]:
    """ATOMIC consumption. Returns ('first'|'resume'|'replay', prior_entry).

    Must be called with the artifact already validated. All ledger reads and
    writes happen under one exclusive lock, so concurrent processes serialize
    and at most one `reserve` is ever written per artifact+finding.
    """
    ad, fd = approval["_artifact_digest"], F.digest(finding)
    with _ledger_lock(receipts_dir):
        entries = _read_ledger(receipts_dir)
        mine = [e for e in entries if e.get("artifact_digest") == ad]
        if any(e.get("finding_digest") != fd for e in mine):
            raise Refusal("approval-reused",
                          "artifact already consumed by a different finding")
        done = [e for e in mine if e.get("kind") == "complete"]
        if done:
            return "replay", done[-1]
        reserved = [e for e in mine if e.get("kind") == "reserve"]
        if reserved:
            return "resume", reserved[-1]
        _append_locked(receipts_dir, {
            "kind": "reserve", "artifact_digest": ad,
            "finding_id": finding["finding_id"], "finding_digest": fd,
            "idempotency_key": idempotency_key, "at": now_iso()})
        return "first", None


def complete_approval(receipts_dir: Path, approval: dict, finding: dict,
                      result: dict) -> None:
    with _ledger_lock(receipts_dir):
        ad, fd = approval["_artifact_digest"], F.digest(finding)
        existing = [e for e in _read_ledger(receipts_dir)
                    if e.get("artifact_digest") == ad and e.get("kind") == "complete"
                    and e.get("finding_digest") == fd]
        if existing:
            return  # already durably completed (concurrent submission race)
        _append_locked(receipts_dir, {
            "kind": "complete", "artifact_digest": approval["_artifact_digest"],
            "finding_id": finding["finding_id"], "finding_digest": F.digest(finding),
            "bead_id": result.get("bead_id"), "state": result.get("state"),
            "stage": result.get("stage"),
            "idempotency_key": result.get("idempotency_key"),
            "receipt_path": result.get("receipt_path"), "at": now_iso()})


# ---- verification (strict, review finding 2) --------------------------------

def verify_readback(bead: dict | None, finding: dict) -> tuple[bool, str]:
    """Read-back must prove the EXACT persisted finding identity and digest.

    Empty/absent metadata fails. Each of finding id, task slug, digest must
    match exactly.
    """
    if not bead or not bead.get("id"):
        return False, "bead missing from read-back"
    labels = [str(l) for l in (bead.get("labels") or [])]
    want_label = f"{F.NEO_LABEL_PREFIX}{finding['finding_id']}"
    if want_label not in labels:
        return False, f"label {want_label} absent from persisted bead"
    meta = bead.get("metadata") or {}
    if not isinstance(meta, dict) or not meta:
        return False, "persisted bead metadata is empty/absent — cannot verify identity"
    if meta.get("neo-finding-id") != finding["finding_id"]:
        return False, (f"persisted neo-finding-id {meta.get('neo-finding-id')!r} != "
                       f"{finding['finding_id']!r}")
    slug = F.task_id_slug(finding)
    if str(meta.get("neo-task-id", "")).strip() != slug:
        return False, f"persisted neo-task-id {meta.get('neo-task-id')!r} != {slug!r}"
    if meta.get("neo-digest") != F.digest(finding):
        return False, "persisted neo-digest != finding digest"
    if slug not in (bead.get("title") or ""):
        return False, "persisted title does not carry the task id"
    return True, "labels+metadata(finding-id/task-id/digest)+title verified"


def find_create_proof(client: GasCityClient, city: str, bead_id: str,
                      finding: dict) -> dict | None:
    """Independently correlated creation event, or None.

    Full proof requires ALL of: event payload bead id == bead_id AND
    neo-finding:<id> label present in the event's own bead snapshot AND
    (when the event carries metadata) its digest matches. A subject-only
    match is NOT proof.
    """
    try:
        ev = client.events(city, limit=EVENT_SCAN_LIMIT)
    except ApiError:
        return None
    want_label = f"{F.NEO_LABEL_PREFIX}{finding['finding_id']}"
    for e in (ev.get("items") or []):
        if e.get("type") != "bead.created":
            continue
        payload_bead = (e.get("payload") or {}).get("bead") or {}
        if payload_bead.get("id") != bead_id:
            continue  # subject-only matches deliberately skipped
        ev_labels = [str(l) for l in (payload_bead.get("labels") or [])]
        if want_label not in ev_labels:
            continue
        meta = payload_bead.get("metadata") or {}
        digest_match = None
        if meta:
            digest_match = meta.get("neo-digest") == F.digest(finding)
            if not digest_match:
                continue
        return {"seq": e.get("seq"), "ts": e.get("ts"),
                "subject": e.get("subject"),
                "label_match": True, "digest_match": digest_match}
    return None


# ---- submit -----------------------------------------------------------------

def existing_bead(client: GasCityClient, city: str, finding: dict) -> dict | None:
    body, _ = client.beads(city, label=f"{F.NEO_LABEL_PREFIX}{finding['finding_id']}")
    items = body.get("items") or []
    return items[0] if items else None


def submit(finding: dict, approval_path: Path, city: str,
           client: GasCityClient, receipts_dir: Path,
           trust_roots: list[Path] | None = None) -> dict[str, Any]:
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
        receipt["environment"] = approval["_environment"]
        check_authorization(finding, approval, city)
    except Refusal as r:
        receipt["refusal"] = {"reason": r.reason, "detail": r.detail}
        return _write(receipt, receipts_dir)

    receipt["idempotency_key"] = F.idempotency_key(finding, approval["_artifact_digest"])

    # ATOMIC single-use consumption (raises approval-reused when burned)
    try:
        mode, prior = consume_approval(receipts_dir, approval, finding,
                                       receipt["idempotency_key"])
    except Refusal as r:
        receipt["refusal"] = {"reason": r.reason, "detail": r.detail}
        return _write(receipt, receipts_dir)
    if mode == "replay":
        receipt.update(state="duplicate", stage=prior.get("stage"),
                       bead_id=prior.get("bead_id"),
                       duplicate_source="approval-ledger",
                       prior_receipt=prior.get("receipt_path"))
        return _write(receipt, receipts_dir)

    # dedup pre-check — an existing bead is returned, never resubmitted
    # (also the crash-recovery path when a reserve exists but no complete)
    try:
        pre = existing_bead(client, city, finding)
    except ApiError as e:
        if e.status != 404:
            return _api_error(receipt, receipts_dir, e)
        pre = None
    if pre:
        ok, why = verify_readback(pre, finding)
        if not ok:
            # a look-alike bead that fails identity verification must never
            # read as "duplicate/accepted" — fail closed
            receipt.update(state="unverified", bead_id=pre.get("id"),
                           duplicate_source="label-check",
                           verification_failure=why)
            return _write(receipt, receipts_dir)
        receipt.update(state="duplicate", bead_id=pre.get("id"),
                       duplicate_source="label-check",
                       stage=STAGE_ACCEPTED,
                       readback=_rb_view(pre))
        if mode == "resume":
            complete_approval(receipts_dir, approval, finding, receipt)
        return _write(receipt, receipts_dir)

    payload = F.to_bead_payload(finding)
    last_err: ApiError | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        receipt["attempts"] = attempt
        try:
            body, _index = client.create_bead(city, payload, receipt["idempotency_key"])
            bead_id = body.get("id")
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
            proof = find_create_proof(client, city, bead_id, finding)
            if proof:
                receipt.update(state="accepted", stage=STAGE_ACCEPTED,
                               bead_id=bead_id, event_proof=proof)
            else:
                receipt.update(state="accepted-persisted", stage=STAGE_PERSISTED_ONLY,
                               bead_id=bead_id, event_proof=None,
                               note="read-back verified; correlated creation event "
                                    "not found in scan window — persisted-only proof")
            complete_approval(receipts_dir, approval, finding, receipt)
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
