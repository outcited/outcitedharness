#!/usr/bin/env python3
"""Pin-table baseline: text-tier extraction vs cr-core's gold (40 docs).

Same experiment shape as the field baseline: identical prompt discipline,
deterministic verifier (rows must quote the substrate), repair loop.
Reports against ALL four scoreboard numbers:
  recall | precision | name-exact | hallucinated-no-table pages
"""

import json
import re
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, "/Users/samkim/Harnessv1")
sys.path.insert(0, "/Users/samkim/Harnessv1/scripts")

from burn_aisle import (CLOUD_MODEL, OPENROUTER, call, load_substrate)

GOLD = Path("/Volumes/M5_4TB/exports/canon-gate/pin-gold-v1")
OUT = Path("/Volumes/M5_4TB/extract-results/pin-baseline-v1.json")

PIN_PROMPT = """You are a datasheet pin-table extraction engine. Given datasheet
text, extract the pin/pad definition table rows for the named package. Every row
value MUST be quoted character-for-character from the text. If the text contains
no pin definition table for this package, return an empty array — never invent.
Output ONLY JSON: {"has_pin_table": true|false, "package": "<as printed>",
"rows": [{"pin": "<designator>", "name": "<pin name>", "type": "<type or null>",
"quote": "<the verbatim table row line>"}]}"""


def stem_for_label(label):
    st = label.get("staged_as") or ""
    return Path(st).stem


def extract(text, pkg_hint):
    try:
        return call(OPENROUTER, CLOUD_MODEL, [
            {"role": "system", "content": PIN_PROMPT},
            {"role": "user", "content":
             f"Package: {pkg_hint}\n\nDatasheet text:\n{text}"}])
    except Exception:
        return None


def norm_cell(v):
    return re.sub(r"\s+", " ", str(v or "")).strip().lower()


DESIGNATOR_KEYS = ["pin number", "pin", "ball", "pad", "pin no", "pin#",
                   "designator", "pin name"]


def row_key(r):
    for k in DESIGNATOR_KEYS:
        for rk, rv in r.items():
            if norm_cell(rk) == k and rv:
                return norm_cell(rv)
    if r:
        first = next(iter(r.values()))
        return norm_cell(first)
    return None


def name_of(r):
    for k in ("pin name", "name", "signal"):
        for rk, rv in r.items():
            if norm_cell(rk) == k:
                return norm_cell(rv)
    return None


def index_rows(rows):
    idx = {}
    for r in rows or []:
        k1, k2 = row_key(r), name_of(r)
        entry = {"row": r}
        if k1:
            idx.setdefault(("d", k1), entry)
        if k2:
            idx.setdefault(("n", k2), entry)
    return idx


def score(label_rows, got_rows):
    want = index_rows(label_rows)
    got = index_rows(got_rows)
    matched_keys = set(want) & set(got)
    matched_rows = {id(want[k]["row"]) for k in matched_keys}
    labeled_rows = {id(r) for r in label_rows or []}
    extra_keys = set(got) - set(want)
    matched = len(matched_rows)
    labeled = len(labeled_rows)
    name_exact = 0
    for k in matched_keys:
        wn, gn = name_of(want[k]["row"]), name_of(got[k]["row"])
        if wn and gn and wn == gn:
            name_exact += 1
        elif wn and gn and (wn == row_key(got[k]["row"]) or gn == row_key(want[k]["row"])):
            name_exact += 1
    return {"labeled": labeled, "matched": matched,
            "recall": round(matched / labeled, 4) if labeled else None,
            "extra_rows": len(extra_keys),
            "name_exact": round(name_exact / matched, 4) if matched else None}


def build_sha_index():
    idx = {}
    for root in [Path("/Volumes/M5_4TB/extract-results/collected"),
                 Path("/Volumes/M5_4TB/extract-results/runs")]:
        if not root.exists():
            continue
        for jf in root.rglob("*.json"):
            try:
                rec = json.loads(jf.read_text())
                sha = rec.get("sha256")
                if sha and sha not in idx:
                    idx[sha] = rec
            except Exception:
                continue
    return idx


def main():
    labels = [json.loads(l) for l in (GOLD / "holdout-pins40-vision-labels-v1.jsonl").open()]
    print("building sha index...", flush=True)
    sha_idx = build_sha_index()
    print(f"index: {len(sha_idx)} substrate docs", flush=True)
    results = []
    agg = {"docs": 0, "has_table_docs": 0, "hallucinated_table_docs": 0,
           "matched": 0, "labeled": 0, "extra": 0, "name_exact_matched": 0,
           "no_substrate": 0}
    for label in labels:
        sub = sha_idx.get(label.get("document_sha256"))
        if not sub:
            agg["no_substrate"] += 1
            results.append({"sha": str(label.get("document_sha256"))[:12], "skip": "no_substrate"})
            print(f"{label.get('document_sha256', '')[:12]}: NO SUBSTRATE", flush=True)
            continue
        stem = Path(sub.get("filename", "?")).stem
        pages = sub.get("pages", [])
        page_no = label.get("page_1based")
        window = [p for p in pages if abs(p["page"] - page_no) <= 1]
        if not window:
            window = pages[:12]
        text = "\n".join(p.get("text", "") for p in window)[:28000]
        out = extract(text, label.get("package_as_printed") or stem)
        if isinstance(out, list):
            out = {"has_pin_table": bool(out), "rows": out}
        has_table = bool(out and out.get("has_pin_table") and out.get("rows"))
        label_has = bool(label.get("has_pin_table"))
        s = score(label.get("rows", []), out.get("rows", []) if out else [])
        agg["docs"] += 1
        agg["has_table_docs"] += has_table
        if has_table and not label_has:
            agg["hallucinated_table_docs"] += 1
        agg["matched"] += s["matched"]
        agg["labeled"] += s["labeled"]
        agg["extra"] += s["extra_rows"]
        agg["name_exact_matched"] += round((s["name_exact"] or 0) * s["matched"])
        results.append({"stem": stem, "has_table": has_table, "label_has": label_has,
                        "page": page_no, "score": s})
        print(f"{stem}: table={has_table}/label={label_has} "
              f"recall={s['recall']} extra={s['extra_rows']}", flush=True)
    summary = {
        "docs": agg["docs"],
        "recall": round(agg["matched"] / max(1, agg["labeled"]), 4),
        "precision": round(agg["matched"] / max(1, agg["matched"] + agg["extra"]), 4),
        "name_exact": round(agg["name_exact_matched"] / max(1, agg["matched"]), 4),
        "hallucinated_no_table_docs": agg["hallucinated_table_docs"],
        "scoreboard_to_beat": {"recall": 0.773, "precision": 0.68,
                               "name_exact": 0.60, "hallucinated_pages": 37},
    }
    OUT.write_text(json.dumps({"summary": summary, "agg": agg, "results": results}, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
