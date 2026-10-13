#!/usr/bin/env python3
"""Generate dw-recon::l0 gold from the LIVE `harness/electronics/l0.py`.

Every case is computed by the reference itself: `record` cases feed a mock
document (TOC + per-page text, image counts, drawing counts) through
`l0_record`, kernel cases call the pure readers. The images/drawings layer is
PDF-layer — the counts travel in the gold exactly as the reference measures
them.

Cases cover the traps the substrate exists for: extension classes beat every
content rule (a zip is a container whatever its bytes say; `.bin` is
deliberately unclassifiable — fail closed), class rules prefer the first
matching rule in order, the manifest prior only fills an `unknown`,
deduplicated TOC sections with lane counts, and the text-poor page buckets
(`needs_ocr` for near-blank pages with figures, `vision_candidate` for thin
pages with 2+ figures) plus `avg_page_chars`/`text_quality` thresholds.

Usage: python3 scripts/gold_l0.py > tests/fixtures/gold/l0_v1.json
"""
from __future__ import annotations

import contextlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness.electronics.l0 import (  # noqa: E402
    classify_document,
    extension_class,
    l0_record,
)


class _Page:
    def __init__(self, spec: dict):
        self.spec = spec

    def get_text(self, mode: str = "text"):
        return self.spec.get("text", "")

    def get_images(self, full: bool = True):
        return [object()] * int(self.spec.get("images", 0))

    def get_drawings(self):
        count = int(self.spec.get("drawings", 0))
        if self.spec.get("drawings_error"):
            raise RuntimeError("no drawings")
        return [object()] * count


class _Document:
    def __init__(self, toc, pages):
        self.toc = toc
        self.pages = pages
        self.page_count = len(pages)

    def get_toc(self):
        return self.toc

    def __getitem__(self, index):
        return self.pages[index]


def _run_record(name: str, case: dict) -> dict:
    toc = [list(entry) for entry in case["toc"]]
    pages = [_Page(spec) for spec in case["pages"]]
    document = _Document(toc, pages)
    result = l0_record(
        document,
        source_path=case["source_path"],
        manifest_prior=case.get("manifest_prior"),
    )
    return {
        "kind": "record",
        "name": name,
        "input": case,
        "expected": result,
    }


def record_cases() -> list[dict]:
    cases: list[dict] = []

    # The datasheet shape: TOC lanes, good text, one scanned page.
    cases.append(
        _run_record(
            "datasheet_substrate",
            {
                "source_path": "/corpus/ds/st_stm32.pdf",
                "manifest_prior": None,
                "toc": [
                    [1, "Features", 1],
                    [1, "Pin and ball descriptions", 5],
                    [1, "Absolute maximum ratings", 30],
                    [1, "Ordering information", 60],
                    [1, "Features", 1],
                ],
                "pages": (
                    [{"text": "x" * 1200}] * 4
                    + [{"text": "pin pages " + "x" * 1200}]
                    + [{"text": "x" * 1200}] * 24
                    + [{"text": "x" * 1200}] * 29
                    + [{"text": "x" * 1200}]
                    + [{"text": "almost blank", "images": 3}]
                ),
            },
        )
    )

    # Text-poor buckets: near-blank + figures -> needs_ocr; thin + figures ->
    # vision_candidate; drawings_error degrades to zero drawings.
    cases.append(
        _run_record(
            "text_poor_buckets",
            {
                "source_path": "/corpus/scan/ti_slau.pdf",
                "manifest_prior": None,
                "toc": [[1, "Schematics and layout guidelines", 1]],
                "pages": [
                    {"text": "cover", "images": 2},
                    {"text": "thin", "images": 1, "drawings": 1},
                    {"text": "thin2", "drawings_error": True},
                    {"text": "normal " + "x" * 900},
                ],
            },
        )
    )

    # Extension beats content: a zip that prints "datasheet" is a container.
    cases.append(
        _run_record(
            "extension_beats_content",
            {
                "source_path": "/corpus/bundle/design_files.zip",
                "manifest_prior": "datasheet",
                "toc": [],
                "pages": [{"text": "data sheet electrical characteristics"}],
            },
        )
    )

    # .bin is deliberately unclassifiable and the manifest prior cannot
    # rescue an extension-unknown... actually `extension_class` returns None
    # for .bin, so content rules run and the prior may fill the unknown.
    cases.append(
        _run_record(
            "bin_falls_through_to_prior",
            {
                "source_path": "/corpus/blob/firmware.bin",
                "manifest_prior": "user_manual",
                "toc": [],
                "pages": [{"text": "opaque bytes"}],
            },
        )
    )

    # No evidence anywhere: unknown, prior that maps not in _PRIOR_CLASS_MAP
    # leaves it unknown.
    cases.append(
        _run_record(
            "unknown_stays_unknown",
            {
                "source_path": "/corpus/mystery/thing.pdf",
                "manifest_prior": "unmapped_prior",
                "toc": [],
                "pages": [{"text": "zzz"}],
            },
        )
    )

    # Sections cap at 400; needs_ocr caps at 200; vision candidates at 400.
    cases.append(
        _run_record(
            "caps",
            {
                "source_path": "/corpus/big/huge.pdf",
                "manifest_prior": None,
                "toc": [[2, f"Pin descriptions {i}", i + 1] for i in range(420)],
                "pages": [{"text": "x" * 500}]
                + [{"text": "p", "images": 1}] * 210,
            },
        )
    )
    return cases


def kernel_cases() -> list[dict]:
    cases: list[dict] = []

    def add(kind: str, name: str, inputs: dict, expected) -> None:
        cases.append({"kind": kind, "name": name, "input": inputs, "expected": expected})

    for name, filename in [
        ("zip_container", "bundle.zip"),
        ("image_only", "fig.png"),
        ("sidecar", "notes.md"),
        ("sheet", "pins.xlsx"),
        ("eda", "model.ibs"),
        ("gerber", "top.gtl"),
        ("design", "board.kicad_pcb"),
        ("no_ext", "README"),
        ("document_ext", "ds.pdf"),
        ("upper_ext", "BUNDLE.ZIP"),
    ]:
        add("extension_class", name, {"filename": filename}, extension_class(filename))

    for name, front, filename, toc in [
        ("errata_text", "Silicon errata known exceptions advisories", "x.pdf", ""),
        ("datasheet_text", "data sheet electrical characteristics", "x.pdf", ""),
        ("pcn_toc", "random", "x.pdf", "Product change notification PCN number"),
        ("filename_hint", "", "sprz123.pdf", ""),
        ("unknown", "", "mystery.bin", ""),
        ("forced_zip", "data sheet datasheet", "pack.zip", ""),
        (
            "eval_document",
            "",
            "x.pdf",
            "Evaluation board schematic and BOM EVM user guide",
        ),
    ]:
        add(
            "classify_document",
            name,
            {"front_text": front, "filename": filename, "toc_titles": toc},
            classify_document(front, filename, toc),
        )
    return cases


def main() -> int:
    out: dict = {"cases": []}
    out["cases"].extend(record_cases())
    out["cases"].extend(kernel_cases())
    json.dump(out, sys.stdout, indent=1, sort_keys=True)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
