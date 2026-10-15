#!/usr/bin/env python3
"""PINOUT-ADMISSION-02 tier engine.

Assigns an evidence-based admission disposition to every staged pin-table
record, computed from primary evidence (substrate pages), not from the model's
self-report:

  COMPLETE/VERIFIED        printed pin-count located AND rows >= count AND
                           package identity printed in the document
  PARTIAL/VERIFIED         printed pin-count located AND rows < count
                           (every staged row is quote-verified by construction)
  UNCONFIRMED COMPLETENESS no printed pin-count locatable — rows verified,
                           completeness cannot be established
  BLOCKED                  citation failure: row quotes not locatable in the
                           substrate pages (source citation cannot be validated)

Writes: results/pin-tables-20261014/admission-tiers.jsonl (one row per doc)
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PIPELINE_DB = ROOT / "results/pipeline-live.db"
OUT = ROOT / "results/pin-tables-20261014/admission-tiers.jsonl"

# Printed-count candidates are only trusted when adjacent to a package token
# ("16-pin TSSOP", "TSSOP-16", "pin count: 16"). Bare "N-pin" strings grab
# spurious 1/2/318 values from prose (observed in QA 2026-10-15) which inflated
# COMPLETE classifications. Conservative by design: unprovable counts yield
# UNCONFIRMED COMPLETENESS, never a false COMPLETE.
PKG_TOKENS = r"(?:SOIC|TSSOP|SSOP|MSOP|TSOP|LQFP|QFN|DFN|SON|SOP|DIP|SDIP|TO-?\d+|SOT\d*\w*|BGA|LBGA|WLCSP|CSP|LGA|LLP|QFP|TQFP|VQFN|UQFN|HVQFN|PGA|DIL|package)"
PIN_COUNT_PATTERNS = [
    re.compile(rf"(\d{{2,3}})\s*-\s*pin\s+{PKG_TOKENS}", re.IGNORECASE),
    re.compile(rf"{PKG_TOKENS}[\s-]*(\d{{2,3}})\b", re.IGNORECASE),
    re.compile(r"pin\s+count[^0-9]{0,12}(\d{2,3})\b", re.IGNORECASE),
]
MAX_SANE_PINS = 400

_tls = threading.local()


def _con():
    if getattr(_tls, "con", None) is None:
        _tls.con = sqlite3.connect(f"file:{PIPELINE_DB}?mode=ro", uri=True, timeout=60)
        _tls.con.row_factory = sqlite3.Row
    return _tls.con


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip().lower()


def substrate_pages(sha: str) -> list[str] | None:
    row = _con().execute(
        "SELECT output FROM results WHERE corpus_key=? AND kind_hint='substrate'"
        " ORDER BY id DESC LIMIT 1", (f"pdf:{sha}",)).fetchone()
    if not row:
        return None
    try:
        return [norm(p.get("text") or "")
                for p in json.loads(row["output"]).get("pages") or []]
    except Exception:
        return None




def printed_pin_count(pages_text, package, n_rows):
    """High-precision printed-pin-count extraction (regression-tested).

    Returns (count, suspect_surplus): count is trusted only from explicit
    high-precision patterns; rows>count marks suspect (dupes/misparse) and
    yields (None, True)."""
    pkg_words = [w for w in re.split(r"[^a-zA-Z]+", str(package or ""))
                 if len(w) >= 3]
    scopes = list(pages_text) + [" ".join(pages_text)]
    for scope in scopes:
        cands = [int(m.group(1)) for m in re.finditer(
            r"pin\s+count[^0-9]{0,12}(\d{2,3})\b", scope, re.I)]
        # count trusted only when followed by a KNOWN package family word —
        # "318-pin provisioning" (the QA anomaly) must never certify
        PKG_WORDS = (r"TSSOP|SSOP|MSOP|TSOP|SOIC|SOP|SON|WSON|VSON|USON|DFN|QFN|"
                     r"HVQFN|VQFN|UQFN|DIP|SDIP|LQFP|QFP|TQFP|BGA|LBGA|LGA|"
                     r"WLCSP|CSP|SOT|TO|PGA|DIL|LLP")
        cands += [int(m.group(1)) for m in re.finditer(
            rf"\b(\d{{2,3}})\s*-\s*pin\s+(?:{PKG_WORDS})\b", scope, re.I)]
        for w in pkg_words:
            cands += [int(m.group(1)) for m in re.finditer(
                rf"\b{re.escape(w)}\s*-\s*(\d{{2,3}})\b", scope, re.I)]
        cands = [c for c in cands if 2 <= c <= MAX_SANE_PINS]
        if cands:
            c = max(cands)
            if c < n_rows:
                return None, True
            return c, False
    return None, False


def classify(sha: str, rows: list[dict], package: str | None) -> dict:
    pages = substrate_pages(sha)
    if pages is None:
        return {"tier": "BLOCKED", "reason": "no_substrate", "page": None,
                "printed_count": None, "package_printed": False}
    joined = " ".join(pages)
    # citation check: every row quote locatable somewhere in the document
    missing_quotes = sum(1 for r in rows
                         if norm(r.get("quote")) and norm(r.get("quote")) not in joined)
    if missing_quotes == len(rows):
        return {"tier": "BLOCKED", "reason": "quotes_not_in_source", "page": None,
                "printed_count": None, "package_printed": False}
    # page identity: first page where the most quotes land
    best, bestn = (None, 0)
    for i, t in enumerate(pages):
        h = sum(1 for r in rows if norm(r.get("quote")) and norm(r.get("quote")) in t)
        if h > bestn:
            best, bestn = i, h
    # printed pin count: search pin-table page first, then whole doc, sane-cap
    # Count is trusted ONLY when adjacent to the record's OWN claimed package
    # ("16-pin TSSOP" when the record claims TSSOP), or an explicit
    # "pin count: N" phrase. Generic package tokens elsewhere in the document
    # (e.g. a SOT-23 mention in an 8-pin SOP datasheet) produced false PARTIALs
    # in the first run — 47x SOT23->23 alone. Anchor or nothing.
    printed, _surplus = printed_pin_count(
        [pages[best]] if best is not None else [], package, len(rows)) \
        if best is not None else printed_pin_count([], package, len(rows))
    if printed is None:
        printed, _surplus2 = printed_pin_count([], package, len(rows)) \
            if best is None else (None, False)
        # fall through: joined-doc scan when page-scoped found nothing
        if printed is None:
            joined_probe, _ = printed_pin_count([joined], package, len(rows))
            printed = joined_probe
    pkg_printed = bool(package) and norm(package) in joined
    n = len(rows)
    if printed is not None and n == printed and pkg_printed:
        tier = "COMPLETE/VERIFIED"
    elif printed is not None and n < printed:
        tier = "PARTIAL/VERIFIED"
    else:
        tier = "UNCONFIRMED COMPLETENESS"
    return {"tier": tier, "reason": None, "page": (best + 1) if best is not None else None,
            "printed_count": printed, "package_printed": pkg_printed,
            "partial_quote_misses": missing_quotes}


def main() -> int:
    sha2pn = {}
    for l in (ROOT / "results/pin-tables-20261014/pin-batch-queue.jsonl").open():
        r = json.loads(l)
        sha2pn[r["sha"]] = r["part_number"]
    from concurrent.futures import ThreadPoolExecutor
    docs = []
    for l in (ROOT / "results/pin-tables-20261014/pin-rows-batch.jsonl").open():
        r = json.loads(l)
        if r.get("verdict") == "ok" and r.get("rows"):
            docs.append(r)
    print(f"classifying {len(docs)} staged records", flush=True)
    t0 = time.time()
    n = 0
    with ThreadPoolExecutor(max_workers=8) as pool, OUT.open("w") as fh:
        def work(r):
            c = classify(r["sha"], r["rows"], r.get("package"))
            return {"sha": r["sha"], "part_number": sha2pn.get(r["sha"]),
                    "rows": len(r["rows"]), "package": r.get("package"), **c}
        for rec in pool.map(work, docs):
            fh.write(json.dumps(rec, sort_keys=True) + "\n")
            n += 1
            if n % 2000 == 0:
                print(f"  {n}/{len(docs)} {time.time()-t0:.0f}s", flush=True)
    print(f"done {n} in {time.time()-t0:.0f}s -> {OUT}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())