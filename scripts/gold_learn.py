#!/usr/bin/env python3
"""Generate dw-learn gold from the LIVE `scripts/build_lora_pairs{,_v2}.py`
and `scripts/{lora_gate,gate_v2}.py`.

Every case is computed by the reference itself: pair cases monkeypatch the
module's filesystem/PDF seams (`index_pdfs`/`pdf_text`/`sha256` for v1,
`load_burn_claims`/`index_pdfs`/`extract_pdf_text` for v2) to replay captured
fixtures and run `main()` end to end, capturing the exact JSONL bytes; gate
cases call the pure `norm_value`/`score`/report assembly. The pymupdf/sqlite
seams are captured inputs, exactly as the PDF layer travels in the recon
golds.

Cases cover the traps the loop exists for: v1's rows-with-symbol-and-value
filter and sha-match gate, v2's `min_claims` and `(symbol, value, unit)`
dedupe and unresolved-stem skip, the train/holdout seeded split, the shared
field-level precision/recall/f1 scoring with unit normalization
(`UNIT_TO_OHM`), the tolerance (`max(1e-9, |wv|*1e-4)`), and the PASS bars
(f1 >= 0.90 AND precision >= 0.95).

Usage: python3 scripts/gold_learn.py > tests/fixtures/gold/learn_v1.json
"""
from __future__ import annotations

import contextlib
import importlib.util
import json
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


class _NoGlob:
    """Module-level `Path.glob` over the M5_4TB volume blocks for minutes;
    stub it out while a reference module's top level runs."""

    def __enter__(self):
        self.original = Path.glob
        Path.glob = lambda self, pattern: iter([])  # type: ignore[assignment]
        return self

    def __exit__(self, *exc):
        Path.glob = self.original  # type: ignore[assignment]
        return False


def _load(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, REPO / relpath)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    with _NoGlob():
        spec.loader.exec_module(module)
    return module


def _v1_case(build_v1, name, docs, pdfs):
    """Run v1's `main()` with the PDF seam replayed from fixtures."""
    root = Path(tempfile.mkdtemp(prefix="gold_learn_v1_"))
    try:
        ds = root / "grids"
        ds.mkdir()
        with (ds / "grid_rows.jsonl").open("w") as f:
            for row in docs:
                f.write(json.dumps(row) + "\n")
        out = root / "pairs.jsonl"
        pdf_paths = {key: Path(key) for key in pdfs}
        original = (build_v1.DEFAULT_DATASETS, build_v1.index_pdfs, build_v1.pdf_text, build_v1.sha256)
        build_v1.DEFAULT_DATASETS = [ds]
        build_v1.index_pdfs = lambda roots: dict(pdf_paths)
        build_v1.pdf_text = lambda p: pdfs[Path(p).name]["text"]
        build_v1.sha256 = lambda p: pdfs[Path(p).name]["sha256"]
        argv = sys.argv
        sys.argv = ["build_lora_pairs.py", "--out", str(out)]
        try:
            with contextlib.redirect_stdout(sys.stderr):
                build_v1.main()
        finally:
            sys.argv = argv
            (build_v1.DEFAULT_DATASETS, build_v1.index_pdfs, build_v1.pdf_text, build_v1.sha256) = original
        return out.read_text(encoding="utf-8")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _v2_case(build_v2, name, stem_claims, pdf_index, texts, min_claims=3, limit=None):
    """Run v2's `main()` with the burn/PDF seams replayed from fixtures."""
    root = Path(tempfile.mkdtemp(prefix="gold_learn_v2_"))
    try:
        original = (
            build_v2.OUT_DIR,
            build_v2.load_burn_claims,
            build_v2.index_pdfs,
            build_v2.extract_pdf_text,
            build_v2.BURN_DIRS,
        )
        build_v2.OUT_DIR = root / "lora-v2-pairs"
        build_v2.load_burn_claims = lambda: stem_claims
        build_v2.index_pdfs = lambda: {k: Path(v) for k, v in pdf_index.items()}
        # The reference reads text via find_pdf_for_stem -> extract_pdf_text;
        # replay the capture keyed on filename.
        build_v2.extract_pdf_text = lambda p: texts.get(Path(p).name)
        build_v2.BURN_DIRS = []
        try:
            with contextlib.redirect_stdout(sys.stderr):
                build_v2.main(limit, min_claims)
        finally:
            (
                build_v2.OUT_DIR,
                build_v2.load_burn_claims,
                build_v2.index_pdfs,
                build_v2.extract_pdf_text,
                build_v2.BURN_DIRS,
            ) = original
        train = (root / "lora-v2-pairs/llamafactory/power-tables-v2/train.jsonl").read_text()
        holdout = (root / "lora-v2-pairs/llamafactory/power-tables-v2/holdout.jsonl").read_text()
        return train, holdout
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _report(tp, fp, fn):
    precision = tp / max(1, tp + fp)
    recall = tp / max(1, tp + fn)
    f1 = 2 * precision * recall / max(1e-9, precision + recall)
    return {
        "fields_matched": tp,
        "fields_wrong_or_extra": fp,
        "fields_missed": fn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "pass": f1 >= 0.90 and precision >= 0.95,
    }


def _gate_cases(lora_gate):
    cases = []
    for name, want, got in [
        ("exact_match",
         [{"symbol": "Vout", "value": 3.3, "unit": "V"}],
         [{"symbol": "Vout", "value": 3.3, "unit": "V"}]),
        ("unit_normalized",
         [{"symbol": "Rds", "value": 120, "unit": "mohm"}],
         [{"symbol": "Rds", "value": 0.12, "unit": "ohm"}]),
        ("invented_symbol",
         [{"symbol": "Vin", "value": 5, "unit": "V"}],
         [{"symbol": "Vin", "value": 5, "unit": "V"}, {"symbol": "Ghost", "value": 1, "unit": "V"}]),
        ("missed_symbol",
         [{"symbol": "Vin", "value": 5, "unit": "V"}, {"symbol": "Vout", "value": 3.3, "unit": "V"}],
         [{"symbol": "Vin", "value": 5, "unit": "V"}]),
        ("value_out_of_tolerance",
         [{"symbol": "Vin", "value": 5, "unit": "V"}],
         [{"symbol": "Vin", "value": 5.1, "unit": "V"}]),
        ("param_alias",
         [{"param": "Vin", "value": 5, "unit": "V"}],
         [{"symbol": "vin", "value": 5, "unit": "V"}]),
    ]:
        tp, fp, fn = lora_gate.score(want, got)
        cases.append({"kind": "gate_score", "name": name,
                      "input": {"want": want, "got": got}, "expected": [tp, fp, fn]})
    for name, tp, fp, fn in [
        ("pass", 95, 4, 5),
        ("fail_precision", 90, 6, 10),
        ("fail_f1", 80, 10, 20),
        ("empty", 0, 0, 0),
        # recall = 1/32 = 0.03125 exactly. Python `round` is round-half-to-even:
        # 0.0312, not a naive `(x*10_000).round()/10_000` (0.0313). The ported
        # `round4` must reproduce Python's correctly-rounded decimal.
        ("bankers_round_half_even", 1, 0, 31),
    ]:
        cases.append({"kind": "gate_report", "name": name,
                      "input": {"tp": tp, "fp": fp, "fn": fn}, "expected": _report(tp, fp, fn)})
    for name, value, unit in [
        ("mohm", 120, "mohm"), ("kohm", 1.2, "kohm"), ("ohm", 5, "ohm"),
        ("plain", 3.3, "V"), ("none", None, "V"), ("junk", "abc", "V"), ("upper", 1, "KOHM"),
    ]:
        cases.append({"kind": "norm_value", "name": name,
                      "input": {"value": value, "unit": unit},
                      "expected": lora_gate.norm_value(value, unit)})
    return cases


def main() -> int:
    build_v1 = _load("build_lora_pairs", "scripts/build_lora_pairs.py")
    build_v2 = _load("build_lora_pairs_v2", "scripts/build_lora_pairs_v2.py")
    lora_gate = _load("lora_gate", "scripts/lora_gate.py")

    out: dict = {"cases": []}
    full_text = "Electrical characteristics\n" + ("Vout 3.3 V\n" * 40)

    v1_cases = [
        ("v1_simple",
         [{"document_sha256": "a" * 64, "source_artifact": "acme-ldo.pdf", "vendor": "acme",
           "scope_as_printed": "ACME-LDO", "symbol": "Vout", "value": 3.3, "unit": "V",
           "quantity_qualifier": None, "condition_verbatim": "TA=25C"}],
         {"acme-ldo.pdf": {"sha256": "a" * 64, "text": full_text}}),
        ("v1_sha_mismatch_skipped",
         [{"document_sha256": "a" * 64, "source_artifact": "acme-ldo.pdf", "vendor": "acme",
           "scope_as_printed": "ACME-LDO", "symbol": "Vout", "value": 3.3, "unit": "V"}],
         {"acme-ldo.pdf": {"sha256": "b" * 64, "text": full_text}}),
        ("v1_missing_symbol_skipped",
         [{"document_sha256": "a" * 64, "source_artifact": "acme-ldo.pdf", "vendor": "acme",
           "scope_as_printed": "ACME-LDO", "symbol": None, "value": 3.3, "unit": "V"}],
         {"acme-ldo.pdf": {"sha256": "a" * 64, "text": full_text}}),
        ("v1_short_text_skipped",
         [{"document_sha256": "a" * 64, "source_artifact": "acme-ldo.pdf", "vendor": "acme",
           "scope_as_printed": "ACME-LDO", "symbol": "Vout", "value": 3.3, "unit": "V"}],
         {"acme-ldo.pdf": {"sha256": "a" * 64, "text": "too short"}}),
        ("v1_multiple_rows_sorted_by_sha",
         [{"document_sha256": "b" * 64, "source_artifact": "b.pdf", "vendor": "b",
           "scope_as_printed": "B", "symbol": "Vin", "value": 5, "unit": "V"},
          {"document_sha256": "a" * 64, "source_artifact": "a.pdf", "vendor": "a",
           "scope_as_printed": "A", "symbol": "Vout", "value": 3.3, "unit": "V"}],
         {"a.pdf": {"sha256": "a" * 64, "text": full_text},
          "b.pdf": {"sha256": "b" * 64, "text": full_text}}),
    ]
    for name, docs, pdfs in v1_cases:
        out["cases"].append({"kind": "pairs_v1", "name": name,
                             "input": {"docs": docs, "pdfs": pdfs},
                             "expected": _v1_case(build_v1, name, docs, pdfs)})

    v2_claims = {
        "acme-ldo": [
            {"symbol": "Vout", "value": 3.3, "unit": "V", "qualifier": None, "condition": "TA=25C"},
            {"symbol": "Vout", "value": 3.3, "unit": "V", "qualifier": None, "condition": "TA=25C"},
            {"symbol": "Vin", "value": 5, "unit": "V"},
        ],
        "beta-psu": [
            {"symbol": "Iout", "value": 1, "unit": "A"},
            {"symbol": "Iq", "param": None, "value": 10, "unit": "uA"},
        ],
        "gamma-part": [{"symbol": "Rds", "value": 120, "unit": "mohm"}],
    }
    v2_texts = {
        "acme-ldo.pdf": "Electrical\n" + ("x" * 200),
        "beta-psu.pdf": "Electrical\n" + ("y" * 200),
        "gamma-part.pdf": "Electrical\n" + ("z" * 200),
    }
    v2_index = {k: f"pdfs/{k}" for k in v2_texts}
    train, holdout = _v2_case(build_v2, "v2", v2_claims, v2_index, v2_texts, min_claims=3)
    out["cases"].append({"kind": "pairs_v2", "name": "min_claims_and_dedupe",
                         "input": {"stem_claims": v2_claims, "pdf_index": v2_index,
                                   "texts": v2_texts, "min_claims": 3},
                         "expected": {"train": train, "holdout": holdout}})

    # The dedupe key is `(sym.lower(), value, unit.lower())` with `value` held
    # as a native number: Python hashes int 1 and float 1.0 (and 1.5 / 1.50)
    # equal, so the later spelling is dropped and the FIRST-seen spelling wins.
    # Pins the port's `canonical_number` int/float collapse.
    v2_mixed = {
        "acme-ldo": [
            {"symbol": "Rds", "value": 1, "unit": "mohm"},
            {"symbol": "Rds", "value": 1.0, "unit": "mohm"},
            {"symbol": "RDS", "value": 1.0, "unit": "MOHM"},
            {"symbol": "Iq", "value": 1.5, "unit": "uA"},
            {"symbol": "Iq", "value": 1.50, "unit": "uA"},
            {"symbol": "V", "value": 10, "unit": "V"},
            {"symbol": "V", "value": 10.0, "unit": "V"},
        ],
    }
    mixed_texts = {"acme-ldo.pdf": "Electrical\n" + ("x" * 200)}
    mixed_index = {"acme-ldo.pdf": "pdfs/acme-ldo.pdf"}
    train, holdout = _v2_case(build_v2, "v2mix", v2_mixed, mixed_index, mixed_texts, min_claims=1)
    out["cases"].append({"kind": "pairs_v2", "name": "int_float_dedupe_first_seen",
                         "input": {"stem_claims": v2_mixed, "pdf_index": mixed_index,
                                   "texts": mixed_texts, "min_claims": 1},
                         "expected": {"train": train, "holdout": holdout}})

    # The seeded shuffle is the split's source of truth; pin CPython's
    # MT19937 `shuffle` directly so the Rust replay can be byte-checked.
    import random as _random

    for name, n in [("ten", 10), ("hundred", 100), ("one", 1), ("empty", 0)]:
        values = list(range(n))
        _random.Random(1788172800).shuffle(values)
        out["cases"].append(
            {
                "kind": "shuffle",
                "name": name,
                "input": {"seed": 1788172800, "values": list(range(n))},
                "expected": values,
            }
        )

    out["cases"].extend(_gate_cases(lora_gate))
    json.dump(out, sys.stdout, indent=1, sort_keys=True)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())