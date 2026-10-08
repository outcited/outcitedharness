#!/usr/bin/env python3
"""Emit the hand-labeled power-topology gold fixture (v1).

Joins hand labels (below) to the sampled parts. Labels state exactly what
the printed front-matter supports: topologies (vocab with a head noun or
topology introducer), integration class (title-noun or FET evidence),
isolation (True only when printed; False when "non-isolated" prints;
None otherwise). Nothing inferred from part-number families or selector
aisles. Compound rule documented in the reader: a "buck-boost" print
claims buck, boost, AND buck-boost (a buck-boost does both).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def L(topologies, integration=None, isolated=None):
    return {"topologies": topologies, "integration_class": integration,
            "isolated": isolated}


LABELS = {
    # --- discretes: no topology/integration/isolation printed ---------------
    "IPF039N08NF2S": L([]), "IPA60R120C7": L([]), "AIMBG75R040M1H": L([]),
    "AIMDQ75R011M2H": L([]), "RQ6A050ZP": L([]), "RW4E045AJ": L([]),
    "R6061YNZ4": L([]),
    # --- LDOs -----------------------------------------------------------------
    "BD25HC0WEFJ": L(["ldo"]), "BD25GC0VEFJ-M": L(["ldo"]),
    "BD70GC0VEFJ-M": L(["ldo"]), "BD18HA3VEFJ-M": L(["ldo"]),
    "BD42540FJ-C": L([]),  # no ldo printed in pages 1-3 (boilerplate cover)
    "BD70GA3WEFJ": L(["ldo"]), "BD15IC0WEFJ": L(["ldo"]),
    "BU11TD3WG": L(["ldo"]),
    "LP3986": L(["ldo"]), "LP3882": L(["ldo"]), "TPS7A20L": L(["ldo"]),
    "TPS715A": L(["ldo"]), "TPS767D3-Q1": L(["ldo"]), "LP5900": L(["ldo"]),
    "LP2985A": L(["ldo"]), "TPS7N48": L(["ldo"]),
    # --- ac-dc ------------------------------------------------------------------
    "UCC24612": L(["flyback", "llc"], "controller"),
    "UCC28881": L(["buck", "buck-boost", "flyback", "boost"], "integrated"),
    "UC3842A": L([], "controller"),
    "UC1856-SP": L([], "controller"),
    "UCC2891": L(["forward", "flyback"], "controller"),
    "UCC2813-4": L([]),  # controller word not printed in p1-3
    # --- battery -----------------------------------------------------------------
    "BQ24753": L(["buck"]),
    "BQ25070": L(["ldo"]),
    "BQ24725A": L(["buck", "boost"], "controller"),
    "BQ24113": L([], "integrated"),
    "BQ24074": L([]),
    "BQ28400": L([]),
    # --- dc-dc -------------------------------------------------------------------
    "TPS562246": L(["buck"], "integrated"),
    "TLVM14406": L(["buck"], "module"),
    "TPS82670": L(["buck"]),
    "TLV61070A": L(["boost"]),  # body "Remote controller" is an application
    "TPS62355": L(["buck"]),
    "LM2831": L(["buck"], "integrated"),  # "internal 130-mΩ PMOS switch"
    "DCP020505": L([], "module", True),   # unregulated isolated module
    "LMZ12003EXT": L(["buck"], "module"),
    "TPS54240-Q1": L(["buck"], "integrated"),
    "LM5163": L(["buck"], "integrated"),
    "TPS566242": L(["buck"], "integrated"),  # "Integrated 27.7-mΩ ... RDSON FET"
    "TPS62870-Q1": L(["buck"], "integrated"),
    "TPS54283": L(["buck"], "integrated"),
    "LM34936-Q1": L(["buck-boost", "buck", "boost"], "controller"),
    "TPS61201": L(["boost"]),
    "PTH04T241W": L([], "module", False),  # NON-ISOLATED printed
    "LM654A5-Q1": L(["buck"]),
    "LM60440-Q1": L(["buck"]),
    "TPS62421": L(["buck"]),
    "LMZ10505": L([], "module"),
    "LM2671": L(["buck"]),
    "TPS564257": L(["buck"], "integrated"),  # "Integrated 55.0-mΩ ... MOSFETs"
    "LM5001-Q1": L(["boost", "flyback", "sepic", "forward"], "integrated"),
    "TPS62182": L(["buck"]),
    "LMQ66420": L(["buck"]),
    "LM3475": L(["buck"], "controller"),
    "TPS6286A08": L(["buck"], "integrated"),
    "TPS546E25W": L(["buck"]),
    "LMR51610": L(["buck"]),
    "LM70840-Q1": L(["buck"]),  # body "buck controller" phrase; title Converters
    "LMR34206-Q1": L(["buck"]),
    "TPS63807": L(["buck-boost", "buck", "boost"]),
    "TPS62112-EP": L(["buck"]),
    "LMR51410": L(["buck"]),
    "TPS62085": L(["buck"]),
    "TPS54561": L(["buck"]),  # "integrated BOOT recharge FET" is not power FET
    "TPIC74100-Q1": L(["buck", "boost"]),
    "TPS53641": L([]),        # NDA cover page, nothing printed
    "TPS61045": L(["boost"]),
    "TPS56628": L(["buck"], "integrated"),
    "TPS65276V": L(["buck"]),
    "TPS63060-EP": L(["buck-boost", "buck", "boost"]),
    "LM3102-Q1": L(["buck"]),
    "LMZ31707": L([], "module"),
    "TPS62401": L(["buck"]),
    "TPS61230A": L(["boost"]),
    # --- gate drivers ------------------------------------------------------------
    "UCC27322": L([]),        # logic-inverting != inverting converter
    "UCC57102Z-Q1": L([]),
    "LM5109B": L(["half-bridge"]),
    "ISO5852S": L([], None, True),
    # --- load switches ------------------------------------------------------------
    "TPS2049": L([]),
    "TPS22953": L([], "integrated"),
    "TPS22920": L([], "integrated"),
    "TPS2067": L([]),         # "incorporates" MOSFET is not integrated/internal
    # --- power management ----------------------------------------------------------
    "TPS65053-Q1": L(["buck", "ldo"]),
    "LM3404": L(["buck"], "integrated"),
    "TPS92511": L(["buck"], "integrated"),
    "LP8865X-Q1": L(["buck-boost", "buck", "boost"], "integrated"),
    "TPS92512HV": L(["buck"], "integrated"),
    "TPS53830": L(["buck"]),  # "DIMM module" is an application context
    # --- protection ------------------------------------------------------------------
    "TPS2331": L([], "controller"),
    "TPS2114A": L([]),
    "TPS2112": L([]),
    # --- power stages ------------------------------------------------------------------
    "BQ500101": L([], "power_stage"),
    "LMG3100R017": L(["buck-boost", "llc", "buck", "boost"]),
    "CSD97376Q4M": L(["buck"], "power_stage"),
    "LMG3411R150": L([]),     # LDO5V is a pin label
    # --- references -------------------------------------------------------------------
    "REF6245": L([]), "TL4051A12": L([]), "REF5025-HT": L([]),
}


def main(sample: Path, out: Path) -> int:
    rows = [json.loads(l) for l in sample.read_text().splitlines() if l.strip()]
    missing = [r["part_number"] for r in rows if r["part_number"] not in LABELS]
    if missing:
        print("UNLABELED:", missing, file=sys.stderr)
        return 1
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w") as h:
        for r in rows:
            r["expected"] = LABELS[r["part_number"]]
            h.write(json.dumps(r, ensure_ascii=False) + "\n")
    n_topo = sum(1 for r in rows if r["expected"]["topologies"])
    n_int = sum(1 for r in rows if r["expected"]["integration_class"])
    print(f"fixture: {len(rows)} rows "
          f"({n_topo} topology-bearing, {n_int} integration-classed) -> {out}")
    return 0


if __name__ == "__main__":
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
        "/tmp/topo-gold/sample.jsonl")
    dst = (Path(sys.argv[2]) if len(sys.argv) > 2
           else Path("tests/fixtures/gold/power_topology_v1.jsonl"))
    raise SystemExit(main(src, dst))
