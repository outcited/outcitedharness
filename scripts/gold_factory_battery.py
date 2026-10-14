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
        # Set after the registry fixture is written; the registry digest
        # covers the root-containing pdf_root string, so it (and the
        # baseline cohort carved from it) differ per materialisation.
        self.registry_sha: str | None = None

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

    def mask_registry(self, value: str) -> str:
        if self.registry_sha:
            value = value.replace(self.registry_sha, "<REGISTRY_SHA>").replace(
                self.registry_sha[:24], "<REGISTRY24>"
            )
        return value

    def row(self, table: str, row: dict) -> dict:
        out = {}
        for key, value in row.items():
            if key in ("device", "inode", "mtime_ns"):
                out[key] = 0
            elif key == "snapshot_sha256":
                # Snapshot bytes embed real observation ids; the registry
                # digest survives via the seeded reason strings instead.
                out[key] = "<SNAPSHOT_SHA>"
            elif key == "cohort_id" and isinstance(value, str):
                out[key] = self.mask_registry(value)
            elif key == "reason" and isinstance(value, str):
                out[key] = self.mask_registry(self.text(value))
            elif key == "lease_token" and value is not None:
                out[key] = "<TOKEN>"
            elif key == "payload_json" and value is not None:
                payload = json.loads(value)
                if "mtime_ns" in payload:
                    payload["mtime_ns"] = 0
                if "snapshot_sha256" in payload:
                    # Snapshot bytes embed real observation ids.
                    payload["snapshot_sha256"] = "<SNAPSHOT_SHA>"
                payload = {
                    k: self.mask_registry(self.text(v) if isinstance(v, str) else v)
                    if isinstance(v, str)
                    else v
                    for k, v in payload.items()
                }
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
            " FROM factory_source_batches ORDER BY cohort_id, created_at, snapshot_path"
        )
    ]
    # Rows can tie on every ORDER BY key (two seeded docs share cohort,
    # timestamp, and snapshot); sort by the NORMALIZED id, which is stable.
    batches = [normalizer.row("batch", row) for row in counts]
    batches.sort(
        key=lambda row: (
            str(row.get("cohort_id")),
            str(row.get("observation_id")),
            str(row.get("snapshot_path")),
        )
    )
    out["batches"] = batches
    connection.close()
    return out


def snapshot_manifest(manifest: dict, normalizer: Normalizer) -> dict:
    """Normalize a sealed snapshot manifest: wall-clock created_at, id
    ordinals, zeroed mtimes, $ROOT paths, and the evidence digest
    recomputed over the normalized core."""
    out = dict(manifest)
    out["created_at"] = "<CREATED_AT>"
    documents = []
    for document in out.get("documents", []):
        document = dict(document)
        document["observation_id"] = normalizer.text(document["observation_id"])
        document["source_path"] = normalizer.text(document["source_path"])
        document["mtime_ns"] = 0
        documents.append(document)
    out["documents"] = documents
    core = {k: v for k, v in out.items() if k not in ("created_at", "evidence_sha256")}
    import hashlib

    from harness.electronics.claims import canonical_json as cj

    out["evidence_sha256"] = hashlib.sha256(cj(core)).hexdigest()
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

    # ── phase-2 fixtures: corpus registry tree + frontier bundle ──────
    import hashlib as _hl

    C_PDF = b"%PDF-1.7\n" + b"C" * 150 + b"\n%%EOF\n"
    seed_root = root / "sealed"
    seed_root.mkdir()
    (seed_root / "a.pdf").write_bytes(GOOD_PDF)
    (seed_root / "b.pdf").write_bytes(GOOD_PDF)
    (seed_root / "c.pdf").write_bytes(C_PDF)
    for rel, payload in (
        ("sealed/a.pdf", GOOD_PDF),
        ("sealed/b.pdf", GOOD_PDF),
        ("sealed/c.pdf", C_PDF),
    ):
        files_manifest.append({"rel": rel, "bytes": list(payload)})
    registry_core = {
        "schema": "harness.electronics-corpus-registry.v1",
        "sources": {"pdf_root": str(seed_root)},
        "counts": {"unique_pdf_sha256": 4},
        "documents": [
            {
                "document_sha256": _hl.sha256(GOOD_PDF).hexdigest(),
                "byte_size": len(GOOD_PDF),
                "paths": ["a.pdf"],
            },
            {
                "document_sha256": _hl.sha256(C_PDF).hexdigest(),
                "byte_size": len(C_PDF),
                "paths": ["c.pdf"],
            },
            {
                "document_sha256": _hl.sha256(b"declared-other").hexdigest(),
                "byte_size": 999,
                "paths": ["b.pdf"],
            },
            {
                "document_sha256": _hl.sha256(b"gone-doc").hexdigest(),
                "byte_size": 100,
                "paths": ["gone.pdf"],
            },
        ],
    }
    from harness.electronics.claims import canonical_json as _cj

    registry = {
        **registry_core,
        "created_at": iso(T0),
        "evidence_sha256": _hl.sha256(_cj(registry_core)).hexdigest(),
    }
    registry_bytes = (
        json.dumps(registry, ensure_ascii=False, sort_keys=True, indent=2).encode()
        + b"\n"
    )
    (root / "registry.json").write_bytes(registry_bytes)
    # The registry evidence digest covers sources.pdf_root, so the fixture
    # stores the CORE with `$ROOT/sealed` substituted and both engines
    # rebuild the file for their own root (digests then differ and are
    # masked in dumps as <REGISTRY_SHA>/<REGISTRY24>).
    fixture_core = json.loads(
        json.dumps(registry_core).replace(
            str(seed_root.resolve()), "$ROOT/sealed"
        ).replace(str(seed_root), "$ROOT/sealed")
    )
    files_manifest.append({"rel": "registry.json", "registry_core": fixture_core})

    frontier_manifest = (
        json.dumps(
            {
                "schema": "harness.electronics-frontier-batch.v1",
                "counts": {"requests": 2},
                "pricing": {"estimated_maximum_usd": 0.5},
                "evidence_sha256": "ab" * 32,
            },
            sort_keys=True,
            indent=2,
        ).encode()
        + b"\n"
    )
    (root / "frontier_bundle").mkdir()
    (root / "frontier_bundle" / "manifest.json").write_bytes(frontier_manifest)
    files_manifest.append(
        {"rel": "frontier_bundle/manifest.json", "bytes": list(frontier_manifest)}
    )
    (root / "frontier_state").mkdir()
    (root / "frontier_state" / "submission-intent.json").write_bytes(b"{}\n")
    files_manifest.append(
        {"rel": "frontier_state/submission-intent.json", "bytes": [123, 125, 10]}
    )
    (root / "frontier_lifecycle").mkdir()
    (root / "frontier_lifecycle2").mkdir()
    files_manifest.append({"rel": "frontier_lifecycle/", "mkdir": True})
    files_manifest.append({"rel": "frontier_lifecycle2/", "mkdir": True})

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
    # ── phase 2: snapshot sealing ─────────────────────────────────────
    ready_manifest = state.seal_ready_source_snapshot(root / "ready.json")
    record(step="seal_ready", manifest=snapshot_manifest(ready_manifest, normalizer))
    try:
        state.seal_ready_source_snapshot(root / "ready.json")
        raise AssertionError("expected exists error")
    except FactoryStateError as exc:
        message = f"{type(exc).__name__}: {exc}".replace(str(root.resolve()), "$ROOT").replace(
            str(root), "$ROOT"
        )
        record(step="error_seal_exists", error=message)

    unassigned = state.seal_unassigned_source_snapshot(
        root / "cohort-a.json",
        cohort_id="cohort-alpha",
        maximum_documents=5000,
        now=T0 + timedelta(seconds=500),
    )
    record(
        step="seal_unassigned",
        manifest=snapshot_manifest(unassigned, normalizer) if unassigned else None,
    )
    empty_seal = state.seal_unassigned_source_snapshot(
        root / "cohort-b.json",
        cohort_id="cohort-beta",
        maximum_documents=5000,
        now=T0 + timedelta(seconds=510),
    )
    record(step="seal_unassigned_empty", manifest=empty_seal)
    for label, call in (
        ("error_bad_cohort", lambda: state.seal_unassigned_source_snapshot(
            root / "x.json", cohort_id=" padded ", maximum_documents=5, now=T0)),
        ("error_bad_maximum", lambda: state.seal_unassigned_source_snapshot(
            root / "x.json", cohort_id="ok", maximum_documents=0, now=T0)),
    ):
        try:
            call()
            raise AssertionError(f"expected error in {label}")
        except ValueError as exc:
            record(step=label, error=f"{type(exc).__name__}: {exc}")

    # ── phase 2: corpus seeding ───────────────────────────────────────
    normalizer.registry_sha = _hl.sha256((root / "registry.json").read_bytes()).hexdigest()
    seeded = state.seed_corpus_registry(
        root / "registry.json", now=T0 + timedelta(seconds=600)
    )
    record(step="seed_registry", counts=seeded)
    reseeded = state.seed_corpus_registry(
        root / "registry.json", now=T0 + timedelta(seconds=610)
    )
    record(step="seed_registry_idempotent", counts=reseeded)

    # ── phase 2: frontier runs ────────────────────────────────────────
    registered = state.register_frontier_run(
        run_id="run-1",
        prepared_bundle=root / "frontier_bundle",
        submission_state=root / "frontier_state",
        lifecycle_root=root / "frontier_lifecycle",
        now=T0 + timedelta(seconds=700),
    )
    record(step="frontier_register", registered=registered)
    re_registered = state.register_frontier_run(
        run_id="run-1",
        prepared_bundle=root / "frontier_bundle",
        submission_state=root / "frontier_state",
        lifecycle_root=root / "frontier_lifecycle",
        now=T0 + timedelta(seconds=701),
    )
    record(step="frontier_register_idempotent", registered=re_registered)
    try:
        state.register_frontier_run(
            run_id="run-1",
            prepared_bundle=root / "frontier_bundle",
            submission_state=root / "frontier_state",
            lifecycle_root=root / "frontier_lifecycle2",
            now=T0 + timedelta(seconds=702),
        )
        raise AssertionError("expected identity conflict")
    except FactoryStateError as exc:
        message = f"{type(exc).__name__}: {exc}".replace(str(root.resolve()), "$ROOT").replace(
            str(root), "$ROOT"
        )
        record(step="frontier_register_conflict", error=message)

    state.record_frontier_status(
        "run-1",
        {"batches": [{"id": "b1", "processing_status": "in_progress"}]},
        now=T0 + timedelta(seconds=710),
    )
    record(step="frontier_status_processing", ok=True)
    state.record_frontier_status(
        "run-1",
        {
            "batches": [
                {"id": "b1", "processing_status": "ended"},
                {"id": "b2", "processing_status": "ended"},
            ]
        },
        now=T0 + timedelta(seconds=720),
    )
    record(step="frontier_status_ended_inferred", ok=True)
    for label, call in (
        ("error_frontier_stage", lambda: state.record_frontier_status(
            "run-1", {}, stage="bogus", now=T0)),
        ("error_frontier_unknown_run", lambda: state.record_frontier_status(
            "run-x", {}, now=T0)),
    ):
        try:
            call()
            raise AssertionError(f"expected error in {label}")
        except (ValueError, FactoryStateError) as exc:
            record(step=label, error=f"{type(exc).__name__}: {exc}")

    status2 = state.status()
    status2.pop("created_at", None)
    for lease_row in status2["active_leases"]:
        normalizer.token(lease_row.get("lease_token") or "")
    record(step="status_with_frontier", status=status2)

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
