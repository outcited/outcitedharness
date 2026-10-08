#!/usr/bin/env python3
"""Size ground-truth gaps against the pages' own digital text.

For every pinout row example in the training_data_pinout_rows_v1 dataset,
re-derive the visible rows from the source PDF page with the deterministic
word-columns extractor, scoped to the example's crop region, and compare
with the stored ground-truth response. Classifies each example as:

- exact: stored rows equal the printed rows in the region
- gt_gap: every stored row is printed, and the region prints additional
  rows the stored ground truth lacks
- gt_conflict_or_unparsed: one or more stored rows are not found in the
  region's printed rows (either the extractor cannot parse the layout or
  the stored row is not printed on the page)

Writes an immutable audit receipt with per-example detail.
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
    PinRowClaim,
    extract_pin_columns,
)

AUDIT_SCHEMA = "harness.datasheet-gt-word-columns-audit.v1"


def _canonical_pin(value: Any) -> str:
    text = str(value).strip().upper()
    if text.isdigit():
        return str(int(text))
    return re.sub(r"[^A-Z0-9]", "", text)


def _region(alignment: dict[str, Any]) -> tuple[float, float, float, float]:
    boxes = []
    for key in ("header_bbox", "body_bbox"):
        box = alignment.get(key)
        if isinstance(box, list) and len(box) == 4:
            boxes.append(box)
    if not boxes:
        return (0.0, 0.0, float("inf"), float("inf"))
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )


def _in_region(claim: PinRowClaim, region: tuple[float, float, float, float]) -> bool:
    x0, y0, x1, y1 = claim.identifier_span.bbox
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    return (
        region[0] - 6 <= cx <= region[2] + 6
        and region[1] - 6 <= cy <= region[3] + 6
    )


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
    parser.add_argument("--row-dataset", type=Path, required=True)
    parser.add_argument("--corpus-registry", type=Path, required=True)
    parser.add_argument(
        "--pdf-root",
        type=Path,
        default=Path(
            "/Volumes/M5_4TB/DigiKey_Reference_Designs/pdf_cache"
        ),
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--maximum-examples", type=int, default=0)
    args = parser.parse_args()

    registry = json.loads(args.corpus_registry.read_text())
    pdf_paths: dict[str, str] = {}
    for document in registry.get("documents", []):
        sha = document.get("document_sha256")
        paths = document.get("paths") or []
        if not sha or not paths:
            continue
        for name in paths:
            candidate = args.pdf_root / name
            if candidate.exists():
                pdf_paths[str(sha)] = str(candidate)
                break

    examples: list[dict[str, Any]] = []
    for split in ("train", "validation", "test"):
        path = args.row_dataset / "canonical" / f"{split}.jsonl"
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            examples.append(json.loads(line))
    if args.maximum_examples:
        examples = examples[: args.maximum_examples]

    cache: dict[tuple[str, int], list[PinRowClaim]] = {}
    failures = 0
    results: list[dict[str, Any]] = []
    counts = {
        "exact": 0,
        "gt_gap": 0,
        "gt_conflict_or_unparsed": 0,
        "no_printed_rows_in_region": 0,
        "extraction_error": 0,
    }
    for index, example in enumerate(examples, 1):
        sha = example["provenance"]["pdf_sha256"]
        page = int(example["alignment"]["page_1based"])
        source = pdf_paths.get(sha)
        if source is None:
            counts["extraction_error"] += 1
            continue
        cache_key = (sha, page)
        if cache_key not in cache:
            try:
                columns = extract_pin_columns(Path(source), page)
                cache[cache_key] = [
                    claim
                    for column in columns.values()
                    for claim in column.rows
                ]
            except Exception:
                cache[cache_key] = []
        claims = cache[cache_key]
        region = _region(example["alignment"])
        region_claims = [claim for claim in claims if _in_region(claim, region)]
        printed = {
            (_canonical_pin(c.pin_no), _canonical_pin(c.name))
            for c in region_claims
        }
        try:
            response = json.loads(example["response"])
        except json.JSONDecodeError:
            counts["extraction_error"] += 1
            continue
        stored = {
            (
                _canonical_pin(pin.get("pin_no")),
                _canonical_pin(pin.get("name")),
            )
            for pin in response.get("pins", [])
            if pin.get("pin_no") is not None and pin.get("name")
        }
        if not printed:
            classification = "no_printed_rows_in_region"
        elif stored == printed:
            classification = "exact"
        elif stored and stored <= printed:
            classification = "gt_gap"
        else:
            classification = "gt_conflict_or_unparsed"
        counts[classification] += 1
        if classification != "exact":
            failures += 1
            results.append(
                {
                    "example_id": example["example_id"],
                    "record_id": example.get("record_id"),
                    "split": example.get("split"),
                    "pdf_sha256": sha[:16],
                    "page_1based": page,
                    "package": example["alignment"].get("package"),
                    "classification": classification,
                    "stored_rows": len(stored),
                    "printed_rows": len(printed),
                    "missing_from_stored": sorted(printed - stored)[:16],
                    "unmatched_stored_rows": sorted(stored - printed)[:16],
                }
            )
        if index % 1000 == 0:
            print(f"audited {index}/{len(examples)}", flush=True)

    total = sum(counts.values())
    summary = {
        "schema": AUDIT_SCHEMA,
        "row_dataset": str(args.row_dataset.resolve()),
        "corpus_registry": str(args.corpus_registry.resolve()),
        "summary": {
            "examples": total,
            "unique_pages": len(cache),
            **counts,
            "gt_gap_rate": (
                round(counts["gt_gap"] / total, 4) if total else None
            ),
            "conflict_or_unparsed_rate": (
                round(
                    counts["gt_conflict_or_unparsed"] / total, 4
                )
                if total
                else None
            ),
        },
        "non_exact_examples": results,
    }
    _write_new_json(args.output, summary)
    print(json.dumps(summary["summary"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
