#!/usr/bin/env python3
"""Generate dw-controller gold from the LIVE factory_control reference.

A scenario battery drives `ElectronicsFactoryState` end to end — discovery
with stability windows and quarantine, duplicate detection, queue
registration and idempotency, the full lease lifecycle (claim / renew /
complete / fail / expire-recover), the visibility summaries, and the
immutability trigger on the event ledger — capturing every step's output
plus normalized table dumps.

Normalization (mirrored by the Rust test): observation ids become `src#N`
in first-seen order, event ids `evt#N` in ledger order, lease tokens
`<TOKEN>`, the device/inode/mtime identity columns zero (they are
machine-local), and the materialisation root becomes `$ROOT`. Everything
else — statuses, counters, timestamps, ordering, sha256, chunk ids — is
deterministic and compared exactly.

Usage: python3 scripts/gold_factory_battery.py > tests/fixtures/gold/factory_battery_v1.json
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from harness.electronics.factory_control import (  # noqa: E402
    ElectronicsFactoryState,
    FactoryStateError,
)

T0 = datetime(2026, 10, 14, 1, 0, 0, tzinfo=timezone.utc)


def iso(dt: datetime) -> str:
    return dt.isoformat()


GOOD_PDF = b"%PDF-1.4\n" + b"A" * 200 + b"\n%%EOF\n"
BAD_PDF = b"%PDF-1.4\n" + b"B" * 100  # no terminal %%EOF


def structural_queue(root: Path) -> bytes:
    from harness.electronics.claims import canonical_json
    import hashlib

    # Work-item paths are opaque to the factory (only queue_sha is stored),
    # so keep them root-free: the queue digest — and therefore every chunk
    # id — must be stable across generator and Rust replay runs.
    work = [{"path": "ev/good.pdf", "op": "extract"} for _ in range(5)]
    core = {
        "schema": "harness.electronics-structural-local-work.v1",
        "work": work,
    }
    value = {
        **core,
        "created_at": iso(T0),
        "evidence_sha256": hashlib.sha256(canonical_json(core)).hexdigest(),
    }
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode() + b"\n"


def chunk_manifest(queue_sha: str, offset: int, items: int) -> bytes:
    value = {
        "schema": "harness.electronics-structural-local-extraction.v1",
        "sources": {"structural_queue_sha256": queue_sha},
        "selection": {"offset": offset, "work_items": items},
        "rows": [],
    }
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode() + b"\n"


class Normalizer:
    def __init__(self, root: Path):
        self.resolved = str(root.resolve())
        self.root = str(root)
        self.source_map: dict[str, str] = {}
        self.event_map: dict[str, str] = {}

    def token(self, value):
        # secrets.token_urlsafe(32): 43 url-safe chars, padding stripped.
        if (
            isinstance(value, str)
            and len(value) == 43
            and all(c.isalnum() or c in "-_" for c in value)
        ):
            return "<TOKEN>"
        return self.text(value)

    def text(self, value):
        if isinstance(value, str):
            if value.startswith("source-") and len(value) == 39:
                return self.source_map.setdefault(value, f"src#{len(self.source_map) + 1}")
            if value.startswith("factory-event-"):
                return self.event_map.setdefault(value, f"evt#{len(self.event_map) + 1}")
            return value.replace(self.resolved, "$ROOT").replace(self.root, "$ROOT")
        return value

    def row(self, table: str, row: dict) -> dict:
        out = {}
        for key, value in row.items():
            if key in ("device", "inode", "mtime_ns"):
                out[key] = 0
            elif key == "lease_token" and value is not None:
                out[key] = "<TOKEN>"
            elif key == "payload_json" and value is not None:
                payload = json.loads(value)
                if "mtime_ns" in payload:
                    payload["mtime_ns"] = 0
                payload = {k: self.text(v) for k, v in payload.items()}
                out[key] = payload
                # Recompute over the NORMALIZED payload: identical on both
                # engines iff the normalized payloads are identical.
                import hashlib

                from harness.electronics.claims import canonical_json as cj

                out["payload_sha256"] = hashlib.sha256(cj(payload)).hexdigest()
            elif key == "payload_sha256" and "payload_sha256" in out:
                continue  # already recomputed over the normalized payload
            elif key == "lease_expires_at" and table == "active":
                out[key] = self.text(value)
            else:
                out[key] = self.text(value)
        return out


def dump(state, normalizer: Normalizer) -> dict:
    connection = sqlite3.connect(state.database)
    connection.row_factory = sqlite3.Row
    out = {}
    sources = [
        dict(row)
        for row in connection.execute(
            "SELECT * FROM factory_source_observations"
            " ORDER BY source_path, first_seen_at"
        )
    ]
    for row in sources:
        normalizer.text(row["observation_id"])  # assign ordinals in order
    out["observations"] = [normalizer.row("obs", row) for row in sources]
    chunks = [
        dict(row)
        for row in connection.execute(
            "SELECT * FROM factory_chunks ORDER BY created_at, offset, chunk_id"
        )
    ]
    out["chunks"] = [normalizer.row("chunk", row) for row in chunks]
    events = [
        dict(row)
        for row in connection.execute(
            "SELECT * FROM factory_events ORDER BY created_at, rowid"
        )
    ]
    for row in events:
        normalizer.text(row["event_id"])
    out["events"] = [normalizer.row("event", row) for row in events]
    counts = [
        dict(row)
        for row in connection.execute(
            "SELECT observation_id, cohort_id, snapshot_path, snapshot_sha256, created_at"
            " FROM factory_source_batches ORDER BY observation_id"
        )
    ]
    out["batches"] = [normalizer.row("batch", row) for row in counts]
    connection.close()
    return out


def lease_dict(lease, normalizer: Normalizer) -> dict:
    if lease is None:
        return None
    return {
        "chunk_id": normalizer.text(lease.chunk_id),
        "node": lease.node,
        "attempt": lease.attempt,
        "lease_token": normalizer.token(lease.lease_token),
        "lease_expires_at": lease.lease_expires_at,
        "queue_path": normalizer.text(str(lease.queue_path)),
        "queue_sha256": lease.queue_sha256,
        "offset": lease.offset,
        "item_count": lease.item_count,
        "output_directory": normalizer.text(str(lease.output_directory)),
    }


def main() -> int:
    import shutil
    import tempfile

    root = Path(tempfile.mkdtemp(prefix="gold_factory_"))
    steps: list[dict] = []
    files_manifest: list[dict] = []

    ev = root / "ev"
    ev.mkdir(parents=True)
    (ev / "good.pdf").write_bytes(GOOD_PDF)
    (ev / "bad.pdf").write_bytes(BAD_PDF)
    os.symlink(root / "ev" / "good.pdf", ev / "link.pdf")
    queue_bytes = structural_queue(root)
    (root / "queue.json").write_bytes(queue_bytes)
    for rel, payload in (
        ("ev/good.pdf", GOOD_PDF),
        ("ev/bad.pdf", BAD_PDF),
        ("queue.json", queue_bytes),
    ):
        files_manifest.append({"rel": rel, "bytes": list(payload)})
    files_manifest.append({"rel": "ev/link.pdf", "symlink_to": "ev/good.pdf"})
    # duplicate discovered later
    (ev / "dup.pdf").write_bytes(GOOD_PDF)
    files_manifest.append({"rel": "ev/dup.pdf", "bytes": list(GOOD_PDF)})

    state = ElectronicsFactoryState(root / "state")
    normalizer = Normalizer(root)

    def record(step: str, **fields) -> None:
        steps.append({"step": step, **fields})

    # ── discovery lifecycle ──────────────────────────────────────────
    report = state.discover_pdfs(
        [ev], stability_seconds=60, now=T0
    )
    record(step="discover_t0", report=vars(report))
    report = state.discover_pdfs(
        [ev], stability_seconds=60, now=T0 + timedelta(seconds=30)
    )
    record(step="discover_t30", report=vars(report))
    report = state.discover_pdfs(
        [ev], stability_seconds=60, now=T0 + timedelta(seconds=61)
    )
    record(step="discover_t61", report=vars(report))

    # ── queue registration ───────────────────────────────────────────
    queue_sha = __import__("hashlib").sha256(queue_bytes).hexdigest()
    chunk_ids = state.register_queue(
        root / "queue.json",
        root / "out",
        chunk_size=2,
        max_attempts=3,
        start_offset=0,
        now=T0,
    )
    record(step="register_queue", chunk_ids=chunk_ids)
    again = state.register_queue(
        root / "queue.json",
        root / "out",
        chunk_size=2,
        max_attempts=3,
        start_offset=0,
        now=T0,
    )
    record(step="register_queue_idempotent", chunk_ids=again)

    # ── lease lifecycle ──────────────────────────────────────────────
    t1 = T0 + timedelta(seconds=1000)
    lease1 = state.claim_chunk("asus2", lease_seconds=60, now=t1)
    lease2 = state.claim_chunk("asus2", lease_seconds=1800, now=t1)
    lease3 = state.claim_chunk("asus2", lease_seconds=60, now=t1)
    none_claim = state.claim_chunk("asus2", lease_seconds=60, now=t1)
    record(
        step="claims_t1",
        lease1=lease_dict(lease1, normalizer),
        lease2=lease_dict(lease2, normalizer),
        lease3=lease_dict(lease3, normalizer),
        fourth=none_claim,
    )

    renewed = state.renew_chunk(
        lease1, lease_seconds=60, now=t1 + timedelta(seconds=30)
    )
    record(step="renew_lease1", expires=renewed)

    failed_status = state.fail_chunk(
        lease2,
        "transient worker oom",
        terminal=False,
        retry_delay_seconds=30,
        now=t1 + timedelta(seconds=10),
    )
    record(step="fail_lease2_retry", status=failed_status)
    blocked = state.claim_chunk("asus3", lease_seconds=60, now=t1 + timedelta(seconds=20))
    record(step="claim_blocked", lease=lease_dict(blocked, normalizer))
    reclaimer = state.claim_chunk(
        "asus3", lease_seconds=60, now=t1 + timedelta(seconds=40)
    )
    record(step="claim_retry_available", lease=lease_dict(reclaimer, normalizer))
    terminal_status = state.fail_chunk(
        reclaimer,
        "terminal extraction failure",
        terminal=True,
        retry_delay_seconds=30,
        now=t1 + timedelta(seconds=50),
    )
    record(step="fail_terminal", status=terminal_status)

    # complete chunk 1 against a sealed output directory
    output_dir = Path(lease1.output_directory)
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_bytes = chunk_manifest(queue_sha, lease1.offset, lease1.item_count)
    (output_dir / "manifest.json").write_bytes(manifest_bytes)
    files_manifest.append(
        {
            "rel": f"out/{lease1.chunk_id}/manifest.json",
            "bytes": list(manifest_bytes),
        }
    )
    completed_sha = state.complete_chunk(lease1, now=t1 + timedelta(seconds=60))
    record(step="complete_lease1", manifest_sha=completed_sha)
    again_sha = state.complete_chunk(lease1, now=t1 + timedelta(seconds=61))
    record(step="complete_lease1_idempotent", manifest_sha=again_sha)

    # lease3 (60s from t1) expires; recover requeues it
    recovered = state.recover_expired(now=t1 + timedelta(seconds=200))
    record(step="recover_expired", recovered=recovered)
    reclaimed = state.claim_chunk(
        "asus4", lease_seconds=60, now=t1 + timedelta(seconds=201)
    )
    record(step="claim_after_recover", lease=lease_dict(reclaimed, normalizer))

    # stale lease on the completed chunk
    try:
        state.renew_chunk(lease1, lease_seconds=60, now=t1 + timedelta(seconds=300))
        raise AssertionError("expected stale lease")
    except FactoryStateError as exc:
        record(step="renew_completed_chunk", error=f"{type(exc).__name__}: {exc}")

    # ── validation errors ────────────────────────────────────────────
    for label, call in (
        ("blank_node", lambda: state.claim_chunk("  ", lease_seconds=60, now=t1)),
        ("short_lease", lambda: state.claim_chunk("n1", lease_seconds=10, now=t1)),
        ("bad_stability", lambda: state.discover_pdfs([ev], stability_seconds=-1, now=T0)),
        ("bad_chunk_size", lambda: state.register_queue(root / "queue.json", root / "o2", chunk_size=0, max_attempts=3, start_offset=0, now=T0)),
        ("untrimmed_error", lambda: state.fail_chunk(lease3, " padded ", terminal=False, retry_delay_seconds=30, now=t1)),
        ("missing_root", lambda: state.discover_pdfs([root / "nope"], stability_seconds=1, now=T0)),
    ):
        try:
            call()
            raise AssertionError(f"expected error in {label}")
        except (ValueError, FactoryStateError, FileNotFoundError) as exc:
            message = f"{type(exc).__name__}: {exc}"
            message = message.replace(str(root.resolve()), "$ROOT").replace(
                str(root), "$ROOT"
            )
            record(step=f"error_{label}", error=message)

    # ── event immutability ───────────────────────────────────────────
    connection = sqlite3.connect(state.database)
    try:
        connection.execute(
            "UPDATE factory_events SET event_type = 'tampered'"
        )
        connection.commit()
        raise AssertionError("expected immutability trigger")
    except sqlite3.DatabaseError as exc:
        record(step="events_immutable", contains=str(exc))
    finally:
        connection.close()

    # ── visibility ───────────────────────────────────────────────────
    status = state.status()
    status.pop("created_at", None)
    for lease_row in status["active_leases"]:
        normalizer.token(lease_row.get("lease_token") or "")
    record(step="status", status=status)
    record(step="queue_summary", summary=state.queue_chunk_summary(queue_sha))
    record(
        step="completed_paths",
        paths=[normalizer.text(str(p)) for p in state.completed_bundle_paths(queue_sha)],
    )
    record(step="tables", dump=state and dump(state, normalizer))

    shutil.rmtree(root, ignore_errors=True)
    print(
        json.dumps(
            {"files": files_manifest, "steps": steps}, indent=1, sort_keys=True
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
