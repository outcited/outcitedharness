#!/usr/bin/env python3
"""Generate dw-recon::page_index gold from the LIVE
`harness/electronics/page_index.py`.

Every case is computed by the reference itself: document cases feed a mock
document (TOC + page texts) through `index_document`, kernel cases call the
pure readers. `locator.locate_pin_definition_pages` is a PDF-layer reader
(§5.5, not yet ported), so its outputs travel as inputs — `pin_calls`
captures the exact call arguments (including the `single_package` decision
the port must recompute) and the result object; the reference still does the
request deduplication, family stripping, and field copying under test.

Cases cover the traps the reader exists for: TOC lanes first, page-text
fallback only for lanes the TOC missed, the 3..=180 heading window,
`PIN` vs `pin_semantics` both firing on "Pin and ball descriptions", the
single-package relaxation gated on BOTH one request and front matter naming
at most that family, printed package families preferring longer tokens
(UFBGA over BGA), and `package_requests_from_ground_truth` taking the
printed count from each package string.

Usage: python3 scripts/gold_page_index.py > tests/fixtures/gold/page_index_v1.json
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import harness.electronics.page_index as page_index  # noqa: E402
from harness.electronics.page_index import (  # noqa: E402
    _printed_package_families,
    classify_section,
    index_document,
    package_requests_from_ground_truth,
)


class _Page:
    def __init__(self, text: str):
        self.text = text

    def get_text(self, mode: str = "text"):
        return self.text

    def find_tables(self):
        return None


class _Document:
    def __init__(self, toc, pages):
        self.toc = toc
        self.pages = pages
        self.page_count = len(pages)

    def get_toc(self):
        return self.toc

    def __getitem__(self, index):
        return self.pages[index]


def _run_case(name: str, case: dict) -> dict:
    """Run `index_document` on the mock document with a capturing locator."""
    toc = [list(entry) for entry in case["toc"]]
    pages = [_Page(text) for text in case["pages"]]
    document = _Document(toc, pages)
    requests = [(pkg, pins) for pkg, pins in case.get("package_requests", [])]
    pin_calls: list[dict] = []

    def locator(document_arg, *, document_id, requested_package,
                expected_package_pins, source_path, text_section_single_package):
        pin_calls.append(
            {
                "package": requested_package,
                "pins": expected_package_pins,
                "single_package": bool(text_section_single_package),
            }
        )
        canned = case["pin_results"].get(f"{requested_package}|{expected_package_pins}")
        assert canned is not None, f"{name}: no canned result for {requested_package}"
        return SimpleNamespace(**canned)

    original = page_index.locate_pin_definition_pages
    page_index.locate_pin_definition_pages = locator
    try:
        with contextlib.redirect_stdout(sys.stderr):
            result = index_document(
                document,
                document_sha256=case["document_sha256"],
                source_path=Path(case["source_path"]),
                package_requests=requests,
                fallback_maximum_pages=case.get("fallback_maximum_pages"),
            )
    except ValueError as exc:
        return {
            "kind": "document",
            "name": name,
            "input": {**case, "pin_calls": pin_calls},
            "expected": None,
            "error": str(exc),
        }
    finally:
        page_index.locate_pin_definition_pages = original
    return {
        "kind": "document",
        "name": name,
        "input": {**case, "pin_calls": pin_calls},
        "expected": result,
        "error": None,
    }


def _pin_result(package: str, pins: int, pages: list[int]) -> dict:
    return {
        "requested_package": package,
        "expected_package_pins": pins,
        "status": "bound",
        "reason": "table_header_exact",
        "pages_1based": tuple(pages),
        "column_header": f"{package} ({pins} pin)",
        "column_headers_all": (f"{package} ({pins} pin)",),
        "corroborating_jedec_ids": (),
    }


def document_cases() -> list[dict]:
    cases: list[dict] = []
    sha = "a" * 64

    # The pytest shape: TOC covers two lanes, text fallback fills the rest.
    cases.append(
        _run_case(
            "toc_then_text_fallback",
            {
                "toc": [[1, "Pin descriptions", 2]],
                "pages": [
                    "Features\nApplications",
                    "Pin descriptions",
                    "Absolute maximum ratings",
                ],
                "document_sha256": sha,
                "source_path": "/corpus/atom.pdf",
                "package_requests": [],
                "fallback_maximum_pages": None,
                "pin_results": {},
            },
        )
    )

    # TOC already covers every printed lane: fallback must add nothing, and a
    # TOC entry whose lanes are all present stays page-deduped.
    cases.append(
        _run_case(
            "toc_only_no_fallback",
            {
                "toc": [
                    [1, "Features", 1],
                    [2, "Pin and ball descriptions", 2],
                    [1, "Ordering information", 3],
                ],
                "pages": [
                    "Features",
                    "Pin and ball descriptions\nOrdering information",
                    "Bill of materials",
                ],
                "document_sha256": sha,
                "source_path": "/corpus/atom2.pdf",
                "package_requests": [],
                "fallback_maximum_pages": None,
                "pin_results": {},
            },
        )
    )

    # The heading window: a 2-char and a 181-char line are not headings.
    long_line = "Electrical characteristics " + "x" * 160
    cases.append(
        _run_case(
            "heading_window",
            {
                "toc": [],
                "pages": ["Ok\n" + long_line, "Electrical characteristics"],
                "document_sha256": sha,
                "source_path": "/corpus/atom3.pdf",
                "package_requests": [],
                "fallback_maximum_pages": None,
                "pin_results": {},
            },
        )
    )

    # Single request, front matter names only that family: the relaxation is
    # safe and the locator sees single_package=True.
    cases.append(
        _run_case(
            "single_package_relaxation",
            {
                "toc": [[1, "Pin descriptions", 2]],
                "pages": ["Features\nLQFP64 package", "Pin descriptions"],
                "document_sha256": sha,
                "source_path": "/corpus/atom4.pdf",
                "package_requests": [["LQFP64", 64]],
                "fallback_maximum_pages": None,
                "pin_results": {"LQFP64|64": _pin_result("LQFP64", 64, [2])},
            },
        )
    )

    # Multi-package front matter (LQFP plus QFN) with one request: the lone
    # binding is a weak proxy, so the relaxation stays off.
    cases.append(
        _run_case(
            "multi_package_front_matter",
            {
                "toc": [[1, "Pin descriptions", 2]],
                "pages": ["LQFP64 and QFN packages", "Pin descriptions"],
                "document_sha256": sha,
                "source_path": "/corpus/atom5.pdf",
                "package_requests": [["LQFP64", 64]],
                "fallback_maximum_pages": None,
                "pin_results": {"LQFP64|64": _pin_result("LQFP64", 64, [2])},
            },
        )
    )

    # Two requests: never single-package; requests dedupe and keep order.
    cases.append(
        _run_case(
            "two_requests_dedupe",
            {
                "toc": [[1, "Pin descriptions", 2]],
                "pages": ["LQFP64 package", "Pin descriptions"],
                "document_sha256": sha,
                "source_path": "/corpus/atom6.pdf",
                "package_requests": [
                    ["LQFP64", 64],
                    ["LQFP64", 64],
                    ["QFN-32", 32],
                ],
                "fallback_maximum_pages": None,
                "pin_results": {
                    "LQFP64|64": _pin_result("LQFP64", 64, [2]),
                    "QFN-32|32": _pin_result("QFN-32", 32, [2]),
                },
            },
        )
    )

    # fallback_maximum_pages caps the text scan.
    cases.append(
        _run_case(
            "fallback_page_cap",
            {
                "toc": [],
                "pages": ["Features", "Absolute maximum ratings"],
                "document_sha256": sha,
                "source_path": "/corpus/atom7.pdf",
                "package_requests": [],
                "fallback_maximum_pages": 1,
                "pin_results": {},
            },
        )
    )

    # The document digest guard.
    cases.append(
        _run_case(
            "invalid_sha",
            {
                "toc": [],
                "pages": ["Features"],
                "document_sha256": "abc",
                "source_path": "/corpus/atom8.pdf",
                "package_requests": [],
                "fallback_maximum_pages": None,
                "pin_results": {},
            },
        )
    )
    return cases


def kernel_cases() -> list[dict]:
    cases: list[dict] = []

    def add(kind: str, name: str, inputs: dict, expected) -> None:
        cases.append({"kind": kind, "name": name, "input": inputs, "expected": expected})

    for name, title, expected in [
        ("pin_and_ball", "5. Pin and ball descriptions", ("pin_or_ball", "pin_semantics")),
        ("absolute_max", "Absolute maximum ratings", ("parametrics",)),
        ("ordering", "Ordering information", ("opn_decoder",)),
        ("bom", "Bill of materials", ("bom_parts",)),
        ("design_specs", "Design requirements", ("design_specs",)),
        ("test_results", "Test results", ("test_results",)),
        ("schematic", "Schematics and layout guidelines", ("schematic_layout",)),
        ("typical", "Typical operating characteristics", ("typical_characteristics",)),
        ("power", "Power consumption", ("power_modes", "parametrics")),
        ("unknown", "Revision history", ()),
        ("long_token", "UFBGA package", ("parametrics",)),
        ("ballout", "Ballout", ("pin_or_ball",)),
        ("terminal", "Terminal assignments", ("pin_or_ball",)),
        ("multiplex", "Pin multiplexing", ("pin_semantics",)),
    ]:
        add("classify_section", name, {"title": title}, list(classify_section(title)))

    for name, records in [
        (
            "printed_counts",
            [
                {
                    "pinout": {
                        "packages": ["144-Pin QFP (PGE)", "337-Ball BGA (ZWT)"],
                        "declared_pin_total": 144,
                    }
                }
            ],
        ),
        (
            "declared_fallback",
            [{"pinout": {"packages": ["QFN"], "declared_pin_total": 32}}],
        ),
        (
            "dedupe_and_sort",
            [
                {"pinout": {"packages": ["LQFP64", "LQFP64"], "declared_pin_total": 64}},
                {"pinout": {"packages": ["BGA-100"]}},
            ],
        ),
        ("no_pinout", [{"part_number": "X"}]),
        (
            "zero_expected_skipped",
            [{"pinout": {"packages": ["QFN"], "declared_pin_total": 0}}],
        ),
    ]:
        add(
            "package_requests",
            name,
            {"records": records},
            [list(item) for item in package_requests_from_ground_truth(records)],
        )

    for name, pages in [
        ("multi", ["LQFP64 plus QFN-32 and UFBGA packages"]),
        ("none", ["no packages here"]),
        ("case", ["lqfp64 and tssop"]),
    ]:
        add(
            "printed_package_families",
            name,
            {"pages": pages},
            sorted(_printed_package_families(_Document([], [_Page(t) for t in pages]))),
        )
    return cases


def main() -> int:
    out: dict = {"cases": []}
    out["cases"].extend(document_cases())
    out["cases"].extend(kernel_cases())
    json.dump(out, sys.stdout, indent=1, sort_keys=True)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
