#!/usr/bin/env python3
"""Generate dw-recon::incremental_corpus gold from the LIVE
`harness/electronics/incremental_corpus.py`.

Every case is computed by the reference itself: `build` cases materialise a
fixture tree and run `build_incremental_corpus_registry` end to end, kernel
cases call the pure readers. The snapshot JSON embeds absolute paths, so tree
entries may carry `subst: true` — `$ROOT` in their bytes is replaced with the
materialised root when written (the Rust test does the same), while absolute
paths in outputs and error messages are normalised back to `$ROOT` and the
`evidence_sha256` is re-derived over the normalised core on both sides.

Cases cover the traps the reader exists for: the snapshot's own evidence
digest and document count are enforced, sources must still hash and size as
intaked (else "changed after intake"), `source_path` must stay under the PDF
root and be a non-symlink file, directory vendor prefixes invent alias stems
(`ti_ti_tms320`) that join ground truth like exact stems, ambiguous stems
(>1 digest) join nothing, `_`-prefixed validated subdirectories are skipped,
and bindings contribute `record_ids` and `manifested_opn` memberships.

Usage: python3 scripts/gold_incremental_corpus.py > tests/fixtures/gold/incremental_corpus_v1.json
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness.electronics.claims import canonical_json  # noqa: E402
from harness.electronics.incremental_corpus import (  # noqa: E402
    _part_key,
    _summary,
    _verify_snapshot,
    build_incremental_corpus_registry,
)


def _normalise(value: object, root: str) -> object:
    if isinstance(value, str):
        return value.replace(root, "$ROOT")
    if isinstance(value, list):
        return [_normalise(item, root) for item in value]
    if isinstance(value, dict):
        return {key: _normalise(item, root) for key, item in value.items()}
    return value


def _reissue_digest(registry: dict) -> dict:
    """Strip `created_at`, re-derive `evidence_sha256` over the normalised
    core — the digest computation is differential (Python hashlib here, the
    Rust in-crate SHA-256 there)."""
    registry.pop("created_at", None)
    core = {key: item for key, item in registry.items() if key != "evidence_sha256"}
    registry["evidence_sha256"] = hashlib.sha256(canonical_json(core)).hexdigest()
    return registry


def _tree_spec(tree: dict[str, bytes]) -> list[dict]:
    return [
        {
            "path": rel,
            "bytes": list(data),
            "subst": b"$ROOT" in data or b"$DIGEST" in data,
        }
        for rel, data in sorted(tree.items())
    ]


FIXED_PREFIX = "/tmp/gold_inc_corpus"


def build_case(name: str, tree: dict[str, bytes], config: dict) -> dict:
    # A FIXED root: the snapshot's file hash and embedded evidence digest are
    # computed over root-containing bytes, so the Rust test must materialise
    # at the same path to produce identical digests.
    root = Path(f"{FIXED_PREFIX}_{name}")
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    root = root.resolve()
    root_bytes = str(root).encode("utf-8")

    def materialise(data: bytes, rel: str) -> bytes:
        data = data.replace(b"$ROOT", root_bytes)
        if b"$DIGEST" in data:
            data = materialise_snapshot(
                data.replace(b"$ROOT", b"$ROOT"), str(root)
            )
        return data

    try:
        for key in ("pdfs", "gt", "validated"):
            (root / config.get(key, key)).mkdir(parents=True, exist_ok=True)
        for rel, data in tree.items():
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            payload = data.replace(b"$ROOT", root_bytes)
            if b"$DIGEST" in payload:
                payload = materialise_snapshot(
                    payload.replace(root_bytes, b"$ROOT"), str(root)
                )
            target.write_bytes(payload)
        captured: dict
        try:
            with contextlib.redirect_stdout(sys.stderr):
                registry = build_incremental_corpus_registry(
                    root / config.get("snapshot", "snapshot.json"),
                    pdf_root=root / config.get("pdf_root", "pdfs"),
                    ground_truth_root=root / config.get("ground_truth_root", "gt"),
                    validated_root=root / config.get("validated_root", "validated"),
                )
            captured = {
                "expected": _reissue_digest(_normalise(registry, str(root))),
                "error": None,
            }
        except ValueError as exc:
            captured = {
                "expected": None,
                "error": {
                    "kind": "value",
                    "match": "exact",
                    "message": _normalise(str(exc), str(root)),
                },
            }
        except OSError as exc:
            # Missing paths are OS errors; the message is not wire, only that
            # the build rejects.
            captured = {"expected": None, "error": {"kind": "os", "message": ""}}
        if config.get("error"):
            assert captured["error"], f"{name}: reference did not raise"
        else:
            assert captured["expected"] is not None, (
                f"{name}: reference raised {captured['error']}"
            )
        return {
            "kind": "build",
            "name": name,
            "input": {
                "tree": _tree_spec(tree),
                "config": {
                    key: value
                    for key, value in config.items()
                    if key != "error"
                },
            },
            "expected": captured["expected"],
            "error": captured["error"],
        }
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _snapshot_bytes(documents: list[dict], counts: dict | None = None) -> bytes:
    """Snapshot JSON with `$ROOT`-templated source paths and a `$DIGEST`
    placeholder: the evidence digest is computed over the MATERIALISED core,
    so both sides substitute `$ROOT`, digest the core, then substitute
    `$DIGEST`."""
    core = {
        "schema": "harness.electronics-incremental-source-snapshot.v1",
        "purpose": "stable_deduplicated_datasheet_intake",
        "cohort_id": "wave-1",
        "counts": counts if counts is not None else {"documents": len(documents)},
        "documents": documents,
    }
    value = {
        "created_at": "2026-09-02T00:00:00+00:00",
        **core,
        "evidence_sha256": "$DIGEST",
    }
    return json.dumps(value).encode("utf-8")


def materialise_snapshot(data: bytes, root: str) -> bytes:
    """`$ROOT` in paths, then `$DIGEST` = sha256 of the materialised core."""
    text = data.replace(b"$ROOT", root.encode("utf-8")).decode("utf-8")
    value = json.loads(text)
    core = {key: item for key, item in value.items() if key != "created_at"}
    core.pop("evidence_sha256", None)
    digest = hashlib.sha256(canonical_json(core)).hexdigest()
    return text.replace("$DIGEST", digest).encode("utf-8")


def _doc_row(rel: str, byte_size: int, digest: str, extra: dict | None = None) -> dict:
    row = {
        "observation_id": f"obs-{rel}",
        "source_path": f"$ROOT/{rel}" if not rel.startswith("$") else rel,
        "byte_size": byte_size,
        "mtime_ns": 1_700_000_000_000_000_000,
        "sha256": digest,
    }
    if extra:
        row.update(extra)
    return row


def _pdf_bytes(tag: bytes = b"part") -> bytes:
    return b"%PDF-1.7\n" + tag + b"\n" + b"x" * 128 + b"\n%%EOF\n"


def kernel_cases() -> list[dict]:
    cases: list[dict] = []

    def add(kind: str, name: str, inputs: dict, expected) -> None:
        cases.append({"kind": kind, "name": name, "input": inputs, "expected": expected})

    # `_part_key` here differs from corpus's: only None is falsy.
    for name, value in [
        ("upper", "atom1"),
        ("punct", "AT-MEGA1608"),
        ("none", None),
        ("zero_kept", 0),
        ("false_kept", False),
        ("empty", ""),
        ("float", 1.0),
    ]:
        add("part_key", name, {"value": value}, _part_key(value))

    # `_summary`: no stem vendor fallback (unlike corpus's `_record_summary`).
    for name, rel_path, value in [
        (
            "full",
            "ti/atom1.json",
            {
                "overview": {"manufacturer": "Texas Instruments", "part_number": "TMS320"},
                "pinout": {"pin_functions_summary": []},
                "clock_specs": [],
                "_meta": {
                    "batch_id": "b9",
                    "provenance": {"validation": "VALIDATED_GROUND_TRUTH"},
                },
            },
        ),
        (
            "no_stem_fallback",
            "st_mcu.json",
            {"part_number": "STM32"},
        ),
    ]:
        summary, digest, payload = _summary_expected(rel_path, value)
        add(
            "summary",
            name,
            {
                "rel_path": rel_path,
                "digest": digest,
                "value": value,
                "payload": list(payload),
            },
            summary,
        )
    return cases


def _summary_expected(rel_path: str, value: dict) -> tuple[dict, str, bytes]:
    """Run the reference's `_summary` on a materialised file; return the
    summary, the file digest and the exact payload bytes (the Rust test
    writes them verbatim — serde would re-order keys and change the hash)."""
    root = Path(f"{FIXED_PREFIX}_sum")
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    root = root.resolve()
    try:
        target = root / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(value).encode("utf-8")
        target.write_bytes(payload)
        return (
            _summary(target, root),
            hashlib.sha256(payload).hexdigest(),
            payload,
        )
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main() -> int:
    out: dict = {"cases": []}
    pdf = _pdf_bytes()
    pdf_digest = hashlib.sha256(pdf).hexdigest()

    # The pytest shape: source verified against the snapshot, exact-stem gt.
    out["cases"].append(
        build_case(
            "verifies_sources_and_exact_stem_ground_truth",
            {
                "pdfs/ti_tms320.pdf": pdf,
                "gt/ti_tms320.json": json.dumps(
                    {
                        "overview": {
                            "part_number": "TMS320",
                            "manufacturer": "Texas Instruments",
                        },
                        "pinout": {"pin_functions_summary": []},
                    }
                ).encode("utf-8"),
                "snapshot.json": _snapshot_bytes(
                    [_doc_row("pdfs/ti_tms320.pdf", len(pdf), pdf_digest)]
                ),
            },
            {"snapshot": "snapshot.json"},
        )
    )

    # Source changed after intake: same path, different bytes.
    out["cases"].append(
        build_case(
            "rejects_source_changed_after_snapshot",
            {
                "pdfs/part.pdf": _pdf_bytes(b"changed"),
                "snapshot.json": _snapshot_bytes(
                    [_doc_row("pdfs/part.pdf", len(pdf), pdf_digest)],
                ),
            },
            {"snapshot": "snapshot.json", "error": True},
        )
    )

    # Directory vendor prefix invents an alias stem; bindings contribute
    # record_ids and manifested_opn; validated join via the alias.
    deep = _pdf_bytes(b"deep")
    deep_digest = hashlib.sha256(deep).hexdigest()
    out["cases"].append(
        build_case(
            "directory_vendor_alias_and_bindings",
            {
                "pdfs/ti.com/ti_mcu.pdf": deep,
                "gt/ti_mcu.json": json.dumps(
                    {"overview": {"manufacturer": "Silicon Labs", "part_number": "EFR32"}}
                ).encode("utf-8"),
                "validated/nested/ti_mcu.json": json.dumps(
                    {
                        "part_number": "EFR32",
                        "_meta": {"validation": "VALIDATED_GROUND_TRUTH"},
                    }
                ).encode("utf-8"),
                "snapshot.json": _snapshot_bytes(
                    [
                        _doc_row(
                            "pdfs/ti.com/ti_mcu.pdf",
                            len(deep),
                            deep_digest,
                            {
                                "bindings": [
                                    {"part_id": "OPN-1", "opn": "OPN-1", "vendor": "Silicon Labs"},
                                    {"opn": "OPN-2"},
                                    {"part_id": "", "vendor": None},
                                ]
                            },
                        )
                    ],
                ),
            },
            {"snapshot": "snapshot.json"},
        )
    )

    # Ambiguous stems join nothing; underscore validated skipped; escaped
    # source rejected.
    a = _pdf_bytes(b"aaa")
    b = _pdf_bytes(b"bbb")
    out["cases"].append(
        build_case(
            "ambiguous_stems_join_nothing",
            {
                "pdfs/x_shared.pdf": a,
                "pdfs/y_shared.pdf": b,
                "gt/x_shared.json": json.dumps({"part_number": "SHARED"}).encode("utf-8"),
                "validated/_skip/x_shared.json": json.dumps({"part_number": "SHARED"}).encode("utf-8"),
                "snapshot.json": _snapshot_bytes(
                    [
                        _doc_row("pdfs/x_shared.pdf", len(a), hashlib.sha256(a).hexdigest()),
                        _doc_row("pdfs/y_shared.pdf", len(b), hashlib.sha256(b).hexdigest()),
                    ],
                ),
            },
            {"snapshot": "snapshot.json"},
        )
    )

    out["cases"].append(
        build_case(
            "source_escapes_pdf_root",
            {
                "outside/esc.pdf": pdf,
                "snapshot.json": _snapshot_bytes(
                    [_doc_row("outside/esc.pdf", len(pdf), pdf_digest)]
                ),
            },
            {"snapshot": "snapshot.json", "error": True},
        )
    )

    # Snapshot self-checks: bad digest, bad count, bad schema, missing file.
    good = _snapshot_bytes([_doc_row("pdfs/ti_tms320.pdf", len(pdf), pdf_digest)])
    for name, mutate in [
        (
            "snapshot_digest_invalid",
            # A wrong digest is left in place after materialisation.
            lambda text: text.replace(b"$DIGEST", b"0" * 64),
        ),
        (
            "snapshot_count_invalid",
            # Count drifts from the document list; the digest is recomputed so
            # only the count check can fail.
            lambda text: text.replace(b'"documents": 1', b'"documents": 2'),
        ),
        (
            "snapshot_schema_invalid",
            lambda text: text.replace(
                b"harness.electronics-incremental-source-snapshot.v1",
                b"nope.v1",
            ),
        ),
    ]:
        out["cases"].append(
            build_case(
                name,
                {
                    "pdfs/ti_tms320.pdf": pdf,
                    "snapshot.json": mutate(good),
                },
                {"snapshot": "snapshot.json", "error": True},
            )
        )
    out["cases"].append(
        build_case(
            "snapshot_missing",
            {
                "pdfs/ti_tms320.pdf": pdf,
            },
            {"snapshot": "snapshot.json", "error": True},
        )
    )

    # Root validations.
    out["cases"].append(
        build_case(
            "pdf_root_missing",
            {
                "snapshot.json": _snapshot_bytes(
                    [_doc_row("pdfs/ti_tms320.pdf", len(pdf), pdf_digest)]
                ),
            },
            {"snapshot": "snapshot.json", "pdf_root": "snapshot.json", "error": True},
        )
    )
    out["cases"].append(
        build_case(
            "source_unsafe",
            {
                "pdfs/dir_here/keep.txt": b"x",
                "snapshot.json": _snapshot_bytes(
                    [_doc_row("pdfs/dir_here", len(pdf), pdf_digest)]
                ),
            },
            {"snapshot": "snapshot.json", "error": True},
        )
    )

    out["cases"].extend(kernel_cases())
    json.dump(out, sys.stdout, indent=1, sort_keys=True)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
