#!/usr/bin/env python3
"""Normalization layer -> compare substrate. The comparability law.

Compare (front end) compares NOTHING until two parts' claims are in the
same numeric space. This tool is the truth-side law that makes it so:

  claim (value_text, unit, rating_class) + axis op
    -> bound {value_si, unit_si, op} + honest unnormalizable class

Semantics come from the gold-tested scorer (`score_key_features_gold.to_base`,
tests/test_score_key_features_gold.py) — the SI table, the mOhm-mW glyph rule
(BOUND: RDS document rows only; elsewhere mW is watts and stays watts), and
the °C/ºC/℃ family -> "c". Anything else is counted UNNORMALIZABLE and left
raw — never silently coerced (the type-gate lesson).

Compare semantics shipped with the matrix (m4 + API):
  min_rating axis  : part satisfies requirement R iff part_value >= R
  max_rating axis  : part satisfies R iff part_value <= R
  covers axis      : R in [lo, hi] of the part's declared range
  rating_class gates elimination (AbsMax never satisfies an operating atom;
  Unknown never eliminates) — dw-decide owns the verdicts, this owns the units.

Self-test runs first, every invocation, and exits non-zero on mismatch — the
negative control is part of the tool, not a separate ceremony.

Outputs (api-store-export/):
  compare_ready.jsonl  one row per normalizable typed knife
  compare_matrix.json  parts x axes normalized cells + semantics

Usage: python3 designwins/crates/dw-extract/tools/normalize_compare.py
"""

from __future__ import annotations

import json
import re
from pathlib import Path

EXPORT = Path("/Users/samkim/mnt/yosemiteraid5-nfs/m5-opencode/api-store-export")

SI_PREFIX = {"p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6, "μ": 1e-6,
             "m": 1e-3, "": 1.0, "k": 1e3, "M": 1e6, "G": 1e9}
UNIT_BASE = {"ohm": "ohm", "ω": "ohm", "Ω": "ohm", "v": "v", "a": "a", "c": "c",
             "ºc": "c", "°c": "c", "℃": "c", "hz": "hz", "w": "w", "f": "f",
             "h": "h", "s": "s", "j": "j", "%": "%"}
# The glyph rule (CR adjudicator-fixes-20260911), BOUNDED to the RDS axis:
# inside RDS(on) rows the Omega->W confusion means mW reads mOhm. Nowhere else.
GLYPH_RULE_AXES = {"rds_on_ohm"}
NUM = re.compile(r"[-+]?\d+(?:\.\d+)?")


def to_base(value: float, unit: str | None) -> tuple[float, str] | None:
    """score_key_features_gold.to_base, plus the glyph table families."""
    u = (unit or "").strip()
    u = u.replace("Ohms", "Ohm").replace("ohms", "ohm").replace("mOhm", "mΩ").replace("mohm", "mΩ")
    if not u:
        return (value, "")
    if u.lower() in UNIT_BASE:
        return (value, UNIT_BASE[u.lower()])
    if u[0] in SI_PREFIX and u[1:].lower() in UNIT_BASE:
        base = UNIT_BASE[u[1:].lower()]
        return (value * SI_PREFIX[u[0]], base)
    return None


def normalize_bound(value_si: float, unit_si: str, axis: str) -> str:
    """Which comparison the bound participates in (registry op)."""
    return {"vds_rating_v": "min_rating", "id_continuous_a": "min_rating",
            "rds_on_ohm": "max_rating", "tj_max_c": "min_rating",
            "tstg_range_c": "covers"}.get(axis, "unknown")


def normalize_row(row: dict) -> dict | None:
    text = str(row.get("value") or "")
    m = NUM.search(text)
    if not m:
        return None
    val = float(m.group(0))
    unit = row.get("unit") or ""
    axis = row.get("axis") or ""
    # Glyph rule, bounded: RDS rows only.
    if axis in GLYPH_RULE_AXES and unit.strip() in ("mW", "W"):
        unit = "mΩ" if unit.strip() == "mW" else "Ω"
    nb = to_base(val, unit)
    if nb is None:
        return None
    value_si, unit_si = nb
    return {**row, "value_si": value_si, "unit_si": unit_si,
            "op": normalize_bound(value_si, unit_si, axis)}


def self_test() -> None:
    """The negative control. Every invocation. Exit non-zero on failure."""
    cases = [
        ((6.8, "mΩ"), (0.0068, "ohm")),
        ((3.5, "Ω"), (3.5, "ohm")),
        ((40, "A"), (40.0, "a")),
        ((3.5, "mOhm"), (0.0035, "ohm")),
    ]
    for (v, u), want in cases:
        got = to_base(v, u)
        assert got is not None and abs(got[0] - want[0]) < 1e-12 and got[1] == want[1], f"to_base({v},{u}) = {got}, want {want}"
    # degree family -> c
    for u in ("°C", "ºC", "℃", "C"):
        got = to_base(-55.0, u)
        assert got == (-55.0, "c"), f"degree family failed for {u!r}: {got}"
    # THE GLYPH TRAP, bounded: RDS axis turns mW into mOhm...
    rds = normalize_row({"axis": "rds_on_ohm", "value": "1.6", "unit": "mW", "rating_class": "rated_limit"})
    assert rds and abs(rds["value_si"] - 0.0016) < 1e-12 and rds["unit_si"] == "ohm", f"glyph rule failed: {rds}"
    # ...and ONLY there: on the id axis, mW is watts.
    idr = normalize_row({"axis": "id_continuous_a", "value": "1.6", "unit": "mW", "rating_class": "rated_limit"})
    # NB: 1.6 mW is genuinely 0.0016 W — the prefix still applies; the rule
    # only decides mW-reads-mOhm. (This assertion was itself wrong first;
    # the negative control caught the test, which is the control working.)
    assert idr and idr["unit_si"] == "w" and abs(idr["value_si"] - 0.0016) < 1e-12, f"glyph rule leaked outside RDS: {idr}"
    # unnormalizable stays unnormalizable
    assert to_base(1.0, "Â") is None and to_base(1.0, "m") is None


def main() -> int:
    self_test()
    rows = [json.loads(l) for l in (EXPORT / "typed_axes.jsonl").open()]
    ready, unnormalizable = [], []
    matrix: dict[str, dict[str, dict]] = {}
    for r in rows:
        n = normalize_row(r)
        if n is None:
            unnormalizable.append({"axis": r.get("axis"), "opn": r.get("opn"),
                                   "value": r.get("value"), "unit": r.get("unit")})
            continue
        ready.append(n)
        cell = {"value_si": n["value_si"], "unit_si": n["unit_si"], "op": n["op"],
                "class": n.get("rating_class"), "quote": n.get("quote"),
                "page": n.get("page"), "sha": n.get("sha")}
        matrix.setdefault(n["opn"], {})[n["axis"]] = cell
    body = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in ready)
    (EXPORT / "compare_ready.jsonl").write_text(body)
    axes = sorted({r["axis"] for r in ready})
    doc = {
        "semantics": {
            "min_rating": "part satisfies requirement R iff value_si >= R",
            "max_rating": "part satisfies R iff value_si <= R",
            "covers": "R within the part's declared range",
            "elimination": "dw-decide: AbsMax never satisfies an operating atom; Unknown never eliminates; PositiveOnly never eliminates",
        },
        "axes": axes,
        "parts": len(matrix),
        "cells": sum(len(v) for v in matrix.values()),
        "normalized": len(ready),
        "unnormalizable": len(unnormalizable),
        "matrix": matrix,
    }
    (EXPORT / "compare_matrix.json").write_text(json.dumps(doc, sort_keys=True))
    print(f"normalized: {len(ready)} / {len(rows)} typed claims")
    print(f"unnormalizable (honest): {len(unnormalizable)}")
    print(f"compare matrix: {len(matrix)} parts x {len(axes)} axes, {doc['cells']} cells")
    print("axes:", axes)
    print("self-test: PASS (negative controls ran)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
