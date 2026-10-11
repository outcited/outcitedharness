#!/usr/bin/env python3
"""Generate extraction gold fixtures from the LIVE Python reference.

Writes tests/fixtures/gold/extract_* (this crate's fixtures) by running the
reference's own code paths and recording what they produce. Re-run this after
any reference change; the fixtures are samples of the reference (DECISIONS.md
D8), never the other way round.

Seams (documented, bounded): the reference's file-system reads are substituted
(`_document_records`, `_row_records`, `sha256_file`, `_pdf_uris`) so the gold
is machine-independent — evidence URIs are fixed as file:///gold-corpus/...
Everything else (make_claim, stable_id, canonical_json, every assembly loop,
every regex) runs live. The fs wrappers' own failure modes are gated by the
Rust unit tests' negative controls.

Usage: python3 designwins/crates/dw-extract/tools/gen_gold.py
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO))

OUT = REPO / "tests" / "fixtures" / "gold"
GOLD_URI_ROOT = "file:///gold-corpus"

import harness.electronics.extraction as extraction  # noqa: E402
from harness.electronics.claims import (  # noqa: E402
    canonical_json,
    make_claim,
    stable_id,
)
from harness.electronics.key_features_grid import build_grid  # noqa: E402
import harness.electronics.bom_extract as bom_extract  # noqa: E402


def normalize_key_order(obj):
    """Recursively sort object keys.

    Dictionaries feed `build_grid`'s iteration order (peripheral_instances,
    chapters.peripheral_classes), which decides row order. Rust parses JSON
    into a BTreeMap (serde_json `preserve_order` is off — enabling it would
    change serde_json's feature unification for the whole workspace, and that
    file is not ours). Sorting here makes both engines iterate identically so
    the gate compares outputs order-exact; documented as the fixture's one
    input normalization (D8: the fixture is a sample of the reference — this
    is the sample's input form, not a bend of either side).
    """
    if isinstance(obj, dict):
        return {k: normalize_key_order(obj[k]) for k in sorted(obj)}
    if isinstance(obj, list):
        return [normalize_key_order(v) for v in obj]
    return obj


def write_jsonl(name: str, rows: list) -> None:
    path = OUT / name
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"{name}: {len(rows)} cases, {path.stat().st_size} bytes")


# --------------------------------------------------------------- canonical

def gen_canonical() -> None:
    values = [
        None,
        True,
        False,
        0,
        1,
        -55,
        100,
        1.5e-08,
        1e-05,
        1e16,
        1e15,
        -55.0,
        0.1,
        0.0001,
        100.0,
        2.5,
        -0.0,
        "",
        "0.5",
        "8 KB data flash",
        "quote \" \\ \n \t",
        "unicode: µA °C – 〔〕",
        [],
        [1, 2.5, "x", None],
        {},
        {"b": 1, "a": 2, "c": {"z": 1.0, "y": [1.5e-08]}},
        {"field": "pin.name", "value": ["PA0", "PA1"], "unit": None},
    ]
    rows = []
    for i, value in enumerate(values):
        payload = {
            "entity": {
                "entity_id": "opn:acme:ATOM1",
                "grain": "opn",
                "canonical_id": "ATOM1",
                "vendor": "acme",
                "family": None,
                "package": None,
                "parent_ids": [],
            },
            "field": "clock_specs.max_freq_mhz",
            "value": value,
            "unit": "MHz",
            "conditions": {"source_record_id": "rec-1", "z": 1, "a": 2},
            "claim_class": "semantic_label",
            "extraction_method": "imported_frontier_ground_truth",
            "evidence": [
                {
                    "kind": "source_record",
                    "document_sha256": "a" * 64,
                    "source_uri": f"{GOLD_URI_ROOT}/acme_atom1.pdf",
                    "artifact_sha256": "b" * 64,
                    "page_1based": None,
                    "bbox": None,
                    "table_index": None,
                    "row_index": None,
                    "column_index": None,
                    "quoted_text": None,
                }
            ],
            "source_claim_ids": [],
            "model": None,
        }
        rows.append(
            {
                "case": f"canonical-{i:02d}",
                "value": value,
                "canonical_hex": canonical_json(payload).hex(),
                "stable_claim_id": stable_id("claim", payload),
                "payload": payload,
            }
        )
    write_jsonl("extract_canonical_gold_v1.jsonl", rows)


# --------------------------------------------------------------------- bom

class FakeTable:
    def __init__(self, rows):
        self._rows = rows

    def extract(self):
        return self._rows


class FakeTables:
    def __init__(self, tables):
        self.tables = tables


class FakePage:
    def __init__(self, text, tables):
        self._text = text
        self._tables = tables

    def get_text(self):
        return self._text

    def find_tables(self):
        return FakeTables([FakeTable(t) for t in self._tables])


class FakeDoc:
    def __init__(self, pages):
        self._pages = [FakePage(p.get("text", ""), p.get("tables", [])) for p in pages]
        self.page_count = len(self._pages)

    def __getitem__(self, i):
        return self._pages[i]


def gen_bom() -> None:
    cases = []

    def case(name, pages):
        expected = bom_extract.bom_from_document(FakeDoc(pages))
        cases.append({"case": name, "pages": pages, "expected": expected})

    header = [
        "Designator",
        "Quantity",
        "Value",
        "PartNumber",
        "Manufacturer",
        "Description",
        "PackageReference",
    ]
    # 1. Standalone BOM sheet, column flow.
    case(
        "flow-happy",
        [
            {
                "text": "TIDA-010003\nEvaluates: TIDA-010003\nBill of Materials\n"
                + "\n".join(
                    header
                    + [
                        "R1,R2",
                        "4",
                        "10k",
                        "RC0603FR-0710KL",
                        "Yageo",
                        "RES SMD 10K OHM",
                        "0603",
                        "C1",
                        "2",
                        "100n",
                        "GRM188R71C104KA01D",
                        "Murata",
                        "CAP CER 100NF",
                        "0603",
                    ]
                ),
                "tables": [],
            }
        ],
    )
    # 2. EV-kit embedded table.
    case(
        "table-happy",
        [
            {
                "text": "TMDSDOCK28335\nBill of Materials\n",
                "tables": [
                    [
                        ["Designator", "Quantity", "Value", "Part Number", "Manufacturer", "Description", "Package"],
                        ["R1", "1", "10k", "RC0603FR-0710KL", "Yageo", "RES 10K", "0603"],
                        ["C1", "4", "100n", "GRM188R71C104", "Murata", "CAP 100NF", "0603"],
                    ]
                ],
            }
        ],
    )
    # 3. Nullish cells excluded; qty plausibility refuses garbage.
    case(
        "flow-nullish-and-qty",
        [
            {
                "text": "\n".join(
                    header
                    + [
                        "R1",
                        "1",
                        "10k",
                        "RC0603",
                        "Yageo",
                        "DNP",
                        "0603",
                        "C1",
                        "22",
                        "N/A",
                        "GRM188",
                        "Murata",
                        "CAP",
                        "NC",
                        "U1",
                        "1",
                        "3.3V",
                        "",
                        "TI",
                        "LDO",
                        "SOT23",
                    ]
                ),
                "tables": [],
            }
        ],
    )
    # 4. Header not found -> no items.
    case("no-header", [{"text": "Random prose without any BOM header at all.", "tables": []}])
    # 5. Board name from "Bill of Materials" fallback (last line before).
    case(
        "board-fallback",
        [
            {
                "text": "User's Guide\nTMDXDOCK28143\nEvaluation Board\nBill of Materials\nDesignator\nQuantity\nValue\nPartNumber\nManufacturer\nDescription\nPackageReference\nR1\n1\n1k\nRC0603\nYageo\nRES\n0603",
                "tables": [],
            }
        ],
    )
    # 6. Table on page 2 beats smaller flow page 1.
    case(
        "table-wins-over-flow",
        [
            {
                "text": "\n".join(header + ["R1", "1", "1k", "RC0603", "Yageo", "RES", "0603"]),
                "tables": [],
            },
            {
                "text": "",
                "tables": [
                    [
                        ["Designator", "Qty", "Value", "MPN", "Vendor", "Description", "Footprint"],
                        ["R1", "1", "1k", "RC0603", "Yageo", "RES 1K", "0603"],
                        ["R2", "1", "2k", "RC0603-2K", "Yageo", "RES 2K", "0603"],
                        ["R3", "1", "3k", "RC0603-3K", "Yageo", "RES 3K", "0603"],
                    ]
                ],
            },
        ],
    )
    # 7. Designator plausibility: -source tokens cannot pass as designators.
    case(
        "flow-designator-plausibility",
        [
            {
                "text": "\n".join(
                    header
                    + [
                        "GND",
                        "1",
                        "-",
                        "RC0603",
                        "Yageo",
                        "RES",
                        "0603",
                        "JP1,JP2",
                        "2",
                        "jumper",
                        "HEADER-2",
                        "Samtec",
                        "CONN HDR",
                        "2.54",
                    ]
                ),
                "tables": [],
            }
        ],
    )
    write_jsonl("extract_bom_gold_v1.jsonl", cases)


# -------------------------------------------------------------------- grid

def gen_grid() -> None:
    records = []
    for line in (REPO / "results" / "family-documents-cr-20260908" / "records.jsonl").open():
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        if "error" in rec:
            continue
        records.append((len(line), rec))
    records.sort(key=lambda t: t[0])

    def interesting(rec):
        return bool(
            rec.get("io_by_package")
            or rec.get("memory")
            or rec.get("prose_facts")
            or rec.get("chapter_features")
        )

    small_interesting = [r for _, r in records if interesting(r)][:4]
    smallest = [r for _, r in records[:3]]
    # One mid-sized record with instance counts and chapters.
    with_inst = [
        r
        for _, r in records
        if r.get("peripheral_instances") and r.get("chapters", {}).get("peripheral_classes")
    ][:2]

    # A synthetic record exercising the sram bank check, dual-home classes,
    # vendor section headings (2b) and the instance stem/title choice.
    synthetic = {
        "_meta": {
            "extracted_by": "gen_gold",
            "extracted_at": "2026-09-09T00:00:00+00:00",
            "document_sha256": "c" * 64,
            "source_path": "/corpus/RA4C1.pdf",
            "page_count": 12,
            "pages_read": 12,
            "vendor": None,
            "models_used": [],
        },
        "identity": {
            "document_id": "RA4C1",
            "revision": "Rev 1.00",
            "title_verbatim": "RA4C1 Datasheet",
            "lines_covered": {
                "group_tokens": ["RA4C1"],
                "wildcards": [],
                "parts": [],
                "family_phrases": [],
                "filename_tokens": ["RA4C1"],
            },
        },
        "peripheral_instances": {
            "SCI": {
                "instances": [
                    {"instance": "SCI0", "weak": False, "first_page": 4},
                    {"instance": "SCI1", "weak": False, "first_page": 4},
                    {"instance": "SCI2", "weak": True, "first_page": 5},
                ],
                "count_asserted": True,
            },
            "CAN": {
                "instances": [{"instance": "CAN0", "weak": False, "first_page": 6}],
                "count_asserted": True,
            },
        },
        "features": [
            {
                "verbatim": "Arm Cortex-M33 core",
                "classes": [],
                "vendor_section": "■ Arm Cortex-M33 Core with Floating Point Unit (FPU)",
                "numbers": [],
                "level": 1,
                "receipt": {"page": 2},
                "source": "features",
            },
            {
                "verbatim": "128-bit unique ID",
                "label": "128-bit unique ID",
                "classes": ["crypto"],
                "vendor_section": "Security",
                "numbers": [],
                "level": 2,
                "parent": "AES",
                "receipt": {"page": 3},
                "source": "features",
            },
            {
                "verbatim": "Watchdog timer",
                "label": "Watchdog timer",
                "classes": ["watchdog"],
                "vendor_section": "Timers",
                "numbers": [],
                "level": 1,
                "receipt": {"page": 3},
                "source": "features",
            },
            {
                "verbatim": "48 MHz maximum CPU speed",
                "label": "Up to 48 MHz",
                "classes": ["cpu_core"],
                "vendor_section": "Processing",
                "numbers": [{"value": 48, "unit": "MHz"}],
                "qualifier_verbatim": "Up to",
                "level": 1,
                "receipt": {"page": 2},
                "source": "features",
            },
        ],
        "chapter_features": [
            {
                "verbatim": "128 KB code flash",
                "chapter_class": "flash",
                "numbers": [{"value": 128, "unit": "KB"}],
                "qualifier_verbatim": None,
                "section": "Memory",
                "receipt": {"page": 7},
            }
        ],
        "memory": [
            {
                "verbatim": "SRAM1",
                "size_kb": 64,
                "qualifier_verbatim": None,
                "receipt": {"page": 7},
            },
            {
                "verbatim": "SRAM2",
                "size_kb": 32,
                "qualifier_verbatim": None,
                "receipt": {"page": 7},
            },
            {
                "verbatim": "Data flash",
                "size_kb": 8,
                "qualifier_verbatim": None,
                "receipt": {"page": 7},
            },
        ],
        "prose_facts": [
            {
                "kind": "supply_range",
                "verbatim": "2.7 to 3.6 V",
                "value": [2.7, 3.6],
                "unit": "V",
                "receipt": {"page": 1},
            },
            {
                "kind": "packages",
                "verbatim": "LQFP 32, QFN 32",
                "value": ["LQFP 32", "QFN 32"],
                "receipt": {"page": 1},
            },
        ],
        "io_by_package": [
            {
                "pattern": "stated",
                "io_count": 24,
                "pin_count": 32,
                "package": "LQFP",
                "verbatim": "I/O pins: 24",
                "receipt": {"page": 8},
            }
        ],
        "chapters": {
            "peripheral_classes": {
                "watchdog": [
                    {"title": "13. Watchdog Timer", "page": 30, "level": 1},
                    {"title": "13.1 Overview", "page": 30, "level": 2},
                ],
                "crypto": [{"title": "14. Security", "page": 40, "level": 1}],
            }
        },
        "schema": "harness.electronics-family-document.v1",
    }

    rows = []
    for i, rec in enumerate(smallest + small_interesting + with_inst + [synthetic]):
        rec = normalize_key_order(rec)
        rows.append(
            {
                "case": f"grid-{i:02d}",
                "record": rec,
                "expected": build_grid(rec),
            }
        )
    write_jsonl("extract_grid_gold_v1.jsonl", rows)


# ------------------------------------------------------------------- queue

def registry_for(documents: list) -> dict:
    reg = {
        "schema": "harness.electronics-corpus-registry.v1",
        "sources": {
            "pdf_root": "{root}/pdfs",
            "ground_truth_root": "{root}/gt",
            "validated_root": "{root}/validated",
        },
        "documents": documents,
        "counts": {"unique_pdf_sha256": len(documents)},
    }
    reg["evidence_sha256"] = hashlib.sha256(
        canonical_json({k: v for k, v in reg.items() if k not in ("created_at", "evidence_sha256")})
    ).hexdigest()
    return reg


def gen_queue() -> None:
    cases = []
    doc_a = {
        "document_sha256": "a" * 64,
        "paths": ["acme_atom1.pdf"],
        "vendors": ["acme"],
        "record_ids": ["acme_atom1"],
        "ground_truth": [{"record_id": "gt-1", "sha256": "b" * 64, "sections": ["overview", "clock_specs"]}],
    }
    doc_b = {
        "document_sha256": "d" * 64,
        "paths": [],
        "vendors": [],
        "record_ids": [],
    }
    reg = registry_for([doc_a, doc_b])
    cases.append(
        {
            "case": "queue-mixed",
            "registry": reg,
            "aligned": ["a" * 64],
            "expected": extraction.build_extraction_work_queue(
                reg, aligned_document_sha256=["a" * 64]
            ),
        }
    )
    reg2 = registry_for(
        [
            {
                "document_sha256": "e" * 64,
                "paths": ["x.pdf"],
                "ground_truth": [{"record_id": "gt-9", "sha256": "f" * 64, "sections": ["overview"]}],
            }
        ]
    )
    cases.append(
        {
            "case": "queue-no-align",
            "registry": reg2,
            "aligned": [],
            "expected": extraction.build_extraction_work_queue(reg2, aligned_document_sha256=[]),
        }
    )
    write_jsonl("extract_queue_gold_v1.jsonl", cases)


# ------------------------------------------------------------------ claims

def gen_claims() -> None:
    document = {"document_sha256": "a" * 64}
    summary = {"record_id": "gt-1", "sha256": "b" * 64, "vendor": "acme"}
    gt_value = {
        "overview": {
            "manufacturer": "acme",
            "part_number": "ATOM1",
            "description": "An MCU for deterministic tests.",
        },
        "clock_specs": {"max_freq_mhz": 100},
        "power_modes": {"run_ma": 4.5, "": "empty-name", "blank": "   "},
        "pinout": {"packages": ["LQFP2"], "declared_pin_total": 2, "rows_emitted": 1},
        "_meta": {"batch_id": "ignored"},
    }
    gt_cases = [
        {
            "case": "gt-full",
            "document": document,
            "summary": summary,
            "value": gt_value,
            "stem": "acme_atom1",
        },
        {
            "case": "gt-stem-fallback",
            "document": document,
            "summary": {"record_id": "gt-2", "sha256": "c" * 64},
            "value": {"clock_specs": {"max_freq_mhz": 48}},
            "stem": "fallback_part",
        },
    ]
    pinout_case = {
        "case": "pinout-full",
        "example": {
            "alignment": {
                "body_bbox": [10.0, 20.0, 30.0, 40.0],
                "header_bbox": [1.0, 2.0, 3.0, 4.0],
                "package": "LQFP2",
                "page_1based": 5,
            },
            "example_id": "example-1",
            "image_sha256": ["c" * 64, "b" * 64],
            "images": ["images/train/header.png", "images/train/body.png"],
            "prompt": "Extract one row.",
            "provenance": {
                "frontier_batch_id": "msgbatch_rows",
                "pdf_sha256": "a" * 64,
            },
            "record_id": "acme_atom1",
            "response": json.dumps(
                {
                    "pins": [
                        {"pin_no": 1, "name": "PA0", "type": "gpio", "functions": ["ADC0"], "dir": "I/O"},
                        {"pin_no": 2, "name": "VDD", "type": "power", "dir": "P"},
                        {"pin_no": 3, "name": "NC"},
                    ]
                },
                separators=(",", ":"),
            ),
            "split": "train",
        },
        "pdf_uris": {"a" * 64: f"{GOLD_URI_ROOT}/acme_atom1.pdf"},
    }

    # Live reference: the claim-assembly loops run unmodified; only the fs
    # seams (_document_records, _row_records, _pdf_uris, sha256_file) are
    # substituted so the gold is machine-independent.
    rows = []
    created_at = "2026-09-01T00:00:00+00:00"
    real_doc_records = extraction._document_records
    real_row_records = extraction._row_records
    real_pdf_uris = extraction._pdf_uris
    real_sha = extraction.sha256_file
    try:
        for tc in gt_cases:
            tc_uri = f"{GOLD_URI_ROOT}/{tc['stem']}.pdf"
            tc["source_uri"] = tc_uri
            extraction._pdf_uris = lambda registry, _u=tc_uri: {"a" * 64: _u}
            extraction._document_records = lambda registry, _tc=tc: iter(
                [(tc["document"], {"summary": tc["summary"], "value": tc["value"], "path": Path(tc["stem"])})]
            )
            claims = [
                c.model_dump(mode="json", by_alias=True)
                for c in extraction.iter_ground_truth_claims(
                    registry_for([tc["document"]]),
                    created_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
                )
            ]
            rows.append({"kind": "ground_truth", **tc, "created_at": created_at, "expected": claims})

        extraction._pdf_uris = lambda registry: dict(pinout_case["pdf_uris"])
        extraction._row_records = lambda root, splits: iter([pinout_case["example"]])
        extraction.sha256_file = lambda path: pinout_case["example"]["image_sha256"][1]
        claims = [
            c.model_dump(mode="json", by_alias=True)
            for c in extraction.iter_pinout_row_claims(
                registry_for([{"document_sha256": "a" * 64, "paths": ["acme_atom1.pdf"]}]),
                REPO,  # any real dir: _row_records is the substituted seam
                created_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
                splits=("train",),
            )
        ]
        rows.append({"kind": "pinout", **pinout_case, "created_at": created_at, "expected": claims})
    finally:
        extraction._document_records = real_doc_records
        extraction._row_records = real_row_records
        extraction._pdf_uris = real_pdf_uris
        extraction.sha256_file = real_sha

    write_jsonl("extract_claims_gold_v1.jsonl", rows)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    gen_canonical()
    gen_bom()
    gen_grid()
    gen_queue()
    gen_claims()
    print("gold regenerated from live reference")
