#!/usr/bin/env python3
"""Classify the catch-up's no_claims population by ACTUAL CAUSE using
deterministic source inspection only (no model calls).

Classes (per directive):
  missing_substrate       substrate absent/unreadable
  no_table_signals        source contains no parametric evidence
                          (correct empty — NOT eligible)
  wrong_doc_class         document class is not a datasheet
                          (app note / errata / catalog / unknown class)
  truncation_boundary     table signals exist only beyond the 28k char cap
  raster_or_partial_text  text layer below the workable floor
  model_empty_despite_signals   table signals + text present, model
                          returned nothing -> ELIGIBLE for map-context route
  other                   anything else, named

Eligible for map-context = model_empty_despite_signals (+ truncation as
secondary). Preserves exact stems + original failure reasons.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = Path("/Users/samkim/Harnessv1")  # live checkout: Python baseline
sys.path.insert(0, str(ROOT))

TABLE_SIGNALS = [
    re.compile(r"electrical characteristics", re.I),
    re.compile(r"absolute maximum", re.I),
    re.compile(r"recommended operating", re.I),
    re.compile(r"min\W{0,3}typ\W{0,3}max", re.I),
    re.compile(r"parameter.{0,40}conditions.{0,40}(min|typ|max|unit)", re.I),
    re.compile(r"symbol.{0,30}(value|unit|min|max)", re.I),
]
UNITS = re.compile(r"\b(?:[mµunp]?[AVWΩohm]|°C|kHz|MHz|ns|µs|mA|mV)\b")
NO_DOC_CLASS = re.compile(
    r"errata|revision history|product catalog|selection guide|application note"
    r"|user guide|quick start|data brief", re.I)
SIGNAL_AFTER_CAP = None  # computed per doc


def classify(stem: str, sub_path: str | None, baseline_claims: int | None):
    rec = {"stem": stem, "original_failure": "no_claims (catch-up era)",
           "python_baseline_claims": baseline_claims}
    if not sub_path:
        rec["class"] = "missing_substrate"
        return rec
    try:
        sub = json.loads(Path(sub_path).read_text())
    except Exception as exc:
        rec["class"] = "missing_substrate"
        rec["detail"] = f"unreadable: {type(exc).__name__}"
        return rec
    pages = [p.get("text") or "" for p in sub.get("pages", [])]
    full = "\n".join(pages)
    capped = full[:28000]
    rec["text_chars"] = len(full)
    rec["pages"] = len(pages)
    if len(full) < 400:
        rec["class"] = "raster_or_partial_text"
        return rec
    head = full[:4000]
    if NO_DOC_CLASS.search(head) and not TABLE_SIGNALS[0].search(full):
        rec["class"] = "wrong_doc_class"
        rec["detail"] = NO_DOC_CLASS.search(head).group(0)
        return rec
    sig_cap = sum(1 for rx in TABLE_SIGNALS if rx.search(capped))
    sig_full = sum(1 for rx in TABLE_SIGNALS if rx.search(full))
    units_cap = len(UNITS.findall(capped))
    rec["signals_in_cap"] = sig_cap
    rec["signals_full"] = sig_full
    rec["unit_tokens_cap"] = units_cap
    if sig_full >= 2 and sig_cap == 0:
        rec["class"] = "truncation_boundary"
        return rec
    if sig_cap >= 2 and units_cap >= 10:
        rec["class"] = "model_empty_despite_signals"
        rec["eligible"] = ("confirmed_recoverable" if (baseline_claims or 0) > 0
                           else "primary")
        return rec
    if sig_cap >= 1 and units_cap >= 5:
        rec["class"] = "model_empty_despite_signals"
        rec["eligible"] = ("confirmed_recoverable" if (baseline_claims or 0) > 0
                           else "secondary")
        return rec
    if sig_cap == 0:
        rec["class"] = "no_table_signals"
        return rec
    rec["class"] = "other"
    return rec


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--outputs-root", type=Path,
                    default=ROOT / "scratch/m5-soak/fixup-out")
    ap.add_argument("--ra03-root", type=Path,
                    default=ROOT / "scratch/m5-soak/ra03-out")
    ap.add_argument("--substrates", type=Path,
                    default=ROOT / "scratch/m5-soak/fixup-substrates.json")
    ap.add_argument("--redo-cohort", type=Path,
                    default=ROOT / "scratch/m5-soak/fixup-cohort.json")
    ap.add_argument("--out", type=Path,
                    default=ROOT / "scratch/m5-soak/empty-classification.jsonl")
    args = ap.parse_args()

    subs = json.loads(args.substrates.read_text())
    ra_subs = json.loads((ROOT / "scratch/m5-soak/ra03-substrates.json").read_text())
    for k, v in ra_subs.items():
        subs.setdefault(k, v)  # retained-era stems live in the ra03 map
    redo = {Path(p).stem for p in json.loads(args.redo_cohort.read_text())["pdfs"]}
    # python baseline claims per stem — the strongest eligibility evidence
    baseline_claims = {}
    for root in sorted((MAIN / "results").glob("burn-power-batch*-local")):
        for f in root.glob("*.json"):
            if f.name.startswith("_"):
                continue
            try:
                d = json.loads(f.read_text())
            except Exception:
                continue
            if isinstance(d, dict) and "stem" in d:
                baseline_claims.setdefault(d["stem"], d.get("claims") or 0)

    # the 2,918 population: authoritative no_claims rows (catch-up era =
    # fixup outputs for redo stems + original for retained, minus the
    # corrupted-era rows which are already in redo)
    empties = []
    for f in sorted(args.outputs_root.glob("chunk-*/*.json")):
        if f.name == "manifest.json":
            continue
        try:
            d = json.loads(f.read_text())
        except Exception:
            continue
        if isinstance(d, dict) and d.get("skip") == "no_claims":
            empties.append(d["stem"])
    for f in sorted(args.ra03_root.glob("chunk-*/*.json")):
        if f.name == "manifest.json":
            continue
        try:
            d = json.loads(f.read_text())
        except Exception:
            continue
        if (isinstance(d, dict) and d.get("skip") == "no_claims"
                and d["stem"] not in redo):
            empties.append(d["stem"])

    rows = [classify(s, subs.get(s), baseline_claims.get(s))
            for s in sorted(set(empties))]
    counts = Counter(r["class"] for r in rows)
    eligible = [r for r in rows if r["class"] == "model_empty_despite_signals"]
    args.out.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n")
    print(json.dumps({
        "population": len(rows),
        "classes": dict(counts),
        "eligible_confirmed_recoverable": sum(
            1 for r in eligible if r.get("eligible") == "confirmed_recoverable"),
        "eligible_primary": sum(1 for r in eligible if r.get("eligible") == "primary"),
        "eligible_secondary": sum(1 for r in eligible if r.get("eligible") == "secondary"),
    }, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
