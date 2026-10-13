#!/usr/bin/env python3
"""Generate dw-recon::page_evidence gold from the LIVE
`harness/electronics/page_evidence.py`.

Every case is computed by the reference itself: `evidence` cases feed a mock
document (page blocks, dict structure, table grids, page rect, OCR results)
through `extract_profile_evidence`, kernel cases call `selected_pages`. The
PyMuPDF layer (blocks/dict/find_tables/OCR) is PDF-layer — its outputs
travel in the gold exactly as the reference reads them.

Cases cover the traps the extractor exists for: `selected_pages` capping
each lane's pages but letting exact pin locations override the cap with
both pin lanes, tables only on pin/parametric lanes, the layout IR
preserving text-layer geometry and falling back to normalized blocks, the
OCR fallback chain (`text_layer` -> `ocr_fallback` -> `no_text_after_ocr` /
`ocr_failed` with the `Type: message` error string), and
`needs_visual_verification` firing only on the three failure statuses.

Usage: python3 scripts/gold_page_evidence.py > tests/fixtures/gold/page_evidence_v1.json
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness.electronics.claims import canonical_json  # noqa: E402
from harness.electronics.page_evidence import (  # noqa: E402
    extract_profile_evidence,
    selected_pages,
)


class _Page:
    def __init__(self, spec: dict):
        self.spec = spec
        self.rect = type("Rect", (), {"width": spec.get("width", 612.0), "height": spec.get("height", 792.0)})()

    def get_text(self, mode: str = "blocks", textpage=None):
        if mode == "blocks":
            return self.spec.get("ocr_blocks" if textpage is not None else "blocks", [])
        if mode == "dict":
            if self.spec.get("dict_error"):
                raise RuntimeError("dict extraction failed")
            return self.spec.get("dict", {})
        return ""

    def get_textpage_ocr(self, language: str = "eng"):
        if self.spec.get("ocr_error"):
            raise RuntimeError(self.spec["ocr_error"])
        return object()

    def find_tables(self):
        if self.spec.get("tables") is None:
            return None
        tables = self.spec["tables"]
        return type(
            "Finder",
            (),
            {
                "tables": [
                    type(
                        "Table",
                        (),
                        {
                            "extract": staticmethod(lambda t=table: t["rows"]),
                            "bbox": table.get("bbox"),
                        },
                    )()
                    for table in tables
                ]
            },
        )()


class _Document:
    def __init__(self, pages):
        self.pages = pages
        self.page_count = len(pages)

    def __getitem__(self, index):
        return self.pages[index]


def _run_evidence(name: str, case: dict) -> dict:
    pages = [_Page(spec) for spec in case["pages"]]
    document = _Document(pages)
    try:
        outputs = list(
            extract_profile_evidence(
                document,
                case["profile"],
                maximum_pages_per_lane=case["maximum_pages_per_lane"],
                enable_ocr_fallback=case.get("enable_ocr_fallback", False),
                ocr_language=case.get("ocr_language", "eng"),
            )
        )
    except ValueError as exc:
        return {
            "kind": "evidence",
            "name": name,
            "input": case,
            "expected": None,
            "error": str(exc),
        }
    return {
        "kind": "evidence",
        "name": name,
        "input": case,
        "expected": outputs,
        "error": None,
    }


def evidence_cases() -> list[dict]:
    cases: list[dict] = []

    # Text layer present: blocks + dict layout + tables on a pin lane.
    dict_ir = {
        "blocks": [
            {
                "type": 0,
                "bbox": [70.0, 60.0, 500.0, 120.0],
                "lines": [
                    {
                        "bbox": [70.0, 60.0, 300.0, 75.0],
                        "spans": [{"text": "Pin descriptions"}],
                    },
                    {
                        "bbox": [70.0, 80.0, 200.0, 95.0],
                        "spans": [{"text": "PE2"}, {"text": " GPIO"}],
                    },
                ],
            },
            {"type": 1, "bbox": [50.0, 200.0, 300.0, 400.0]},
        ]
    }
    cases.append(
        _run_evidence(
            "text_layer_with_tables",
            {
                "maximum_pages_per_lane": 2,
                "profile": {
                    "document_sha256": "a" * 64,
                    "source_path": "/corpus/ds/atom.pdf",
                    "lane_pages": {"pin_or_ball": [3, 3, 9], "parametrics": [30]},
                    "exact_pin_locations": [],
                },
                "pages": [
                    {"width": 612.0, "height": 792.0, "blocks": [], "dict": {"blocks": []}},
                    {"width": 612.0, "height": 792.0, "blocks": [], "dict": {"blocks": []}},
                    {
                        "width": 612.0,
                        "height": 792.0,
                        "blocks": [
                            [70.0, 60.0, 500.0, 120.0, "Pin descriptions\nPE2 GPIO"],
                        ],
                        "dict": dict_ir,
                        "tables": [
                            {"rows": [["LQFP64", "Name"], ["1", "PE2"]], "bbox": [60.0, 50.0, 400.0, 200.0]}
                        ],
                    },
                    {"width": 612.0, "height": 792.0, "blocks": [], "dict": {"blocks": []}},
                    {"width": 612.0, "height": 792.0, "blocks": [], "dict": {"blocks": []}},
                    {"width": 612.0, "height": 792.0, "blocks": [], "dict": {"blocks": []}},
                    {"width": 612.0, "height": 792.0, "blocks": [], "dict": {"blocks": []}},
                    {"width": 612.0, "height": 792.0, "blocks": [], "dict": {"blocks": []}},
                    {"width": 612.0, "height": 792.0, "blocks": [], "dict": {"blocks": []}},
                    {"width": 612.0, "height": 792.0, "blocks": [], "dict": {"blocks": []}},
                    {
                        "width": 612.0,
                        "height": 792.0,
                        "blocks": [[70.0, 60.0, 500.0, 120.0, "Overview text"]],
                        "dict": {"blocks": []},
                    },
                    {"width": 612.0, "height": 792.0, "blocks": [], "dict": {"blocks": []}},
                    {"width": 612.0, "height": 792.0, "blocks": [], "dict": {"blocks": []}},
                    {"width": 612.0, "height": 792.0, "blocks": [], "dict": {"blocks": []}},
                    {"width": 612.0, "height": 792.0, "blocks": [], "dict": {"blocks": []}},
                    {"width": 612.0, "height": 792.0, "blocks": [], "dict": {"blocks": []}},
                    {"width": 612.0, "height": 792.0, "blocks": [], "dict": {"blocks": []}},
                    {"width": 612.0, "height": 792.0, "blocks": [], "dict": {"blocks": []}},
                    {"width": 612.0, "height": 792.0, "blocks": [], "dict": {"blocks": []}},
                    {"width": 612.0, "height": 792.0, "blocks": [], "dict": {"blocks": []}},
                    {"width": 612.0, "height": 792.0, "blocks": [], "dict": {"blocks": []}},
                    {"width": 612.0, "height": 792.0, "blocks": [], "dict": {"blocks": []}},
                    {"width": 612.0, "height": 792.0, "blocks": [], "dict": {"blocks": []}},
                    {"width": 612.0, "height": 792.0, "blocks": [], "dict": {"blocks": []}},
                    {"width": 612.0, "height": 792.0, "blocks": [], "dict": {"blocks": []}},
                    {"width": 612.0, "height": 792.0, "blocks": [], "dict": {"blocks": []}},
                    {"width": 612.0, "height": 792.0, "blocks": [], "dict": {"blocks": []}},
                    {"width": 612.0, "height": 792.0, "blocks": [], "dict": {"blocks": []}},
                    {"width": 612.0, "height": 792.0, "blocks": [], "dict": {"blocks": []}},
                    {"width": 612.0, "height": 792.0, "blocks": [], "dict": {"blocks": []}},
                    {
                        "width": 612.0,
                        "height": 792.0,
                        "blocks": [[70.0, 60.0, 500.0, 120.0, "Electrical text"]],
                        "dict": {"blocks": []},
                        "tables": [],
                    },
                ],
            },
        )
    )

    # Exact pin locations override the per-lane cap and add both pin lanes;
    # a no-text page takes the OCR fallback.
    cases.append(
        _run_evidence(
            "pin_override_and_ocr_fallback",
            {
                "maximum_pages_per_lane": 1,
                "enable_ocr_fallback": True,
                "profile": {
                    "document_sha256": "b" * 64,
                    "source_path": "/corpus/ds/ti.pdf",
                    "lane_pages": {"series_summary": [1, 2]},
                    "exact_pin_locations": [
                        {"status": "send", "pages_1based": [7, 8]},
                        {"status": "withhold", "pages_1based": [99]},
                    ],
                },
                "pages": [
                    {
                        "width": 612.0,
                        "height": 792.0,
                        "blocks": [[70.0, 60.0, 500.0, 120.0, "Features"]],
                        "dict": {"blocks": []},
                    },
                    {
                        "width": 612.0,
                        "height": 792.0,
                        "blocks": [[70.0, 60.0, 500.0, 120.0, "Applications"]],
                        "dict": {"blocks": []},
                    },
                    {"width": 612.0, "height": 792.0, "blocks": [], "dict": {"blocks": []}},
                    {"width": 612.0, "height": 792.0, "blocks": [], "dict": {"blocks": []}},
                    {"width": 612.0, "height": 792.0, "blocks": [], "dict": {"blocks": []}},
                    {"width": 612.0, "height": 792.0, "blocks": [], "dict": {"blocks": []}},
                    {
                        "width": 612.0,
                        "height": 792.0,
                        "blocks": [],
                        "dict": {"blocks": []},
                        "ocr_blocks": [[70.0, 60.0, 500.0, 120.0, "OCR pin text"]],
                    },
                    {
                        "width": 612.0,
                        "height": 792.0,
                        "blocks": [],
                        "dict": {"blocks": []},
                    },
                ],
            },
        )
    )

    # OCR failure and no-text-after-ocr statuses.
    cases.append(
        _run_evidence(
            "ocr_failure_and_no_text",
            {
                "maximum_pages_per_lane": 2,
                "enable_ocr_fallback": True,
                "profile": {
                    "document_sha256": "c" * 64,
                    "source_path": "/corpus/ds/scan.pdf",
                    "lane_pages": {"parametrics": [1, 2]},
                    "exact_pin_locations": [],
                },
                "pages": [
                    {
                        "width": 612.0,
                        "height": 792.0,
                        "blocks": [],
                        "dict": {"blocks": []},
                        "ocr_error": "TesseractNotFound",
                    },
                    {
                        "width": 612.0,
                        "height": 792.0,
                        "blocks": [],
                        "dict": {"blocks": []},
                        "ocr_blocks": [],
                    },
                ],
            },
        )
    )

    # dict extraction failure falls back to normalized blocks.
    cases.append(
        _run_evidence(
            "normalized_blocks_fallback",
            {
                "maximum_pages_per_lane": 1,
                "profile": {
                    "document_sha256": "d" * 64,
                    "source_path": "/corpus/ds/old.pdf",
                    "lane_pages": {"opn_decoder": [1]},
                    "exact_pin_locations": [],
                },
                "pages": [
                    {
                        "width": 595.0,
                        "height": 842.0,
                        "blocks": [
                            [70.0, 60.0, 500.0, 120.0, "Ordering information"],
                            [70.0, 130.0, 500.0, 145.0, ""],
                        ],
                        "dict_error": True,
                    }
                ],
            },
        )
    )

    # Out-of-bounds indexed page: the generator raises.
    cases.append(
        _run_evidence(
            "page_out_of_bounds",
            {
                "maximum_pages_per_lane": 5,
                "profile": {
                    "document_sha256": "e" * 64,
                    "source_path": "/corpus/ds/bad.pdf",
                    "lane_pages": {"series_summary": [9]},
                    "exact_pin_locations": [],
                },
                "pages": [
                    {"width": 612.0, "height": 792.0, "blocks": [], "dict": {"blocks": []}},
                ],
            },
        )
    )
    return cases


def kernel_cases() -> list[dict]:
    cases: list[dict] = []

    def add(kind: str, name: str, inputs: dict, expected) -> None:
        cases.append({"kind": kind, "name": name, "input": inputs, "expected": expected})

    for name, profile, cap in [
        (
            "caps_each_lane",
            {
                "lane_pages": {"pin_or_ball": [3, 1, 3, 2], "series_summary": [5]},
                "exact_pin_locations": [],
            },
            2,
        ),
        (
            "pin_locations_override",
            {
                "lane_pages": {"series_summary": []},
                "exact_pin_locations": [
                    {"status": "send", "pages_1based": [7, 7, 8]},
                    {"status": "withhold", "pages_1based": [50]},
                ],
            },
            1,
        ),
        ("empty_profile", {}, 3),
    ]:
        output = selected_pages(profile, maximum_pages_per_lane=cap)
        serialized = {
            str(page): sorted(lanes) for page, lanes in sorted(output.items())
        }
        add(
            "selected_pages",
            name,
            {"profile": profile, "maximum_pages_per_lane": cap},
            serialized,
        )
    # The cap guard.
    try:
        selected_pages({}, maximum_pages_per_lane=0)
        raised = None
    except ValueError as exc:
        raised = str(exc)
    add(
        "selected_pages",
        "cap_guard",
        {"profile": {}, "maximum_pages_per_lane": 0},
        {"error": "maximum_pages_per_lane must be positive", "raised": raised},
    )
    return cases


def main() -> int:
    out: dict = {"cases": []}
    out["cases"].extend(evidence_cases())
    out["cases"].extend(kernel_cases())
    json.dump(out, sys.stdout, indent=1, sort_keys=True)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
