#!/usr/bin/env python3
"""Compare substrate — an ADAPTER of the frozen normalization law.

The law already existed before this tool (PRD-NORMALIZE, dw-normalize):
`units::unit_token` + the eight-kind multiplier tables (frozen, fail-closed,
`Scale::Unknown` on anything not in a table), `dw_types::Quantity`
(canonical Unit enum; comparisons only via approx_eq with stored tolerance),
`rating::compare_with_margin` (margin inflates the REQUIREMENT; NearMiss is
a separate cohort; claim None -> Unknown never eliminates).

This tool's ONLY job: carry typed claims into that law's numeric space for
the export seam, so compare (m4's view, dw-decide's verdicts) has cells that
are legally comparable. It introduces NO new normalization rules. Two
consequences of respecting the law:

1. **Fail closed.** A printed unit not scaling to the axis's kind is
   UNNORMALIZABLE and stays raw — never coerced (the "no invented units" law).
2. **The mW/mOhm glyph rule (CR adjudicator-fixes-20260911) is NOT a unit
   rule.** It is a bounded scoring-layer correction for RDS document rows.
   Rows it flags get `glyph_suspect: true` and nothing else — the numbers
   stay exactly as the frozen law parsed them. dw-knives/dw-decide may apply
   the correction in the RDS axis context; the substrate will not invent it.

Self-test runs first every invocation (exit non-zero on failure), including
the vectors the gold fixtures encode (condition_model_v1 reject:"dash",
the degree family, the mV/V scale) and the negative controls (mW on an
Ohm-kind axis must FAIL closed; a unit-less token must fail).

Outputs (api-store-export/):
  compare_ready.jsonl   normalizable rows: value + canonical unit + scale
  compare_matrix.json   parts x axes cells + compare semantics
  unnormalizable.jsonl  honest rejects (raw value/unit kept)

Usage: python3 designwins/crates/dw-extract/tools/normalize_compare.py
"""

from __future__ import annotations

import json
import re
from pathlib import Path

EXPORT = Path("/Users/samkim/mnt/yosemiteraid5-nfs/m5-opencode/api-store-export")

# ---- the frozen law, ported exactly (dw-normalize/src/units.rs) ----------

def unit_token(text: str) -> str:
    collapsed = "".join(c for c in (text or "") if not c.isspace())
    return collapsed.replace("μ", "µ").replace("Ω", "ohm").lower()


KIND_SCALE = {
    "volt": {"mv": 1e-3, "v": 1.0, "kv": 1e3},
    "ampere": {"na": 1e-9, "µa": 1e-6, "ma": 1e-3, "ka": 1e3, "a": 1.0},
    "hertz": {"hz": 1.0, "khz": 1e3, "mhz": 1e6, "ghz": 1e9},
    "ohm": {"mohm": 1e-3, "ohm": 1.0, "kohm": 1e3},
    "henry": {"nh": 1e-9, "µh": 1e-6, "mh": 1e-3, "h": 1.0},
    "celsius": {"c": 1.0, "°c": 1.0, "ºc": 1.0, "℃": 1.0, "k": 1.0},
    "celsiusperwatt": {"c/w": 1.0, "°c/w": 1.0, "k/w": 1.0, "c/watt": 1.0},
    # D10 additive kinds (DECISIONS.md, 2026-10-12) — frozen multipliers,
    # ported exactly from units.rs. Cross-kind tokens still fail closed.
    "coulomb": {"nc": 1e-9, "µc": 1e-6, "uc": 1e-6, "mc": 1e-3, "c": 1.0},
    "farad": {"pf": 1e-12, "nf": 1e-9, "µf": 1e-6, "uf": 1e-6, "mf": 1e-3, "f": 1.0},
    "second": {"ns": 1e-9, "µs": 1e-6, "us": 1e-6, "ms": 1e-3, "s": 1.0},
    "watt": {"mw": 1e-3, "w": 1.0, "kw": 1e3},
    "joule": {"mj": 1e-3, "j": 1.0},
}
KIND_UNIT = {"volt": "Volt", "ampere": "Ampere", "hertz": "Hertz", "ohm": "Ohm",
             "henry": "Henry", "celsius": "Celsius", "celsiusperwatt": "Celsius",
             "coulomb": "Coulomb", "farad": "Farad", "second": "Second",
             "watt": "Watt", "joule": "Joule"}
# The axis registry decides the KIND (what the number IS); the printed token
# must scale to it or the row fails closed.
AXIS_KIND = {
    "vds_rating_v": "volt",
    "id_continuous_a": "ampere",
    "rds_on_ohm": "ohm",
    "tj_max_c": "celsius",
    "tstg_range_c": "celsius",
    # E3: the eight additive knives (registry.rs, 155ae227) — the Python
    # adapter grows cells with the Rust registry, never beside it.
    "vgs_rating_v": "volt",
    "vgs_th_v": "volt",
    "vsd_v": "volt",
    "idss_a": "ampere",
    "igss_a": "ampere",
    "is_a": "ampere",
    "rg_ohm": "ohm",
    "rthja_c_per_w": "celsiusperwatt",
    "rthja_cperw": "celsiusperwatt",  # typed_axes spelling of the same axis
}
AXIS_OP = {"vds_rating_v": "min_rating", "id_continuous_a": "min_rating",
           "rds_on_ohm": "max_rating", "tj_max_c": "min_rating",
           "tstg_range_c": "covers",
           "vgs_rating_v": "min_rating", "vgs_th_v": "max_rating",
           "vsd_v": "max_rating", "idss_a": "max_rating",
           "igss_a": "max_rating", "is_a": "min_rating",
           "rg_ohm": "max_rating", "rthja_c_per_w": "max_rating",
           "rthja_cperw": "max_rating"}
NUM = re.compile(r"[-+]?\d+(?:\.\d+)?")


def scale_to_kind(kind: str, token: str) -> float | None:
    """units::lookup — Unknown (None here) fails closed, never guesses."""
    tok = unit_token(token)
    if kind == "amperepersecond":
        rest = tok[2:] if tok.startswith("a/") else None
        return {"s": 1.0, "ms": 1e3, "µs": 1e6, "ns": 1e9}.get(rest or "")
    table = KIND_SCALE.get(kind, {})
    if tok in table:
        return table[tok]
    # degree-family tolerance in the frozen pattern set (°\s*C forms collapse
    # in unit_token via whitespace strip: "° C" -> "°c").
    return None


def normalize_row(row: dict) -> tuple[dict | None, dict]:
    """-> (normalized row | None, reject record)."""
    axis = row.get("axis") or ""
    kind = AXIS_KIND.get(axis)
    text = str(row.get("value") or "")
    m = NUM.search(text)
    if kind is None or not m:
        return None, {"axis": axis, "opn": row.get("opn"), "value": row.get("value"),
                      "unit": row.get("unit"), "why": "no axis kind or no number"}
    factor = scale_to_kind(kind, row.get("unit") or "")
    if factor is None:
        rec = {"axis": axis, "opn": row.get("opn"), "value": row.get("value"),
               "unit": row.get("unit"), "why": f"Scale::Unknown for kind {kind}"}
        # The bounded glyph annotation (scoring layer, NOT applied here).
        if axis == "rds_on_ohm" and unit_token(row.get("unit") or "") in ("mw", "w"):
            rec["glyph_suspect"] = True
        return None, rec
    value_si = float(m.group(0)) * factor
    out = {**row, "value_si": value_si, "unit_si": KIND_UNIT[kind],
           "op": AXIS_OP[axis], "kind": kind}
    return out, {}


def self_test() -> None:
    """Negative controls, run every invocation. The law tests the adapter."""
    assert unit_token("Ω") == "ohm" and unit_token("μA") == "µa" and unit_token("° C") == "°c"
    assert scale_to_kind("volt", "mV") == 1e-3          # 500 mV == 0.5 V law
    assert scale_to_kind("ohm", "mΩ") == 1e-3           # 6.8 mOhm == 0.0068 ohm
    assert scale_to_kind("ohm", "mW") is None           # FAIL CLOSED: no Watt table
    assert scale_to_kind("volt", "Â") is None           # mojibake never guesses
    assert scale_to_kind("celsius", "℃") == 1.0         # degree family
    ok, rej = normalize_row({"axis": "rds_on_ohm", "value": "1.6", "unit": "mW"})
    assert ok is None and rej.get("glyph_suspect") is True, "glyph rule must FLAG, not coerce"
    ok2, _ = normalize_row({"axis": "rds_on_ohm", "value": "1.6", "unit": "mΩ"})
    assert ok2 and abs(ok2["value_si"] - 0.0016) < 1e-12 and ok2["unit_si"] == "Ohm"


def main() -> int:
    self_test()
    rows = [json.loads(l) for l in (EXPORT / "typed_axes.jsonl").open()]
    ready, rejects = [], []
    matrix: dict[str, dict[str, dict]] = {}
    for r in rows:
        n, rej = normalize_row(r)
        if n is None:
            rejects.append(rej)
            continue
        ready.append(n)
        matrix.setdefault(n["opn"], {})[n["axis"]] = {
            "value": n["value_si"], "unit": n["unit_si"], "op": n["op"],
            "class": n.get("rating_class"), "quote": n.get("quote"),
            "page": n.get("page"), "sha": n.get("sha"),
        }
    (EXPORT / "compare_ready.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in ready))
    (EXPORT / "unnormalizable.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rejects))
    axes = sorted({r["axis"] for r in ready})
    doc = {
        "law": "PRD-NORMALIZE / dw-normalize (frozen); this file is an adapter, not a rule",
        "compare_semantics": {
            "pass": "compare_with_margin: margin inflates the REQUIREMENT; satisfies iff claim >= inflated (min_rating) / <= inflated (max_rating)",
            "near_miss": "within near_miss_pct of the raw requirement — a separate cohort, never a silent Fail",
            "unknown": "claim absent -> Unknown, never eliminates (C9.1)",
            "equality": "dw_types::Quantity::approx_eq only; float == is denied workspace-wide",
        },
        "parts": len(matrix),
        "axes": axes,
        "cells": sum(len(v) for v in matrix.values()),
        "normalized": len(ready),
        "unnormalizable": len(rejects),
        "matrix": matrix,
    }
    (EXPORT / "compare_matrix.json").write_text(json.dumps(doc, sort_keys=True))
    glyph = sum(1 for r in rejects if r.get("glyph_suspect"))
    print(f"normalized: {len(ready)} / {len(rows)}  (frozen law, fail-closed)")
    print(f"unnormalizable: {len(rejects)}  (of which glyph-suspect RDS rows: {glyph} — flagged, NOT coerced)")
    print(f"matrix: {len(matrix)} parts x {len(axes)} axes, {doc['cells']} cells")
    print("self-test: PASS (frozen-law vectors + negative controls)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
