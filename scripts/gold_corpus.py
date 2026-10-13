#!/usr/bin/env python3
"""Generate dw-recon::corpus gold from the LIVE `harness/electronics/corpus.py`.

Every case is computed by the reference itself: `build` cases materialise a
fixture tree and run `build_corpus_registry` end to end, kernel cases call the
pure readers. Absolute paths are volatile, so both the generator and the Rust
test normalise the tree root to `$ROOT` and re-derive `evidence_sha256` over
the normalised core (which doubles as a live differential check of
`canonical_json` + SHA-256 against Python's `hashlib`); `created_at` is
stripped. `build` cases additionally require the raw registry to pass
`verify_corpus_registry` on the Rust side — the code's own digest emission.

Cases cover the traps the registry exists for: duplicate PDF bytes collapsing
into ONE document with both paths, direct joins on `pdf_sha256`, unambiguous
part joins through canonical vendors ("acme.com" -> "acme"), ambiguous and
unresolved rows counting apart, orphan ground truth by reason, the source
audit's multi-row-per-PDF lists and hash-mismatch rejection, `_`-prefixed
validated subdirectories being skipped, `records` lists vs whole-object JSON
assets, and the read-only exclusive write.

Usage: python3 scripts/gold_corpus.py > tests/fixtures/gold/corpus_v1.json
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness.electronics.claims import canonical_json  # noqa: E402
from harness.electronics.corpus import (  # noqa: E402
    AssetSource,
    CorpusInputs,
    _document_digest,
    _part_key,
    _record_ids,
    _record_summary,
    build_corpus_registry,
    canonical_vendor,
    verify_corpus_registry,
    write_new_registry,
)

VOLATILE = {"created_at", "evidence_sha256"}


def _py_repr(value: object) -> str:
    """Python `repr` for the shapes the reference interpolates."""
    if isinstance(value, str):
        if "'" in value or "\\" in value:
            return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
        return f"'{value}'"
    return repr(value)


def _normalise(value: object, root: str) -> object:
    if isinstance(value, str):
        return value.replace(root, "$ROOT")
    if isinstance(value, list):
        return [_normalise(item, root) for item in value]
    if isinstance(value, dict):
        return {key: _normalise(item, root) for key, item in value.items()}
    return value


def _reissue_digest(registry: dict) -> dict:
    """Strip `created_at`, then re-derive `evidence_sha256` over what is left.

    The reference digests the core that carries absolute paths; after `$ROOT`
    normalisation the digest must be recomputed or it would not match the
    value the Rust test derives the same way. The digest computation is
    itself differential: Python's hashlib here, Rust's in-crate SHA-256 there.
    """
    registry.pop("created_at", None)
    core = {key: item for key, item in registry.items() if key != "evidence_sha256"}
    registry["evidence_sha256"] = hashlib.sha256(canonical_json(core)).hexdigest()
    return registry


def _tree_spec(root: Path, tree: dict[str, bytes]) -> list[dict]:
    # Byte arrays: the Rust test writes them verbatim with no decoder.
    return [{"path": rel, "bytes": list(data)} for rel, data in sorted(tree.items())]


def build_case(name: str, tree: dict[str, bytes], config: dict, expected) -> dict:
    """Materialise `tree`, run the reference, capture normalised output."""
    root = Path(tempfile.mkdtemp(prefix="gold_corpus_")).resolve()
    try:
        for key in ("pdf_root", "ground_truth_root", "validated_root"):
            (root / config.get(key, key.replace("ground_truth_root", "gt").replace("pdf_root", "pdfs").replace("validated_root", "validated"))).mkdir(parents=True, exist_ok=True)
        for rel, data in tree.items():
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        captured: dict = {}
        try:
            inputs = CorpusInputs(
                pdf_root=root / config.get("pdf_root", "pdfs"),
                ground_truth_root=root / config.get("ground_truth_root", "gt"),
                validated_root=root / config.get("validated_root", "validated"),
                source_audit=(
                    root / config["source_audit"] if config.get("source_audit") else None
                ),
                row_dataset_manifest=(
                    root / config["row_dataset_manifest"]
                    if config.get("row_dataset_manifest")
                    else None
                ),
                assets=tuple(
                    AssetSource(
                        asset["name"], root / asset["path"], asset.get("kind", "auto")
                    )
                    for asset in config.get("assets", [])
                ),
                expected_pdf_files=config.get("expected_pdf_files"),
            )
            with contextlib.redirect_stdout(sys.stderr):
                registry = build_corpus_registry(inputs, hash_workers=2)
            verify_corpus_registry(registry)
            captured = {
                "expected": _reissue_digest(_normalise(registry, str(root))),
                "error": None,
            }
        except ValueError as exc:
            captured = {
                "expected": None,
                "error": {
                    "match": "contains"
                    if "differs from the live corpus" in str(exc)
                    else "exact",
                    "message": _normalise(str(exc), str(root)),
                },
            }
        if expected.get("error"):
            assert captured["error"], f"{name}: reference did not raise"
        else:
            assert captured["expected"] is not None, (
                f"{name}: reference raised {captured.get('error')}"
            )
        return {
            "kind": "build",
            "name": name,
            "input": {
                "tree": _tree_spec(root, tree),
                "config": {
                    **{
                        key: value
                        for key, value in config.items()
                        if key != "assets"
                    },
                    "assets": config.get("assets", []),
                },
            },
            "expected": captured["expected"],
            "error": captured["error"] if captured["error"] else expected.get("error"),
        }
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _verify_expected(value: dict) -> dict:
    try:
        verify_corpus_registry(value)
    except ValueError as exc:
        return {"ok": False, "message": str(exc)}
    return {"ok": True, "message": None}


def kernel_cases() -> list[dict]:
    cases: list[dict] = []

    def add(kind: str, name: str, inputs: dict, expected) -> None:
        cases.append({"kind": kind, "name": name, "input": inputs, "expected": expected})

    for name, value in [
        ("plain", "acme"),
        ("casefold", "STMicroelectronics"),
        ("dotcom", "acme.com"),
        ("alias_silabs", "Silicon Labs"),
        ("alias_freescale", "Freescale/NXP"),
        ("alias_ti", "Texas Instruments"),
        ("unknown_kept", "Contoso"),
        ("empty", ""),
        ("none", None),
        ("numeric", 123),
        ("float", 1.0),
        ("bool_true", True),
        ("whitespace", "  acme  "),
    ]:
        add(
            "canonical_vendor",
            name,
            {"value": value},
            canonical_vendor(value),
        )

    for name, value in [
        ("upper", "atom1"),
        ("punct", "AT-MEGA1608"),
        ("spaces", "  x y  "),
        ("empty", ""),
        ("none", None),
        ("zero_is_falsy", 0),
        ("int", 12),
        ("float", 1.0),
        ("bool_true", True),
    ]:
        add("part_key", name, {"value": value}, _part_key(value))

    for name, row, fallback in [
        (
            "priority",
            {"record_id": "r1", "queue_id": 2, "part_number": "P1", "base_mpn": "B"},
            "file:1",
        ),
        ("empty_strings", {"record_id": "  ", "series_key": ""}, "file:2"),
        ("float", {"custom_id": 1.0}, "f:3"),
        ("bool", {"record_id": True}, "f:4"),
        ("int_zero", {"record_id": 0}, "f:5"),
        ("dict_ignored", {"record_id": {"a": 1}}, "f:6"),
    ]:
        add(
            "record_ids",
            name,
            {"row": row, "fallback": fallback},
            _record_ids(row, fallback),
        )

    for name, row in [
        ("first_key", {"pdf_sha256": "a" * 64, "source_document_sha256": "b" * 64}),
        ("second_key", {"source_document_sha256": "b" * 64}),
        ("uppercase", {"pdf_sha256": "A" * 64}),
        ("short", {"pdf_sha256": "abc"}),
        ("non_hex", {"pdf_sha256": "z" * 64}),
        ("missing", {}),
    ]:
        add("document_digest", name, {"row": row}, _document_digest(row))

    summary_inputs = {
        "rel_path": "acme/atom1.json",
        "stem": "atom1",
        "digest": "d" * 64,
        "value": {
            "overview": {"manufacturer": "Silicon Labs", "part_number": "EFR32"},
            "pinout": [],
            "clock_specs": [],
            "_meta": {
                "batch_id": "b1",
                "provenance": {"validation": "VALIDATED_GROUND_TRUTH"},
            },
        },
    }
    def _summary_expected(inputs: dict) -> dict:
        value = _record_summary(
            path=Path(inputs["rel_path"]),
            digest=inputs["digest"],
            value=inputs["value"],
            relative_to=Path("."),
        )
        return value

    add("record_summary", "full", summary_inputs, _summary_expected(summary_inputs))
    fallback_inputs = {
        **summary_inputs,
        "rel_path": "st_mcu.json",
        "stem": "st_mcu",
        "value": {"part_number": "STM32"},
    }
    add(
        "record_summary",
        "stem_vendor_fallback",
        fallback_inputs,
        _summary_expected(fallback_inputs),
    )

    for name, value in [
        ("unicode", {"b": "ü", "a": [1, 2.5], "c": {"z": True, "y": None}}),
        ("nested_order", {"m": {"2": 1, "10": 3}, "a": "x"}),
        ("floats", {"f": 0.1, "g": 1.0, "h": -2.5}),
    ]:
        add(
            "canonical_json_sha256",
            name,
            {"value": value},
            hashlib.sha256(canonical_json(value)).hexdigest(),
        )

    # The digest case mirrors the pytest: digest over the pristine value,
    # then tamper. Expected outcomes are captured from the reference.
    base = {
        "created_at": "2026-09-01T00:00:00+00:00",
        "schema": "harness.electronics-corpus-registry.v1",
        "policy": {},
        "sources": {},
        "counts": {"unique_pdf_sha256": 0},
        "assets": [],
        "documents": [],
        "orphans": {"ground_truth": [], "published_pinouts": []},
    }
    pristine = dict(base)
    core = {key: item for key, item in pristine.items() if key != "created_at"}
    pristine["evidence_sha256"] = hashlib.sha256(canonical_json(core)).hexdigest()
    add(
        "verify",
        "ok",
        {"value": pristine},
        _verify_expected(pristine),
    )
    tampered = dict(pristine)
    tampered["counts"] = {"unique_pdf_sha256": 1}
    add("verify", "tampered", {"value": tampered}, _verify_expected(tampered))
    bad_schema = dict(pristine)
    bad_schema["schema"] = "other.v1"
    add("verify", "bad_schema", {"value": bad_schema}, _verify_expected(bad_schema))

    return cases


def main() -> int:
    atom1_sha = hashlib.sha256(b"%PDF-atom-one").hexdigest()
    atom2_sha = hashlib.sha256(b"%PDF-atom-two").hexdigest()
    json_bytes = lambda value: json.dumps(value).encode("utf-8")  # noqa: E731
    jsonl_bytes = lambda rows: (  # noqa: E731
        "".join(json.dumps(row) + "\n" for row in rows).encode("utf-8")
    )
    out: dict = {"cases": []}

    # The pytest join case plus the join-count traps in one tree.
    out["cases"].append(
        build_case(
            "joins_and_duplicate_documents",
            {
                "pdfs/acme_atom1.pdf": b"%PDF-atom-one",
                "pdfs/copy_atom1.pdf": b"%PDF-atom-one",
                "pdfs/acme_atom2.pdf": b"%PDF-atom-two",
                "gt/acme_atom1.json": json_bytes(
                    {
                        "overview": {"manufacturer": "acme", "part_number": "ATOM1"},
                        "pinout": [{"pin_no": 1, "name": "PA0"}],
                        "clock_specs": [{"max_mhz": 100}],
                    }
                ),
                "validated/acme/acme_atom1.json": json_bytes(
                    {
                        "overview": {"manufacturer": "acme", "part_number": "ATOM1"},
                        "pinout": [{"pin_no": 1, "name": "PA0"}],
                        "_meta": {
                            "provenance": {"validation": "VALIDATED_GROUND_TRUTH"}
                        },
                    }
                ),
                "validated/_skip/ignored.json": json_bytes({"part_number": "IGNORED"}),
                "asset.jsonl": jsonl_bytes(
                    [
                        {"record_id": "direct-1", "pdf_sha256": atom2_sha},
                        {
                            "record_id": "part-1",
                            "vendor": "acme.com",
                            "part_number": "ATOM1",
                        },
                        {
                            "record_id": "ambiguous-1",
                            "part_number": "AMBIG",
                        },
                        {"record_id": "missing-1", "part_number": "NOSUCH"},
                        {"record_id": "dup-1", "part_number": "ATOM1"},
                    ]
                ),
                "gt/acme_amb1.json": json_bytes(
                    {"overview": {"manufacturer": "acme", "part_number": "AMBIG"}}
                ),
                "pdfs/acme_amb1.pdf": b"%PDF-amb-one",
                "pdfs/acme_amb2.pdf": b"%PDF-amb-two",
                "gt/acme_amb2.json": json_bytes(
                    {"overview": {"manufacturer": "acme", "part_number": "AMBIG"}}
                ),
            },
            {
                "assets": [{"name": "facts", "path": "asset.jsonl", "kind": "auto"}],
                "expected_pdf_files": 5,
            },
            {"error": None},
        )
    )

    # JSON asset with a `records` list, a whole-object asset, a directory
    # asset, and an orphan + ambiguous-stem ground truth.
    out["cases"].append(
        build_case(
            "assets_and_orphans",
            {
                "pdfs/acme_atom1.pdf": b"%PDF-atom-one",
                "pdfs/acme_atom2.pdf": b"%PDF-atom-two",
                "pdfs/other_atom2.pdf": b"%PDF-atom-two-other",
                "gt/acme_atom1.json": json_bytes(
                    {"part_number": "ATOM1", "pinout": []}
                ),
                "gt/orphan.json": json_bytes({"part_number": "LOST"}),
                "gt/acme_atom2.json": json_bytes({"part_number": "ATOM2"}),
                "validated/nested/deep.json": json_bytes(
                    {
                        "part_number": "ATOM1",
                        "_meta": {"validation": "VALIDATED_GROUND_TRUTH"},
                    }
                ),
                "packs/recs.json": json_bytes(
                    {
                        "schema": "x",
                        "records": [
                            {
                                "record_id": "r1",
                                "vendor": "Silicon Labs",
                                "base_mpn": "ATOM1",
                            },
                            {"record_id": "r2", "source_document_sha256": "e" * 64},
                            {"record_id": "r3", "series": "atom1"},
                        ],
                    }
                ),
                "packs/single.json": json_bytes({"part_number": "ATOM1"}),
                "packs/dir/one.jsonl": jsonl_bytes(
                    [{"custom_id": "c1", "part_number": "ATOM1"}]
                ),
            },
            {
                "assets": [
                    {"name": "recs", "path": "packs/recs.json", "kind": "json"},
                    {"name": "single", "path": "packs/single.json", "kind": "auto"},
                    {"name": "dir", "path": "packs/dir", "kind": "auto"},
                ],
            },
            {"error": None},
        )
    )

    # Source audit: multi-row-per-PDF lists, published join, row manifest.
    audit_core = {
        "schema": "harness.pinout-vision-source-audit.v1",
        "candidates": [
            {"pdf_path": "acme_atom1.pdf", "pdf_sha256": atom1_sha},
            {
                "pdf_path": "acme_atom1.pdf",
                "published_path": "acme/atom1.json",
            },
        ],
    }
    audit = dict(audit_core)
    audit["evidence_sha256"] = hashlib.sha256(canonical_json(audit_core)).hexdigest()
    manifest_core = {
        "schema": "harness.pinout-vision-row-dataset.v1",
        "counts": {"rows": 2},
    }
    manifest = dict(manifest_core)
    manifest["evidence_sha256"] = hashlib.sha256(
        canonical_json(manifest_core)
    ).hexdigest()
    out["cases"].append(
        build_case(
            "source_audit_and_manifest",
            {
                "pdfs/acme_atom1.pdf": b"%PDF-atom-one",
                "pdfs/acme_atom2.pdf": b"%PDF-atom-two",
                "gt/acme_atom1.json": json_bytes({"part_number": "ATOM1"}),
                "validated/acme/atom1.json": json_bytes({"part_number": "ATOM1"}),
                "audit.json": json.dumps(audit).encode("utf-8"),
                "manifest.json": json.dumps(manifest).encode("utf-8"),
            },
            {
                "source_audit": "audit.json",
                "row_dataset_manifest": "manifest.json",
            },
            {"error": None},
        )
    )

    # Error paths.
    out["cases"].append(
        build_case(
            "expected_pdf_files_mismatch",
            {"pdfs/acme_atom1.pdf": b"%PDF-atom-one"},
            {"expected_pdf_files": 3},
            {"error": "expected 3 PDFs, found 1"},
        )
    )
    out["cases"].append(
        build_case(
            "source_audit_schema",
            {
                "pdfs/acme_atom1.pdf": b"%PDF-atom-one",
                "audit.json": json_bytes({"schema": "other.v9", "candidates": []}),
            },
            {"source_audit": "audit.json"},
            {"error": "source audit schema is not supported"},
        )
    )
    bad_audit_core = {
        "schema": "harness.pinout-vision-source-audit.v1",
        "candidates": [],
    }
    bad_audit = dict(bad_audit_core)
    bad_audit["evidence_sha256"] = "0" * 64
    out["cases"].append(
        build_case(
            "source_audit_digest",
            {
                "pdfs/acme_atom1.pdf": b"%PDF-atom-one",
                "audit.json": json.dumps(bad_audit).encode("utf-8"),
            },
            {"source_audit": "audit.json"},
            {"error": "source audit has an invalid evidence_sha256"},
        )
    )
    mismatch_audit_core = {
        "schema": "harness.pinout-vision-source-audit.v1",
        "candidates": [
            {
                "pdf_path": "acme_atom1.pdf",
                "pdf_sha256": "f" * 64,
            }
        ],
    }
    mismatch_audit = dict(mismatch_audit_core)
    mismatch_audit["evidence_sha256"] = hashlib.sha256(
        canonical_json(mismatch_audit_core)
    ).hexdigest()
    out["cases"].append(
        build_case(
            "source_audit_hash_mismatch",
            {
                "pdfs/acme_atom1.pdf": b"%PDF-atom-one",
                "gt/acme_atom1.json": json_bytes({"part_number": "ATOM1"}),
                "audit.json": json.dumps(mismatch_audit).encode("utf-8"),
            },
            {"source_audit": "audit.json"},
            {"error": "source audit PDF hashes differ from the live corpus"},
        )
    )
    out["cases"].append(
        build_case(
            "row_manifest_schema",
            {
                "pdfs/acme_atom1.pdf": b"%PDF-atom-one",
                "manifest.json": json_bytes({"schema": "nope", "counts": {}}),
            },
            {"row_dataset_manifest": "manifest.json"},
            {"error": "row dataset manifest schema is not supported"},
        )
    )
    out["cases"].append(
        build_case(
            "asset_name_invalid",
            {
                "pdfs/acme_atom1.pdf": b"%PDF-atom-one",
                "asset.jsonl": jsonl_bytes([]),
            },
            {"assets": [{"name": "bad name", "path": "asset.jsonl"}]},
            {"error": "invalid asset name", "error_match": "contains"},
        )
    )
    out["cases"].append(
        build_case(
            "asset_kind_invalid",
            {
                "pdfs/acme_atom1.pdf": b"%PDF-atom-one",
                "asset.jsonl": jsonl_bytes([]),
            },
            {"assets": [{"name": "facts", "path": "asset.jsonl", "kind": "parquet"}]},
            {"error": "invalid asset kind", "error_match": "contains"},
        )
    )
    out["cases"].append(
        build_case(
            "asset_kind_uninferable",
            {
                "pdfs/acme_atom1.pdf": b"%PDF-atom-one",
                "asset.txt": b"hello",
            },
            {"assets": [{"name": "facts", "path": "asset.txt", "kind": "auto"}]},
            {"error": "cannot infer asset kind", "error_match": "contains"},
        )
    )
    out["cases"].append(
        build_case(
            "json_source_not_object",
            {
                "pdfs/acme_atom1.pdf": b"%PDF-atom-one",
                "asset.json": json_bytes([1, 2]),
            },
            {"assets": [{"name": "facts", "path": "asset.json", "kind": "json"}]},
            {"error": "JSON source must contain an object", "error_match": "contains"},
        )
    )
    out["cases"].append(
        build_case(
            "records_entry_not_object",
            {
                "pdfs/acme_atom1.pdf": b"%PDF-atom-one",
                "asset.json": json_bytes({"records": [{"ok": 1}, 5]}),
            },
            {"assets": [{"name": "facts", "path": "asset.json", "kind": "json"}]},
            {"error": "records entry 2 is not an object", "error_match": "contains"},
        )
    )
    out["cases"].append(
        build_case(
            "jsonl_line_not_object",
            {
                "pdfs/acme_atom1.pdf": b"%PDF-atom-one",
                "asset.jsonl": b'{"ok": 1}\n5\n',
            },
            {"assets": [{"name": "facts", "path": "asset.jsonl", "kind": "jsonl"}]},
            {"error": "line 2 is not an object", "error_match": "contains"},
        )
    )

    out["cases"].extend(kernel_cases())

    # write_new_registry: exclusive + read-only, run for real on a tempdir;
    # the mode and the second-call error message travel as expected.
    value = {
        "created_at": "2026-09-01T00:00:00+00:00",
        "schema": "harness.electronics-corpus-registry.v1",
        "policy": {},
        "sources": {},
        "counts": {"unique_pdf_sha256": 0},
        "assets": [],
        "documents": [],
        "orphans": {"ground_truth": [], "published_pinouts": []},
    }
    core = {key: item for key, item in value.items() if key != "created_at"}
    value["evidence_sha256"] = hashlib.sha256(canonical_json(core)).hexdigest()
    root = Path(tempfile.mkdtemp(prefix="gold_corpus_")).resolve()
    try:
        destination = root / "registry.json"
        write_new_registry(destination, value)
        mode = format(destination.stat().st_mode & 0o777, "o").zfill(4)
        try:
            write_new_registry(destination, value)
            raise AssertionError("second write must fail")
        except ValueError as exc:
            message = _normalise(str(exc), str(root))
        out["cases"].append(
            {
                "kind": "write",
                "name": "exclusive_read_only",
                "input": {"value": value},
                "expected": {"mode": mode, "error": message},
            }
        )
    finally:
        shutil.rmtree(root, ignore_errors=True)

    json.dump(out, sys.stdout, indent=1, sort_keys=True)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
