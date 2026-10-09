"""Three end-to-end narrowing journeys (PRD-FACET-02 deliverable 6).

Power runs against the LIVE catalog+index (1,577 real candidates). MCU and
Connectors run against the frozen fixture corpus: the live catalog is
power-only today (0 mcu/connector parts beyond ESP8266EX), and a journey
must demonstrate the mechanics on real data shapes rather than fake
candidates. Each step measures: cohort reduction, candidate vs evidence
counts (never conflated), coverage, unknowns, and latency.

Usage: scripts/facet_journeys.py [--json]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from harness.search import cohort, indexer, units  # noqa: E402

CATALOG = "/Volumes/M5_4TB/extract-results/catalog.db"
SEARCH_DB = "/Volumes/M5_4TB/extract-results/search_index.db"
WAVE = "/Volumes/M5_4TB/extract-results/power-topology-v1.jsonl"
FIXTURE = Path(__file__).resolve().parents[1] / \
    "tests/fixtures/gold/search_fixture_corpus.jsonl"

FIXTURE_AISLES = {
    "STM32F103C4T6": "microcontrollers", "STM32F103C8T6": "microcontrollers",
    "TM4C123GH6PM": "microcontrollers",
    "DEMO-CONN-12P": "connectors",
    "IPF039N08NF2S": "discrete-mosfets", "AIMBG75R040M1H": "discrete-mosfets",
    "IPA60R120C7": "discrete-mosfets", "AIMBF170R1K0M1": "discrete-mosfets",
}


def _fixture_dbs(tmp: Path):
    cat = indexer.catalog_from_corpus_jsonl(FIXTURE)
    search = units.connect(str(tmp / "fixture_search.db"))
    merged = list(indexer.claim_units(cat))
    grouped: dict[tuple[str, str], list[dict]] = {}
    for u in merged:
        grouped.setdefault((u["doc_sha256"], u["extraction_version"]),
                           []).append(u)
    for (doc, version), group in grouped.items():
        units.replace_document(search, group, doc_sha256=doc,
                               extraction_version=version)
    return cat, search


def _step(label, built, constraints, out):
    t0 = time.time()
    f = cohort.facets_for_cohort(built, constraints=constraints)
    dt = (time.time() - t0) * 1000.0
    step = {
        "step": label,
        "constraints": constraints,
        "candidates": f["candidate_count"],
        "evidence_units": f["evidence_units"],
        "evidence_grade_units": f["evidence_grade_units"],
        "discovery_only_units": f["discovery_only_units"],
        "recommended_next": f["recommended_next"],
        "unknown_excluded": {k: len(v) for k, v in
                             f["unknown_excluded"].items()},
        "latency_ms": round(dt, 1),
    }
    top = []
    for facet in f["facets"][:3]:
        vals = [(v["value"], v["candidates"]) for v in facet["values"][:3]]
        top.append({"dimension": facet["dimension"], "top_values": vals})
    step["top_facets"] = top
    out.append(step)
    return f


def journey_power(out):
    cat = sqlite3.connect(f"file:{CATALOG}?mode=ro", uri=True)
    cat.row_factory = sqlite3.Row
    search = sqlite3.connect(f"file:{SEARCH_DB}?mode=ro", uri=True)
    search.row_factory = sqlite3.Row
    amap = indexer.aisle_map_from_wave(WAVE)
    built = cohort.build_cohort(catalog_con=cat, search_con=search,
                                category="power", aisle_map=amap)
    steps = []
    _step("open: all power candidates", built, {}, steps)
    _step("vendor=infineon", built, {"vendor": "infineon"}, steps)
    _step("+ vds 500-1000 V", built,
          {"vendor": "infineon", "vds_rating_v": "500-1000 V"}, steps)
    _step("+ rds_on <50 mOhm", built,
          {"vendor": "infineon", "vds_rating_v": "500-1000 V",
           "rds_on_ohm": "20-50 mOhm"}, steps)
    out["power (live catalog, 1577-part population)"] = steps
    cat.close()
    search.close()


def journey_mcu(out, tmp):
    cat, search = _fixture_dbs(tmp)
    built = cohort.build_cohort(catalog_con=cat, search_con=search,
                                category="mcu", aisle_map=FIXTURE_AISLES)
    steps = []
    _step("open: all mcu candidates", built, {}, steps)
    _step("flash >=128 KB", built, {"flash_kb": "128-512 KB"}, steps)
    _step("+ frequency >=80 MHz", built,
          {"flash_kb": "128-512 KB", "frequency_mhz": "80-150 MHz"}, steps)
    out["mcu (fixture corpus — live catalog is power-only)"] = steps


def journey_connectors(out, tmp):
    cat, search = _fixture_dbs(tmp)
    built = cohort.build_cohort(catalog_con=cat, search_con=search,
                                category="connectors",
                                aisle_map=FIXTURE_AISLES)
    steps = []
    _step("open: all connector candidates", built, {}, steps)
    _step("pitch 1.0-2.0 mm... (fixture: 0.5mm)", built,
          {"pitch_mm": "<1.0 mm"}, steps)
    _step("+ positions 7-24", built,
          {"pitch_mm": "<1.0 mm", "positions": "7-24"}, steps)
    out["connectors (fixture corpus — live catalog has no connector " \
        "parts)"] = steps


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    import tempfile
    out: dict = {"schema": "harness.search-journeys.v1", "journeys": {}}
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        journey_power(out["journeys"])
        journey_mcu(out["journeys"], tmp)
        journey_connectors(out["journeys"], tmp)
    if args.json:
        print(json.dumps(out, indent=2))
        return 0
    for name, steps in out["journeys"].items():
        print(f"\n=== {name} ===")
        for s in steps:
            unk = s["unknown_excluded"]
            print(f"  {s['step']:<38} candidates={s['candidates']:<6}"
                  f" ev={s['evidence_units']:<7}"
                  f" graded={s['evidence_grade_units']:<6}"
                  f" next={s['recommended_next']}"
                  f" unknown_excluded={unk or '-'}"
                  f" {s['latency_ms']}ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
