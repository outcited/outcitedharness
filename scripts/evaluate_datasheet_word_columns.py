#!/usr/bin/env python3
"""Evaluate deterministic word-level column extraction on a frozen cohort.

Reads the frozen cohort labels plus the structural work queue (for source
PDF paths), runs harness.electronics.word_columns on each work item's
page, selects the column matching the requested package (falling back to
generic pin columns), and scores the merged rows against the frozen
expected pins. Writes an immutable evaluation receipt.
"""

from __future__ import annotations

import argparse
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

from harness.electronics.word_columns import (  # noqa: E402
    PackageColumn,
    PinRowClaim,
    extract_pin_columns,
    package_pin_bogey,
    select_package_columns,
)

EVALUATION_SCHEMA = "harness.datasheet-word-columns-evaluation.v1"


def _canonical_pin(value: Any) -> str:
    text = str(value).strip().upper()
    if text.isdigit():
        return str(int(text))
    return re.sub(r"[^A-Z0-9]", "", text)


def _pairs(rows: list[dict[str, Any]]) -> set[tuple[str, str]]:
    return {
        (_canonical_pin(row.get("pin_no")), _canonical_pin(row.get("name")))
        for row in rows
        if row.get("pin_no") is not None and row.get("name")
    }


def _claim_pairs(claims: list[PinRowClaim]) -> set[tuple[str, str]]:
    return {
        (_canonical_pin(claim.pin_no), _canonical_pin(claim.name))
        for claim in claims
    }


def _select_columns(
    columns: dict[str, PackageColumn],
    requested_package: str,
) -> list[PackageColumn]:
    return select_package_columns(columns, requested_package)


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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--work-queue", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    labels = [
        json.loads(line)
        for line in args.labels.read_text().splitlines()
        if line.strip()
    ]
    work_queue = json.loads(args.work_queue.read_text())
    scope_by_key = {
        (item["document_sha256"], item["page_1based"]): item
        for item in work_queue["work"]
    }

    page_cache: dict[tuple[str, int], dict[str, PackageColumn]] = {}
    items: list[dict[str, Any]] = []
    exact = 0
    for label in labels:
        key = (label["document_sha256"], label["page_1based"])
        scope = scope_by_key.get(key)
        if scope is None or not scope.get("source_path"):
            items.append(
                {"work_id": label["work_id"], "status": "no_source_path"}
            )
            continue
        source = Path(scope["source_path"])
        page = label["page_1based"]
        cache_key = (str(source.resolve()), page)
        if cache_key not in page_cache:
            try:
                page_cache[cache_key] = extract_pin_columns(source, page)
            except Exception as error:
                page_cache[cache_key] = {}
                items.append(
                    {
                        "work_id": label["work_id"],
                        "status": "extraction_error",
                        "error": f"{type(error).__name__}: {error}",
                    }
                )
                continue
        columns = page_cache[cache_key]
        requested = str(label.get("package") or "")
        selected = _select_columns(columns, requested)
        claims = [claim for column in selected for claim in column.rows]
        truth = _pairs(label.get("expected_pins") or [])
        got = _claim_pairs(claims)
        is_exact = bool(truth) and got == truth
        if is_exact:
            exact += 1
        bogey = package_pin_bogey(requested)
        items.append(
            {
                "work_id": label["work_id"],
                "status": "exact" if is_exact else "diff",
                "capability": label.get("capability"),
                "package": requested,
                "page_1based": page,
                "columns_found": {
                    key: len(column.rows)
                    for key, column in columns.items()
                },
                "selected_columns": [column.column_key for column in selected],
                "truth_rows": len(truth),
                "extracted_rows": len(got),
                "missing": sorted(truth - got)[:12],
                "extra": sorted(got - truth)[:12],
                "bogey": bogey,
                "claims": [
                    {
                        "pin_no": claim.pin_no,
                        "name": claim.name,
                        "identifier_bbox": claim.identifier_span.bbox,
                        "name_bbox": claim.name_span.bbox,
                    }
                    for claim in claims[:400]
                ],
            }
        )

    result = {
        "schema": EVALUATION_SCHEMA,
        "labels_path": str(args.labels.resolve()),
        "work_queue_path": str(args.work_queue.resolve()),
        "summary": {
            "work_items": len(items),
            "exact": exact,
            "diff_or_error": len(items) - exact,
            "deterministic_coverage": (
                round(exact / len(items), 4) if items else None
            ),
        },
        "items": items,
    }
    _write_new_json(args.output, result)
    print(json.dumps(result["summary"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
