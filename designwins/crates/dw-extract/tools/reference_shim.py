#!/usr/bin/env python3
"""The Python reference, served over stdin/stdout for the replacement gate.

Same contract as scripts/parity_reference.py (the dw-replace pattern): one
subject per line on stdin, one JSON value per line on stdout, same order.
`dw-extract`'s `extract-replace` bin calls exactly this — both engines see the
same inputs in the same run and neither can share state with the other.

Subjects are the extract_* fixture lines (input half); outputs are what the
reference produces NOW. If the reference drifted since the fixture was
generated, the live gate sees it.

The same bounded seams as tools/gen_gold.py apply for `claims` (fs reads
substituted, evidence URIs fixed) — see that file's module docstring.

Usage::

    python3 designwins/crates/dw-extract/tools/reference_shim.py grid < subjects.jsonl
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

CREATED_AT = datetime(2026, 9, 1, tzinfo=timezone.utc)


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


def run_grid(subject):
    from harness.electronics.key_features_grid import build_grid

    return build_grid(subject["record"])


def run_bom(subject):
    import harness.electronics.bom_extract as bom_extract

    return bom_extract.bom_from_document(FakeDoc(subject["pages"]))


def run_queue(subject):
    from harness.electronics.extraction import build_extraction_work_queue

    return build_extraction_work_queue(
        subject["registry"], aligned_document_sha256=subject["aligned"]
    )


def run_canonical(subject):
    from harness.electronics.claims import canonical_json, stable_id

    return {
        "canonical_hex": canonical_json(subject["payload"]).hex(),
        "stable_claim_id": stable_id("claim", subject["payload"]),
    }


def _registry_for(documents):
    """A registry that passes verify_corpus_registry — iter_* verifies first."""
    import hashlib

    from harness.electronics.claims import canonical_json

    reg = {
        "schema": "harness.electronics-corpus-registry.v1",
        "sources": {"pdf_root": "{root}/pdfs", "ground_truth_root": "{root}/gt"},
        "documents": documents,
        "counts": {"unique_pdf_sha256": len(documents)},
    }
    reg["evidence_sha256"] = hashlib.sha256(
        canonical_json({k: v for k, v in reg.items() if k not in ("created_at", "evidence_sha256")})
    ).hexdigest()
    return reg


def run_claims(subject):
    """Claim assembly over the reference's own loops (fs seams substituted)."""
    import harness.electronics.extraction as extraction

    real = (
        extraction._document_records,
        extraction._row_records,
        extraction._pdf_uris,
        extraction.sha256_file,
    )
    try:
        if subject["kind"] == "ground_truth":
            extraction._pdf_uris = lambda registry: {subject["document"]["document_sha256"]: subject["source_uri"]}
            extraction._document_records = lambda registry: iter(
                [
                    (
                        subject["document"],
                        {
                            "summary": subject["summary"],
                            "value": subject["value"],
                            "path": Path(subject["stem"]),
                        },
                    )
                ]
            )
            registry = _registry_for([subject["document"]])
            claims = extraction.iter_ground_truth_claims(registry, created_at=CREATED_AT)
        else:
            extraction._pdf_uris = lambda registry: dict(subject["pdf_uris"])
            extraction._row_records = lambda root, splits: iter([subject["example"]])
            extraction.sha256_file = lambda path: subject["example"]["image_sha256"][1]
            registry = _registry_for(
                [{"document_sha256": "a" * 64, "paths": ["acme_atom1.pdf"]}]
            )
            claims = extraction.iter_pinout_row_claims(
                registry, REPO, created_at=CREATED_AT, splits=("train",)
            )
        return [c.model_dump(mode="json", by_alias=True) for c in claims]
    finally:
        (
            extraction._document_records,
            extraction._row_records,
            extraction._pdf_uris,
            extraction.sha256_file,
        ) = real


MODES = {
    "grid": run_grid,
    "bom": run_bom,
    "queue": run_queue,
    "canonical": run_canonical,
    "claims": run_claims,
}


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode not in MODES:
        print(f"usage: reference_shim.py <{'|'.join(MODES)}> < subjects.jsonl", file=sys.stderr)
        return 2
    run = MODES[mode]
    for line in sys.stdin:
        if not line.strip():
            sys.stdout.write("null\n")
            continue
        sys.stdout.write(json.dumps(run(json.loads(line)), ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
