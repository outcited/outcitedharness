#!/usr/bin/env python3
"""Emit the hand-labeled rating-class gold fixture (v1).

63 rows sampled from the ifx2/rohm-si power grids (stratified by
table_kind, guaranteed symbol coverage for pulse/surge/thermal
vocabulary). Labels follow the projection contract in
harness/electronics/rating_class.py; the label wins, the projection is
fixed to it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

LABELS = {
    "ID,pulse": "transient",        # absmax table, pulsed symbol + parameter
    "Vplateau": "unknown",          # typ column not resolved by reader
    "EAS": "transient",             # avalanche, inside absmax table
    "IGSS": "rated_limit",          # EC max limit
    "Efr": "unknown",
    "_diagram_digits": "unknown",
    "RG": "unknown",
    "VGS|absmax": "absolute_maximum",
    "Qgs": "unknown",
    "TJ|prose": "recommended",      # operating temperature range
    "Qg|min": "rated_limit",
    "tr": "unknown",
    "Qoss": "typical",
    "_capacitance_glued": "unknown",
    "TJ|prose2": "recommended",
    "Qfr": "unknown",
    "Crss": "unknown",
    "Tsold": "unknown",
    "td(on)": "unknown",
    "_delay_glued": "unknown",
    "Qg|min2": "rated_limit",
    "VGS(th)|null": "unknown",
    "RthJC|chmax": "rated_limit",   # EC-table max thermal metric
    "dVBRDSS": "unknown",
    "Eoss,typ": "typical",
    "VGS(th)|min": "rated_limit",
    "trr": "rated_limit",
    "_garbled_typ": "typical",      # qq typical carries even on garbled symbol
    "QgQgsQgd|max": "rated_limit",
    "TJ|prose3": "recommended",
    "RDS(on)|summary": "typical",
    "VGSS|30": "absolute_maximum",
    "RthJC|thermal_max": "rated_limit",
    "Qoss|2": "typical",
    "ID@composite": "absolute_maximum",  # glued IDM symbol, parameter Continuous
    "VDSS|750": "absolute_maximum",
    "EAS|2": "transient",
    "ID|prose": "rated_limit",      # front-page headline rating, not operating
    "E_AS_glued": "transient",
    "Tj|absmax": "absolute_maximum",
    "Ptot": "absolute_maximum",
    "TstgTj": "absolute_maximum",
    "VDSS|100": "absolute_maximum",
    "IDDC": "absolute_maximum",     # continuous DC drain in Maximum ratings
    "RDS(on)|summary2": "typical",
    "dvdt": "absolute_maximum",
    "Tstg": "absolute_maximum",
    "VGSS|20": "absolute_maximum",
    "RthJC|thermal_null": "unknown",
    "ID|summary": "typical",
    "IDP": "transient",
    "IAS": "transient",
    "VDSS|600": "absolute_maximum",
    "QG|summary": "typical",
    "QG(0V..10V)": "typical",
    "ID@TA": "absolute_maximum",
    "RthJA|max": "rated_limit",
    "RJCRJARJA": "rated_limit",
    "PD": "absolute_maximum",
    "VGS(on)|thermal": "unknown",   # non-thermal symbol leaked into thermal table
    "VGS|range": "absolute_maximum",
    "ISM": "surge",                 # tp-limited peak reverse current
}


def key(row: dict) -> str | None:
    sym = row.get("symbol") or ""
    par = row.get("parameter") or ""
    qq = row.get("quantity_qualifier")
    if sym == "ID,pulse":
        return "ID,pulse"
    if sym == "Vplateau":
        return "Vplateau"
    if sym == "EAS":
        return "EAS"
    if sym == "IGSS":
        return "IGSS"
    if sym == "Efr":
        return "Efr"
    if not sym and par[:2].isdigit():
        return "_diagram_digits"
    if sym == "RG" and "Gate resistance" in par:
        return "RG"
    if sym == "VGS" and qq == "absolute_maximum":
        return "VGS|absmax"
    if sym == "Qgs":
        return "Qgs"
    if sym == "TJ" and row.get("table_kind") == "prose" and row.get("value") == [-55.0, 150.0]:
        return "TJ|prose"
    if sym == "" and par == "Qg" and qq == "minimum" and row.get("value") == 31.0:
        return "Qg|min"
    if sym == "tr":
        return "tr"
    if sym == "" and par == "Qoss":
        return "Qoss"
    if sym.startswith("Inputcapacitance"):
        return "_capacitance_glued"
    if sym == "TJ" and row.get("value") == [-55.0, 175.0] and "50" in row.get("verbatim", ""):
        return "TJ|prose2"
    if sym == "" and par == "Qfr":
        return "Qfr"
    if sym == "Crss":
        return "Crss"
    if sym == "Tsold":
        return "Tsold"
    if sym == "td(on)":
        return "td(on)"
    if sym == "td(on)trtd(off)tf":
        return "_delay_glued"
    if sym == "Qg" and qq == "minimum":
        return "Qg|min2"
    if sym == "VGS(th)" and qq is None:
        return "VGS(th)|null"
    if sym == "RthJC" and row.get("table_kind") == "characteristics":
        return "RthJC|chmax"
    if sym.startswith("ΔV"):
        return "dVBRDSS"
    if sym == "Eoss,typ":
        return "Eoss,typ"
    if sym == "VGS(th)" and qq == "minimum":
        return "VGS(th)|min"
    if sym == "trr":
        return "trr"
    if sym.startswith("tÁ"):
        return "_garbled_typ"
    if sym == "QgQgsQgd":
        return "QgQgsQgd|max"
    if sym == "TJ" and row.get("table_kind") == "prose":
        return "TJ|prose3"
    if sym.startswith("RDS(ON") and row.get("table_kind") == "summary" and row.get("value") == 7.3:
        return "RDS(on)|summary"
    if sym == "VGSS" and row.get("value") == 30.0:
        return "VGSS|30"
    if sym.startswith("RthJCΨ"):
        return "RthJC|thermal_max"
    if sym == "" and par == "Qoss" and row.get("value") == 68.0:
        return "Qoss|2"
    if sym.startswith("ID@TC"):
        return "ID@composite"
    if sym == "VDSS" and row.get("value") == 750.0:
        return "VDSS|750"
    if sym == "EAS" and row.get("value") == 0.32:
        return "EAS|2"
    if sym == "ID" and row.get("table_kind") == "prose":
        return "ID|prose"
    if sym == "E*3 AS" or (not sym and "E*3 AS" in par):
        return "E_AS_glued"
    if sym == "Tj" and row.get("table_kind") == "absolute_maximum":
        return "Tj|absmax"
    if sym == "Ptot":
        return "Ptot"
    if sym == "TstgTj‑" or sym == "TstgTj-":
        return "TstgTj"
    if sym == "VDSS" and row.get("value") == 100.0:
        return "VDSS|100"
    if sym == "IDDC":
        return "IDDC"
    if sym.startswith("RDS(ON") and row.get("value") == 60.0:
        return "RDS(on)|summary2"
    if sym == "dvdt":
        return "dvdt"
    if sym == "Tstg":
        return "Tstg"
    if sym == "VGSS" and row.get("value") == 20.0:
        return "VGSS|20"
    if sym == "RthJC" and row.get("table_kind") == "thermal" and qq is None:
        return "RthJC|thermal_null"
    if sym == "ID" and row.get("table_kind") == "summary":
        return "ID|summary"
    if sym == "IDP":
        return "IDP"
    if sym == "IAS":
        return "IAS"
    if sym == "VDSS" and row.get("value") == 600.0:
        return "VDSS|600"
    if sym == "" and par == "QG" and row.get("value") == 62.0:
        return "QG|summary"
    if par == "QG(0V...10V)":
        return "QG(0V..10V)"
    if sym.startswith("ID@TA"):
        return "ID@TA"
    if sym == "RthJA" and qq == "maximum":
        return "RthJA|max"
    if "Junction-to-Ambient" in par:
        return "RJCRJARJA"
    if sym == "PD":
        return "PD"
    if sym == "VGS(on)":
        return "VGS(on)|thermal"
    if sym == "VGS" and isinstance(row.get("value"), list):
        return "VGS|range"
    if sym == "ISM":
        return "ISM"
    return None


def main(sample: Path, out: Path) -> int:
    rows = [json.loads(l) for l in sample.read_text().splitlines() if l.strip()]
    labeled = []
    misses = []
    for r in rows:
        k = key(r)
        if k is None or k not in LABELS:
            misses.append(r)
            continue
        r["expected_rating_class"] = LABELS[k]
        labeled.append(r)
    if misses:
        print(f"UNKEYED ({len(misses)}):", file=sys.stderr)
        for r in misses:
            print(f"  {r.get('symbol')!r} | {str(r.get('parameter'))[:50]!r}", file=sys.stderr)
        return 1
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w") as h:
        for r in labeled:
            h.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"fixture: {len(labeled)} rows -> {out}")
    return 0


if __name__ == "__main__":
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
        "/tmp/rating-gold/sample.jsonl")
    dst = (Path(sys.argv[2]) if len(sys.argv) > 2
           else Path("tests/fixtures/gold/rating_class_v1.jsonl"))
    raise SystemExit(main(src, dst))
