#!/usr/bin/env python3
"""Facet lane outputs -> catalog claims (the missing catalog link, 2026-10-22).

Reads fleet lane-out JSONL rows (thermal / mode_currents / aec) and attaches
them as catalog claims via harness.catalog.store.attach_claim — same law as
catalog_fill: only what the row actually carries, verbatim conditions,
provenance (doc sha + page + quote) on every claim. Idempotent per
(opn, doc, symbol, value, qualifier) tuple.

Symbol mapping (v1):
  thermal rows:  metric (RthetaJA/RthetaJC) + unit + value_role
  mode rows:     current_<mode slug> + unit + value_role
  aec rows:      aec_grade (verbatim grade string as value)

usage: facet_catalog_load.py [--lanes-dir DIR]... [--dry]
"""
import os
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, "/Users/samkim/Harnessv1")

from harness.catalog.store import (attach_claim, connect, plausible_opn,
                                   register_part)

CATALOG_DB = os.environ.get("CATALOG_DB", "/Users/samkim/Harnessv1/results/catalog-live.db")


def _slug(s):
    return re.sub(r"[^a-z0-9]+", "_", (s or "").strip().lower()).strip("_")


def row_to_claim(row, kind):
    """Map one facet row to (opn, symbol, qualifier, value, unit, condition,
    verbatim, page, doc_sha, extractor) or None if the row lacks the law's
    minimum (a real value + doc sha)."""
    doc_sha = row.get("pdf_sha") or row.get("document_sha256")
    if not doc_sha:
        return None
    opn = row.get("part_number") or ""
    page = row.get("page_1based")
    verbatim = row.get("verbatim") or row.get("context_verbatim") or ""
    extractor = row.get("method") or "facet_lane"
    qual = (row.get("value_role") or "typ").lower()
    if kind == "thermal":
        sym = row.get("metric")
        val = row.get("value")
        unit = row.get("unit") or ""
        cond = row.get("condition_verbatim") or ""
    elif kind == "mode":
        mode = row.get("mode") or "unknown"
        sym = f"current_{_slug(mode)}"
        val = row.get("value")
        unit = row.get("unit") or ""
        cond = row.get("conditions_verbatim") or row.get("conditions") or ""
    elif kind == "aec":
        sym = "aec_grade"
        val = row.get("grade") or row.get("value")
        unit = ""
        cond = row.get("context_verbatim") or row.get("condition_verbatim") or ""
    else:
        return None
    if sym is None or val is None or val == "":
        return None
    return dict(opn=opn, symbol=sym, qualifier=qual, value=val, unit=unit,
                condition=cond, verbatim=verbatim, page=page, doc_sha=doc_sha,
                extractor=extractor)


def load_lane_file(con, path, kind, existing, dry=False):
    n_attached = n_skipped = n_rejected = 0
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                n_rejected += 1
                continue
            m = row_to_claim(row, kind)
            if m is None:
                n_rejected += 1
                continue
            opn = m["opn"]
            if not plausible_opn(opn):
                opn = f"doc:{m['doc_sha'][:16]}"
            key = (opn, m["doc_sha"], m["symbol"].upper(), str(m["value"]), m["qualifier"])
            if key in existing:
                n_skipped += 1
                continue
            existing.add(key)
            if dry:
                n_attached += 1
                continue
            register_part(con, opn)
            attach_claim(
                con, opn,
                {"symbol": m["symbol"], "qualifier": m["qualifier"],
                 "condition": m["condition"], "value": str(m["value"]),
                 "unit": m["unit"], "quote": m["verbatim"],
                 "page": m["page"]},
                m["doc_sha"], extractor=m["extractor"])
            n_attached += 1
    return n_attached, n_skipped, n_rejected


def build_existing(con):
    existing = set()
    for r in con.execute(
            "SELECT opn, doc_sha256, symbol, value, qualifier FROM claims_canonical"):
        existing.add(tuple(r))
    return existing


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lanes-dir", action="append", required=True)
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()
    con = connect(CATALOG_DB)
    existing = build_existing(con)
    totals = {}
    for d in args.lanes_dir:
        d = Path(d)
        if not d.exists():
            print(f"skip (missing): {d}")
            continue
        for pattern, kind in (("thermal-*.jsonl", "thermal"),
                              ("mode-*.jsonl", "mode"),
                              ("aec-*.jsonl", "aec")):
            for f in sorted(d.glob(pattern)):
                a, s, r = load_lane_file(con, f, kind, existing, dry=args.dry)
                k = totals.setdefault(kind, [0, 0, 0])
                k[0] += a; k[1] += s; k[2] += r
    con.commit()
    for kind, (a, s, r) in totals.items():
        print(f"{kind}: attached={a} skipped={s} rejected={r}")


if __name__ == "__main__":
    main()
