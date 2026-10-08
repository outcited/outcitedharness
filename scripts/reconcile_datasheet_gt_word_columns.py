#!/usr/bin/env python3
"""Reconcile owned ground-truth pinouts against the pages' printed rows.

For every corpus document that has sealed Claude ground truth and
deterministic word-columns sweep rows:

- GT rows are matched against the union of exactly-located package
  columns (exact or identity-matched headers only; generic fallback is
  never used, so cross-package contamination is impossible)
- single-package documents additionally yield printed-rows-missing-from-
  GT evidence (the GT extension queue)
- name disagreements on matched pins yield GT error candidates with page
  and word bounding-box evidence
- packages whose columns were never located are classified as extractor
  coverage gaps, not GT suspicion

Every GT record file is verified against the corpus-registry sha256
before use. No GT row is modified; this tool only emits evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
import os
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harness.electronics.locator import match_package_column  # noqa: E402

RECONCILIATION_SCHEMA = "harness.datasheet-gt-word-columns-reconciliation.v2"


def _canonical_pin(value: Any) -> str:
    text = str(value).strip().upper()
    if text.isdigit():
        return str(int(text))
    return re.sub(r"[^A-Z0-9]", "", text)


def _names_agree(printed: str, expected: str) -> bool:
    return (
        printed == expected
        or expected in printed
        or printed in expected
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _write_new_json(path: Path, value: dict[str, Any]) -> None:
    destination = path.expanduser().resolve()
    if destination.exists() or destination.is_symlink():
        raise SystemExit(f"immutable output already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(
                json.dumps(
                    value,
                    ensure_ascii=False,
                    sort_keys=True,
                    indent=2,
                    allow_nan=False,
                ).encode("utf-8")
                + b"\n"
            )
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o444)
        os.link(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _exact_columns_for_package(
    sweep_pages: dict[str, dict[str, Any]],
    package: str,
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    requested_key = re.sub(r"[^A-Z0-9]", "", str(package).upper())
    for page_text, columns in sorted(
        sweep_pages.items(), key=lambda item: int(item[0])
    ):
        page = int(page_text)
        for value in columns.values():
            header_key = re.sub(
                r"[^A-Z0-9]", "", str(value["header"]).upper()
            )
            exact = header_key == requested_key
            matched = (
                match_package_column(str(package), [value["header"]])
                is not None
            )
            if not (exact or matched):
                continue
            for row in value["rows"]:
                selected.append(
                    {
                        "pin_no": _canonical_pin(row[0]),
                        "name": _canonical_pin(row[1]),
                        "raw_name": row[1],
                        "page_1based": page,
                        "identifier_bbox": row[2],
                        "name_bbox": row[3],
                        "header": value["header"],
                    }
                )
    return selected


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-registry", type=Path, required=True)
    parser.add_argument("--sweep-extractions", type=Path, required=True)
    parser.add_argument("--gt-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    registry = json.loads(args.corpus_registry.read_text())
    sweep: dict[str, dict[str, Any]] = {}
    for line in args.sweep_extractions.read_text().splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get("pages"):
            sweep[record["document_sha256"]] = record

    counts = {
        "gt_documents": 0,
        "gt_records_verified": 0,
        "gt_sha_mismatch": 0,
        "documents_compared": 0,
        "single_package_documents": 0,
        "gt_rows_total": 0,
        "gt_rows_confirmed": 0,
        "gt_rows_name_disagreement": 0,
        "gt_rows_not_printed_on_parsed_pages": 0,
        "packages_located": 0,
        "packages_not_located": 0,
        "printed_rows_missing_from_gt": 0,
        "documents_with_gt_extension_evidence": 0,
        "documents_with_name_disagreements": 0,
    }
    extension_queue: list[dict[str, Any]] = []
    disagreement_queue: list[dict[str, Any]] = []

    for document in registry["documents"]:
        gt_entries = document.get("ground_truth") or []
        if not gt_entries:
            continue
        counts["gt_documents"] += 1
        entry = gt_entries[0]
        gt_path = args.gt_root / entry["path"].split("/")[-1]
        if not gt_path.exists():
            continue
        if _sha256_file(gt_path) != entry["sha256"]:
            counts["gt_sha_mismatch"] += 1
            continue
        counts["gt_records_verified"] += 1
        try:
            record = json.loads(gt_path.read_text())
        except json.JSONDecodeError:
            continue
        pinout = record.get("pinout") or {}
        packages = pinout.get("packages") or []
        gt_rows = pinout.get("pin_functions_summary") or []
        if not packages or not gt_rows:
            continue
        sweep_record = sweep.get(document["document_sha256"])
        if sweep_record is None or not sweep_record.get("pages"):
            continue
        counts["documents_compared"] += 1
        single_package = len(packages) == 1
        if single_package:
            counts["single_package_documents"] += 1

        located: dict[str, list[dict[str, Any]]] = {}
        for package in packages:
            rows = _exact_columns_for_package(
                sweep_record["pages"], str(package)
            )
            if rows:
                located[str(package)] = rows
                counts["packages_located"] += 1
            else:
                counts["packages_not_located"] += 1
        printed_union: dict[str, dict[str, Any]] = {}
        for rows in located.values():
            for row in rows:
                printed_union.setdefault(row["pin_no"], row)

        expected = {
            _canonical_pin(row.get("pin_no")): _canonical_pin(
                row.get("name")
            )
            for row in gt_rows
            if row.get("pin_no") is not None and row.get("name")
        }
        counts["gt_rows_total"] += len(expected)
        confirmed = 0
        disagreements: list[dict[str, Any]] = []
        not_printed = 0
        for pin, name in expected.items():
            printed = printed_union.get(pin)
            if printed is None:
                not_printed += 1
                continue
            if _names_agree(printed["name"], name):
                confirmed += 1
            else:
                disagreements.append(
                    {
                        "pin_no": pin,
                        "gt_name": name,
                        "printed_name": printed["raw_name"],
                        "page_1based": printed["page_1based"],
                        "header": printed["header"],
                        "identifier_bbox": printed["identifier_bbox"],
                        "name_bbox": printed["name_bbox"],
                    }
                )
        counts["gt_rows_confirmed"] += confirmed
        counts["gt_rows_name_disagreement"] += len(disagreements)
        counts["gt_rows_not_printed_on_parsed_pages"] += not_printed

        if single_package and located:
            package = str(packages[0])
            gaps = [
                row
                for pin, row in printed_union.items()
                if pin not in expected
            ]
            if gaps:
                counts["printed_rows_missing_from_gt"] += len(gaps)
                counts["documents_with_gt_extension_evidence"] += 1
                extension_queue.append(
                    {
                        "document_sha256": document["document_sha256"],
                        "paths": document.get("paths"),
                        "gt_record": entry["path"],
                        "package": package,
                        "gt_rows": len(expected),
                        "printed_rows": len(printed_union),
                        "gt_rows_confirmed": confirmed,
                        "gt_rows_not_printed": not_printed,
                        "missing_rows": [
                            {
                                "pin_no": row["raw_name"]
                                and row["pin_no"],
                                "name": row["raw_name"],
                                "page_1based": row["page_1based"],
                                "identifier_bbox": row["identifier_bbox"],
                                "name_bbox": row["name_bbox"],
                            }
                            for row in sorted(
                                gaps,
                                key=lambda r: (
                                    r["page_1based"],
                                    r["pin_no"],
                                ),
                            )
                        ][:200],
                    }
                )
        if disagreements:
            counts["documents_with_name_disagreements"] += 1
            disagreement_queue.append(
                {
                    "document_sha256": document["document_sha256"],
                    "paths": document.get("paths"),
                    "gt_record": entry["path"],
                    "packages": packages,
                    "disagreements": disagreements[:40],
                }
            )

    result = {
        "schema": RECONCILIATION_SCHEMA,
        "corpus_registry": str(args.corpus_registry.resolve()),
        "sweep_extractions": str(args.sweep_extractions.resolve()),
        "gt_root": str(args.gt_root.resolve()),
        "summary": counts,
        "gt_extension_queue": extension_queue,
        "gt_name_disagreement_queue": disagreement_queue,
    }
    _write_new_json(args.output, result)
    print(json.dumps(counts, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
