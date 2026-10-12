#!/usr/bin/env python3
"""Mine every extractable attribute — the registry expansion evidence pack.

Sam's rule (2026-10-12): a knife does not need coverage to matter. A niche
attribute only a few hundred parts carry still carves the exact cohort under
semantic search ("outdoor bluetooth low-power thermostat"). So the registry
grows to EVERY extractable attribute — high-coverage and niche alike.

Three candidate batches:
  A. electrical axes resolvable by the FROZEN unit law (dw-normalize 8 kinds)
  B. attributes resolvable only by ADDITIVE unit kinds (Coulomb/Farad/Second
     — dw-types vocab is "frozen and additive-only", so the path exists;
     the law owner extends, this pack makes the case)
  C. drawer attributes from the phrase layer (applications/occasion,
     catalog_subcategory, has_wireless_ble, n_adc, ...) — categorical/boolean
     knives, no unit law needed

Each candidate carries its evidence: claims count, parts count, sample
quotes, qualifier distribution (-> accepts_classes), proposed op, and the
kind + whether it needs a law extension. This is the "discovered" half,
curated per Sam's grant: anything with real claim counts and a coherent
kind is IN.

Output: yosemiteraid5 api-store-export/registry_candidates.json
Usage: python3 designwins/crates/dw-extract/tools/attribute_mine.py
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

CATALOG = Path("/Users/samkim/mnt/yosemiteraid5-nfs/m5-opencode/scratch/catalog.db")
EXPORT = Path("/Users/samkim/mnt/yosemiteraid5-nfs/m5-opencode/api-store-export")

# frozen unit law kinds (dw-normalize units.rs) -> the axis kind each symbol
# resolves to from its printed unit vocabulary
EXISTING_KINDS = {
    "volt": ["v", "mv", "kv"],
    "ampere": ["a", "ma", "µa", "na", "ka"],
    "ohm": ["ohm", "mohm", "kohm", "Ω", "mΩ", "mohm", "mohm"],
    "celsius": ["°c", "ºc", "℃", "c", "k"],
    "celsiusperwatt": ["°c/w", "c/w", "k/w"],
    "hertz": ["hz", "mhz", "khz"],
    "henry": ["h", "mh", "µh", "nh"],
}
ADDITIVE_KINDS = {
    "coulomb": ["c", "nc", "µc", "mc"],
    "farad": ["f", "pf", "nf", "µf", "mf"],
    "second": ["s", "ns", "µs", "ms", "min"],
    "watt": ["w", "mw", "kw"],
}
TOKEN_KIND_HINT = {
    # measured unit -> kind guess, used when the unit token is unambiguous
    "mΩ": "ohm", "Ω": "ohm", "ohm": "ohm", "mohm": "ohm", "kohm": "ohm",
    "v": "volt", "mv": "volt", "kv": "volt",
    "a": "ampere", "ma": "ampere", "µa": "ampere", "na": "ampere",
    "°c": "celsius", "℃": "celsius", "ºc": "celsius", "c": "celsius",
    "°c/w": "celsiusperwatt", "c/w": "celsiusperwatt",
    "nc": "coulomb", "µc": "coulomb", "c": "coulomb",
    "pf": "farad", "nf": "farad", "µf": "farad",
    "s": "second", "ns": "second", "µs": "second", "ms": "second",
    "w": "watt", "mw": "watt",
}
# drawer attributes from the phrase layer (dw-knives/phrase.rs) — categorical
# or boolean knives; no unit law involved.
DRAWERS = {
    "has_wireless_ble": ("boolean", "applications[]"),
    "has_wireless": ("boolean", "applications[]"),
    "n_adc": ("count", "applications[]"),
    "adc_resolution_bits": ("count", "applications[]"),
    "occasion": ("categorical", "applications[]"),
    "catalog_subcategory": ("categorical", "catalog_subcategory"),
}

NUM = re.compile(r"[-+]?\d+(?:\.\d+)?")


def unit_kind(unit: str) -> str | None:
    tok = (unit or "").strip().lower().replace("ω", "ohm").replace("μ", "µ").replace(" ", "")
    tok = tok.replace("mohm", "mΩ") if tok == "mohm" else tok
    return TOKEN_KIND_HINT.get(tok)


def main() -> int:
    db = sqlite3.connect(f"file:{CATALOG}?mode=ro&immutable=1", uri=True)
    stats = defaultdict(lambda: {"claims": 0, "parts": set(), "units": Counter(),
                                 "qualifiers": Counter(), "samples": []})
    for opn, symbol, qualifier, unit, value_text, provenance in db.execute(
        "SELECT opn, symbol, qualifier, unit, value_text, provenance FROM claims_canonical"
    ):
        s = stats[symbol or "?"]
        s["claims"] += 1
        s["parts"].add(opn)
        s["units"][unit or ""] += 1
        s["qualifiers"][qualifier or "unknown"] += 1
        if len(s["samples"]) < 2:
            try:
                prov = json.loads(provenance or "{}")
            except Exception:
                prov = {}
            s["samples"].append({"value": value_text, "unit": unit,
                                 "quote": (prov.get("quote") or "")[:120], "page": prov.get("page")})

    # kind inference: majority unit -> kind; then batch by law status
    batches = {"A_existing_law": [], "B_additive_kind": [], "C_drawer": []}
    for symbol, s in sorted(stats.items(), key=lambda kv: -kv[1]["claims"]):
        top_unit = s["units"].most_common(1)[0][0] if s["units"] else ""
        kind = unit_kind(top_unit)
        if kind in EXISTING_KINDS or kind in ("celsiusperwatt",):
            batch = "A_existing_law"
        elif kind in ADDITIVE_KINDS:
            batch = "B_additive_kind"
        else:
            batch = "B_additive_kind"  # unknown kind = evidence for extension
        # op proposal: qualifiers tell us min/max/typ columns
        q = s["qualifiers"]
        if q.get("typical", 0) > max(q.get("rated_limit", 0), q.get("absolute_maximum", 0)):
            op = "covers"
        elif "absolute_maximum" in symbol.lower() or "max" in symbol.lower():
            op = "max_rating"
        else:
            op = "min_rating"
        batches[batch].append({
            "symbol": symbol, "claims": s["claims"], "parts": len(s["parts"]),
            "kind": kind or "unknown", "proposed_op": op,
            "accepts_classes": sorted(q),
            "units_seen": dict(s["units"].most_common(4)),
            "samples": s["samples"],
        })
    for name, (typ, drawer) in DRAWERS.items():
        batches["C_drawer"].append({"symbol": name, "claims": None, "parts": None,
                                    "kind": typ, "drawer": drawer, "proposed_op": "equals",
                                    "note": "phrase-layer drawer; source data = family rows / doc text"})

    out = {
        "mandate": "Sam 2026-10-12: registry grows to EVERY extractable attribute; niche knives carve cohorts under semantic search",
        "counts": {k: len(v) for k, v in batches.items()},
        "batch_A_existing_law": sorted(batches["A_existing_law"], key=lambda x: -x["claims"]),
        "batch_B_additive_kind": sorted(batches["B_additive_kind"], key=lambda x: -x["claims"]),
        "batch_C_drawer": batches["C_drawer"],
    }
    (EXPORT / "registry_candidates.json").write_text(json.dumps(out, indent=1, sort_keys=True))
    for k, v in out["counts"].items():
        print(k, v)
    topA = out["batch_A_existing_law"][:6]
    print("batch A top:", [(x["symbol"], x["claims"], x["kind"]) for x in topA])
    topB = out["batch_B_additive_kind"][:6]
    print("batch B top:", [(x["symbol"], x["claims"], x["kind"]) for x in topB])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
