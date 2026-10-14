#!/usr/bin/env python3
"""Typed axes at scale: fold the 86,736 claims onto the machine's knives.

The elimination machine (dw-knives registry) declares FIVE axes
(VdsRatingV, IdContinuousA, RdsOnOhm, TjMaxC, TstgRangeC) with symbol
lists and ops. A claim is a typed knife only when its symbol binds to an
axis; everything else is typed fact WITHOUT a knife (the mining target).

Symbol binding follows the reference's `norm_symbol` exactly (strip
whitespace/underscores/parens/commas, uppercase) — same normalisation the
registry applies, so the measure cannot drift from the matcher.

Outputs (api-store-export/):
  typed_axes.jsonl   one row per claim that binds to an axis — the feed
  axes_binding.json  the classified fraction + the unbound vocabulary

Usage: python3 designwins/crates/dw-extract/tools/typed_axes.py
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

CATALOG = Path("/Users/samkim/mnt/yosemiteraid5-nfs/m5-opencode/scratch/catalog.db")
OUT = Path("/Users/samkim/mnt/yosemiteraid5-nfs/m5-opencode/api-store-export")

# dw-knives registry.rs AXES — the five knives, in reference declaration order.
AXES = [
    ("vds_rating_v", "min_rating", ["VDSS", "VBRDSS", "VDSSMAX"]),
    ("id_continuous_a", "min_rating", ["IDDC", "ID"]),
    ("rds_on_ohm", "max_rating", ["RDSON", "RDS(ON)MAX"]),
    ("tj_max_c", "min_rating", ["TJ", "TJMAX"]),
    ("tstg_range_c", "covers", ["TSTG"]),
    # additive batch (registry expansion 2026-10-12)
    ("vgs_rating_v", "min_rating", ["VGS", "VGSS", "VGSMAX"]),
    ("vgs_th_v", "max_rating", ["VGSTH", "VGSTHMAX"]),
    ("vsd_v", "max_rating", ["VSD", "VF"]),
    ("idss_a", "max_rating", ["IDSS", "IDSSMAX"]),
    ("igss_a", "max_rating", ["IGSS", "IGSSMAX"]),
    ("is_a", "min_rating", ["IS", "ISMAX"]),
    ("rg_ohm", "max_rating", ["RG"]),
    ("rthja_cperw", "max_rating", ["RTHJA", "RTHJ-A"]),
]


def norm_symbol(symbol: str) -> str:
    """registry.rs `norm_symbol`: strip space/underscore/paren/comma, upper."""
    out = []
    for c in symbol:
        if c.isspace() or c == "_" or c in "(),":
            continue
        out.append(c.upper())
    return "".join(out)


# L1 alias fold — the frozen table shared with dw-knives (same JSONL the Rust
# law reads; adapter layers never re-derive the fold, they consume it).
_ALIASES = {}
for _line in open("/Users/samkim/Harnessv1/designwins/crates/dw-knives/data/symbol_aliases.jsonl"):
    _row = json.loads(_line)
    _ALIASES[_row["alias_norm"]] = norm_symbol(_row["canonical"])


def fold_alias(normed: str) -> str:
    """alias.rs `canonical_key`: the canonical key when folded, else the norm."""
    return _ALIASES.get(normed, normed)


def main() -> int:
    bind = {}
    for axis, op, symbols in AXES:
        for s in symbols:
            bind[norm_symbol(s)] = (axis, op)

    # Fold the located-page chain (claim_pages gold): provenance.page is 0;
    # pages live in the evidence chain — same join as export_api_store r2.
    chain = {}
    for line in open("/Users/samkim/Harnessv1/tests/fixtures/gold/claim_pages_v1.jsonl"):
        r = json.loads(line)
        key = (r["opn"], r["symbol"], r["quote"], r["row_header"], r["column_header"],
               r["qualifier"], r["condition_norm"], r["value_text"], r["doc_sha256"])
        chain[key] = (r.get("page"), r.get("match"))
    db = sqlite3.connect(f"file:{CATALOG}?mode=ro&immutable=1", uri=True)
    typed = []
    unbound = Counter()
    per_axis = Counter()
    bound_parts = defaultdict(set)
    n_claims = 0
    for opn, symbol, qualifier, condition, unit, value_text, provenance, doc_sha in db.execute(
        "SELECT opn, symbol, qualifier, condition_norm, unit, value_text, provenance, doc_sha256"
        " FROM claims_canonical"
    ):
        n_claims += 1
        try:
            prov = json.loads(provenance or "{}")
        except Exception:
            prov = {}
        ns = fold_alias(norm_symbol(symbol or ""))
        hit = bind.get(ns)
        if hit is None:
            unbound[symbol or "?"] += 1
            continue
        axis, op = hit
        per_axis[axis] += 1
        bound_parts[axis].add(opn)
        page, match = chain.get(
            (opn, symbol, prov.get("quote"), prov.get("row_header"), prov.get("column_header"),
             qualifier, condition, value_text, doc_sha),
            (prov.get("page"), "unjoined"),
        )
        typed.append(
            {
                "axis": axis,
                "op": op,
                "opn": opn,
                "doc_sha256": doc_sha,
                "symbol": symbol,
                "symbol_norm": ns,
                "rating_class": qualifier or "unknown",
                "condition_norm": condition or "",
                "value": value_text,
                "unit": unit,
                "quote": prov.get("quote"),
                "page": page,
                "match": match,
                "sha": prov.get("sha"),
            }
        )

    body = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in typed)
    (OUT / "typed_axes.jsonl").write_text(body)
    located = sum(1 for r in typed if r["page"] not in (None, 0))
    allfive = sum(1 for p in set().union(*bound_parts.values()) if all(p in bound_parts[a] for a, _, _ in AXES))
    parts_total = db.execute("SELECT count(*) FROM parts").fetchone()[0]
    report = {
        "claims_total": n_claims,
        "claims_bound_to_axes": len(typed),
        "classified_fraction_pct": round(100 * len(typed) / max(n_claims, 1), 2),
        "bound_with_page": located,
        "per_axis": {
            a: {"claims": per_axis[a], "parts": len(bound_parts[a])} for a, _, _ in AXES
        },
        "parts_total": parts_total,
        "parts_with_all_five_axes": allfive,
        "machine_ready_pct": round(100 * allfive / max(parts_total, 1), 2),
        "unbound_tokens_distinct": len(unbound),
        "unbound_tokens_top20": [{"symbol": s, "claims": c} for s, c in unbound.most_common(20)],
    }
    (OUT / "axes_binding.json").write_text(json.dumps(report, indent=1, sort_keys=True))
    print(json.dumps(report, indent=1))
    print("typed_axes.jsonl:", len(typed), "rows ->", OUT / "typed_axes.jsonl")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
