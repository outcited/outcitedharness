#!/usr/bin/env python3
"""Measure knife coverage over the family-matrix universe.

The measured way (`DECISIONS.md` D4 law 2, `PRD-PHRASE` §6). A binding's
stamp is its **license to eliminate**, and the stamp must be measured, never
declared. This script produces the measurement.

## What it reads

`results/family-matrix-*-20260908/records.jsonl` — the
`family_device_matrix` output. Per family document:

  overview.family / series_tokens / part_numbers_covered
  shared.{attr}   -> attribute common to every variant
  variants.{opn}.{attr} -> per-variant attribute

Each attribute carries `status` (`typed` | `verbatim` | …) and a **receipt**
(`page`, `row_label`, `column_index`, `applies_to`) — so every measured
number here is traceable to a printed table row.

This is `PLAN.md` §6 induction signal #3 doing exactly what the plan says:
*"the attribute rows a comparison table addresses ARE the category's axes."*

## What it emits

`results/knife-coverage/knife-coverage-v1.json`, same shape as
`exports/cr_drops/20260814_yellow_scorecard/2026-08-14.json`, so the two are
comparable and `Stamp::from_coverage` can consume either.

## Why two denominators

| denominator | what it answers |
|---|---|
| **family documents** (681) | how reliably a document exposes this axis |
| **OPNs covered** (7,442) | how much of the part universe the axis can speak to |

A binding is stamped against the **OPN** denominator: a knife that is complete
across 681 documents but covers 7,442 of 18,789 parts is `Blocked` for the
parts nobody measured.

Usage::

    python3 scripts/measure_knife_coverage.py
    python3 scripts/measure_knife_coverage.py --out /tmp/coverage.json
    # then: python3 scripts/freeze_corpus.py tests/fixtures/gold --out ...
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
RESULTS = REPO / "results"
# The stamp oracle lives with the gold, not under results/: results/ is
# run output and is gitignored, while this artifact is what Stamp::from_coverage
# is measured against. It is reproducible from the script and pinned here so a
# stamp cannot change without the diff being visible.
OUT_DEFAULT = REPO / "tests" / "fixtures" / "gold" / "knife_coverage_v1.json"

# The decision universe `yellow_scorecard` measured against. Used only to
# express the OPN denominator as a fraction; the family matrices cover a
# subset of it.
YELLOW_UNIVERSE = 18789

# Map discovered device-table attributes onto the traffic vocabulary from
# PRD-PHRASE Appendix A. This is a **curation** step: the attributes are
# discovered, the mapping to a knife name is a human act and is recorded here
# rather than inferred.
DISCOVERED_TO_KNIFE = {
    "sram_kb": "ram_kb",
    "eeprom_kb": "ram_kb",
    "code_flash_kb": "flash_kb",
    "flash_kb": "flash_kb",
    "freq_mhz": "freq_mhz",
    "usb": "has_usb",
    "can_count": "has_can",
    "can_fd_count": "has_can_fd",
    "adc_count": "adc_dac",
    "dac_count": "adc_dac",
    "dac_channels": "adc_dac",
    "comparator_count": "adc_dac",
    "opamp_count": "adc_dac",
    "temp_range": "temp_range_c",
    "operating_voltage": "operating_voltage_v",
    "gpio_count": "gpio_count",
    "i2c_count": "i2c_count",
    "spi_count": "spi_count",
    "usart_count": "usart_count",
    "uart_count": "usart_count",
    "lpuart_count": "usart_count",
    "i2s_count": "i2s_count",
    "sdio_count": "sdio_count",
    "crypto": "crypto",
    "rtc": "rtc",
    "timer_general_purpose": "timers",
    "timer_advanced": "timers",
    "timer_basic": "timers",
    "timer_watchdog": "timers",
    "timer_systick": "timers",
    "timer_low_power": "timers",
    "timers": "timers",
}

# Attributes the traffic asks for that this corpus does not expose at all.
# Named so the gap is a fact rather than an omission.
WANTED_BUT_ABSENT = {
    "hmi_display": "LCD / TFT / touch / GUI / LVGL",
    "core_family": "handled separately: overview.family + series_tokens",
    "ethernet": "Ethernet / PHY / RGMII",
    "motor": "BLDC / stepper / FOC — timers are a proxy, not the knife",
    "security": "AES / TrustZone / secure boot (holes.json: 0%, do_not_stamp)",
}


def iter_records() -> list[dict]:
    out = []
    for path in sorted(RESULTS.glob("family-matrix-*-20260908/records.jsonl")):
        with path.open() as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return out


def measure() -> dict:
    records = iter_records()
    if not records:
        raise SystemExit("no family-matrix records found under results/")

    n_docs = len(records)
    families: set[str] = set()
    series: set[str] = set()
    opns: set[str] = set()

    # Per-knife: how many family documents expose it, and how many OPNs it
    # speaks to. `typed` is the honest count — `verbatim` is printed but not
    # yet interpreted.
    knife_docs = collections.Counter()
    knife_docs_typed = collections.Counter()
    # OPNs are a SET per knife: the same OPN appears in several family
    # documents (one document per family, and families overlap). Summing
    # double-counts and produced >100% coverage on the first run.
    knife_opn_sets: dict[str, set[str]] = collections.defaultdict(set)
    attr_docs = collections.Counter()
    attr_typed = collections.Counter()

    core_docs = 0
    core_opn_set: set[str] = set()
    core_series_docs = 0

    for rec in records:
        ov = rec.get("overview") or {}
        fam = ov.get("family")
        tokens = ov.get("series_tokens") or []
        covered = ov.get("part_numbers_covered") or []

        if fam:
            families.add(fam)
            core_docs += 1
            core_opn_set |= set(covered)
        for t in tokens:
            series.add(t)
        if tokens:
            core_series_docs += 1
        for p in covered:
            opns.add(p)

        seen_knives: set[str] = set()
        seen_typed: set[str] = set()
        covered_set = set(covered)
        for block in ("shared", "variants"):
            body = rec.get(block) or {}
            if not isinstance(body, dict):
                continue
            for attr, val in body.items():
                attr_docs[attr] += 1
                typed = isinstance(val, dict) and val.get("status") == "typed"
                if typed:
                    attr_typed[attr] += 1
                knife = DISCOVERED_TO_KNIFE.get(attr)
                if knife is None:
                    continue
                seen_knives.add(knife)
                if typed:
                    seen_typed.add(knife)
        for k in seen_knives:
            knife_docs[k] += 1
            knife_opn_sets[k] |= covered_set
        for k in seen_typed:
            knife_docs_typed[k] += 1

    def frac(n: int, d: int) -> float:
        return round(n / d, 4) if d else 0.0

    knife_opns = {k: len(v) for k, v in knife_opn_sets.items()}
    knives = {}
    for k in sorted(set(knife_docs) | set(knife_docs_typed)):
        knives[k] = {
            "docs_classified": knife_docs[k],
            "docs_typed": knife_docs_typed[k],
            "doc_fraction": frac(knife_docs[k], n_docs),
            "opns_classified": knife_opns[k],
            "opn_fraction_of_covered": frac(knife_opns[k], len(opns)),
            "opn_fraction_of_universe": frac(knife_opns[k], YELLOW_UNIVERSE),
        }

    # core_family is the 47% traffic class and gets its own block.
    core_family = {
        "docs_with_family": core_docs,
        "docs_with_series_tokens": core_series_docs,
        "doc_fraction": frac(core_docs, n_docs),
        "distinct_families": len(families),
        "distinct_series_tokens": len(series),
        "opns_with_family": len(core_opn_set),
        "opn_fraction_of_covered": frac(len(core_opn_set), len(opns)),
        "opn_fraction_of_universe": frac(len(core_opn_set), YELLOW_UNIVERSE),
        "note": (
            "family + series_tokens give the containment nesting "
            "(AM13E230x c AM13x c AM1x). PRD-PHRASE 6: this is "
            "KnifeId::Category, not a scalar axis."
        ),
    }

    return {
        "schema": "harness.dw-knife-coverage.v1",
        "generated_by": "scripts/measure_knife_coverage.py",
        "source": "results/family-matrix-*-20260908/records.jsonl",
        "denominators": {
            "family_documents": n_docs,
            "opns_covered": len(opns),
            "yellow_universe": YELLOW_UNIVERSE,
        },
        "core_family": core_family,
        "knives": knives,
        "raw_attributes": {
            "by_doc_count": dict(attr_docs.most_common()),
            "by_typed_count": dict(attr_typed.most_common()),
        },
        "wanted_but_absent": WANTED_BUT_ABSENT,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=pathlib.Path, default=OUT_DEFAULT)
    args = ap.parse_args()

    result = measure()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n")

    d = result["denominators"]
    cf = result["core_family"]
    print(f"wrote {args.out}")
    print(
        f"  denominators: {d['family_documents']} family docs, "
        f"{d['opns_covered']} OPNs, universe {d['yellow_universe']}"
    )
    print()
    print("=== core_family (the 47% traffic class) ===")
    print(f"  docs with family       {cf['docs_with_family']:6d}  {cf['doc_fraction']*100:5.1f}%")
    print(f"  docs with series_tokens{cf['docs_with_series_tokens']:6d}")
    print(f"  distinct families      {cf['distinct_families']:6d}")
    print(f"  distinct series tokens {cf['distinct_series_tokens']:6d}")
    print(
        f"  OPNs with family       {cf['opns_with_family']:6d}  "
        f"{cf['opn_fraction_of_covered']*100:5.1f}% of covered, "
        f"{cf['opn_fraction_of_universe']*100:5.1f}% of universe"
    )
    print()
    print("=== knives (measured, not declared) ===")
    print(f"  {'knife':22s} {'docs':>6s} {'doc%':>6s} {'opns':>7s} {'opn%u':>6s}")
    for k, v in sorted(
        result["knives"].items(), key=lambda kv: -kv[1]["opns_classified"]
    ):
        print(
            f"  {k:22s} {v['docs_classified']:6d} {v['doc_fraction']*100:5.1f}% "
            f"{v['opns_classified']:7d} {v['opn_fraction_of_universe']*100:5.1f}%"
        )
    print()
    print("=== wanted but absent from this corpus ===")
    for k, why in result["wanted_but_absent"].items():
        print(f"  {k:16s} {why}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
