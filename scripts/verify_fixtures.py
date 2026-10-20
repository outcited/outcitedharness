#!/usr/bin/env python3
"""verify_fixtures.py — independent proof of L5 fixtures against source PDFs.

Deliberately shares NO code with spine.py: raw pymupdf + the fixture JSON.
For each axis: resolve the source PDF by the evidence sha via the l4 rows'
source_path, recompute the file sha256, open the EXACT cited page, and
assert (a) the verbatim fragment appears on that page, (b) the asserted
value's printed form appears on that page. Prints a receipt per axis.
"""
import json, hashlib, sys
from pathlib import Path
import pymupdf

FIXTURES = Path("/Users/samkim/lane-out/spine/fixtures")
L4 = Path("/Users/samkim/lane-out/spine/full/l4-bound.jsonl")
_WS = __import__("re").compile(r"\s+")


def nrm(s):
    return _WS.sub(" ", s or "").strip()


# evidence sha -> source_path (from the bound rows, then verified by re-hash)
sha_path = {}
for line in L4.open():
    r = json.loads(line)
    if r.get("pdf_sha") and r.get("source_path"):
        sha_path.setdefault(r["pdf_sha"], r["source_path"])


def file_sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        while c := fh.read(1 << 20):
            h.update(c)
    return h.hexdigest()


def value_forms(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return []
    out = {str(v)}
    if f == int(f):
        out |= {str(int(f)), f"{int(f):,}"}
    for d in (1, 2, 3):
        out.add(f"{f:.{d}f}")
    return list(out)


fails = passes = 0
for fx in sorted(FIXTURES.glob("*.json")):
    d = json.loads(fx.read_text())
    print(f"== fixture {fx.name}: {d['a']} vs {d['b']}")
    for ax in d.get("axes", []):
        for side in ("a", "b"):
            ev = ax[side]["evidence"]
            sha, page, verb = ev.get("sha"), ev.get("page"), ev.get("verbatim") or ""
            val = ax[side].get("value")
            p = sha_path.get(sha)
            if not p:
                print(f"  FAIL {side} {ax['field']}: no source path for sha {sha}")
                fails += 1
                continue
            actual = file_sha(p)
            if actual != sha:
                print(f"  FAIL {side} {ax['field']}: sha mismatch {actual[:12]} != {sha[:12]} ({p})")
                fails += 1
                continue
            doc = pymupdf.open(p)
            if not isinstance(page, int) or page < 1 or page > doc.page_count:
                print(f"  FAIL {side} {ax['field']}: page {page} outside doc")
                fails += 1
                continue
            text = nrm(doc[page - 1].get_text() or "")
            doc.close()
            frag = next((f for f in (nrm(x) for x in verb.split(" | ")) if len(f) >= 6), nrm(verb))
            ok_verb = frag in text
            forms = value_forms(val)
            ok_val = (not forms) or any(f in text for f in forms)
            status = "PASS" if (ok_verb and ok_val) else "FAIL"
            if status == "PASS":
                passes += 1
            else:
                fails += 1
            print(f"  {status} {side:1s} {str(ax['field'])[:14]:14s} page={page} "
                  f"sha={sha[:12]} verbatim_on_page={ok_verb} value_on_page={ok_val}")

print(f"\n{passes} PASS / {fails} FAIL")
sys.exit(1 if fails else 0)
