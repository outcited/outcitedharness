#!/usr/bin/env python3
"""reader_v2.py — extraction-time provenance repair (Reader-v2), 2026-10-21.

Root causes measured (42-claim trace, failure-analysis-42.json):
  synthetic_verbatim (reader joins/normalizes tokens), unicode mutation
  (θ→Θ), multi-page table spans, tiny fragments, value-form mismatches.

Repair law — attribution at extraction time, never blind page search:
  For every reader row, each verbatim fragment is located on the page whose
  RAW text contains it (whitespace-folded only). Rows gain:
    cells: [{text, page_1based, role(value|context)}]   per-cell provenance
    page_1based := page of the VALUE cell (physical, exact)
    page_span   := [min,max] when context cells span pages
    provenance  := 'per-cell-v2' | 'synthetic' (unlocatable fragment)
  Evidence strings are NEVER mutated; normalizations stay in fields.

Depends on the fleet-pending burn_pass readers (import only; no edits).
"""
import hashlib, json, re, sys, time, unicodedata
from pathlib import Path

sys.path.insert(0, "/Users/samkim/Harnessv1/scripts")
import pymupdf
from burn_pass import scan_one  # fleet lineage, read-only import

_WS = re.compile(r"\s+")


def nrm(s):
    s = unicodedata.normalize("NFKC", s or "").replace("−", "-").replace("\u00a0", " ")
    return _WS.sub(" ", s).strip()


def value_forms(val):
    try:
        f = float(val)
    except (TypeError, ValueError):
        return [str(val)] if val is not None else []
    out = {str(val)}
    if f == int(f):
        out |= {str(int(f)), f"{int(f):,}"}
    for d in (1, 2, 3):
        out.add(f"{f:.{d}f}")
    return [x for x in out if x]


def bind_row(row: dict, page_norm: list[str], doc_pages: int) -> dict:
    """Bind per-cell provenance. Structure-first law: the reader's cited page
    is trusted as PRIMARY attribution when the fragment verifiably occurs
    there; single-alternative corrections are recorded; genuine ambiguity
    (repeated values/headings on multiple pages, cited page not among them)
    FAILS CLOSED — cell text on a page never establishes parentage alone."""
    verbatim = row.get("verbatim") or row.get("standard_verbatim") or ""
    cited = row.get("page_1based")
    frags = [nrm(x) for x in verbatim.split(" | ")]
    frags = [f for f in frags if len(f) >= 2]
    cells = []
    synthetic = []
    ambiguous = []
    vforms = value_forms(row.get("value"))
    for f in frags:
        candidates = [pi + 1 for pi in range(doc_pages) if f in page_norm[pi]]
        role = "value" if (vforms and any(v in f for v in vforms)) else "context"
        if not candidates:
            synthetic.append(f)
        elif isinstance(cited, int) and cited in candidates:
            cells.append({"text": f, "page_1based": cited, "role": role,
                          "attribution": "reader-structure-verified"})
        elif len(candidates) == 1:
            cells.append({"text": f, "page_1based": candidates[0], "role": role,
                          "attribution": "corrected-unique"})
        else:
            ambiguous.append({"text": f, "candidate_pages": candidates[:8], "role": role})
    out = dict(row)
    out["cells"] = cells
    out["synthetic_fragments"] = synthetic
    out["ambiguous_fragments"] = ambiguous
    vcells = [c for c in cells if c["role"] == "value"]
    if cells:
        pages = [c["page_1based"] for c in cells]
        out["page_span"] = [min(pages), max(pages)]
        out["page_1based"] = (vcells[0] if vcells else cells[0])["page_1based"]
    if ambiguous:
        out["provenance"] = "ambiguous"
    elif cells and not synthetic:
        out["provenance"] = "per-cell-v2"
    elif cells:
        out["provenance"] = "partial-synthetic"
    else:
        out["provenance"] = "synthetic"
    return out


def process_doc(item: dict, path_map=None) -> dict:
    """One document through fleet readers + provenance binder. Returns
    v2 rows per reader plus binder stats. Never mutates inputs."""
    t0 = time.time()
    res = scan_one(item, path_map or [], ("thermal", "mode", "aec", "pillars"), None)
    if res.get("status", "").startswith("error"):
        return res
    p = item["source_path"]
    for old, new in (path_map or []):
        if p.startswith(old):
            p = new + p[len(old):]
            break
    try:
        doc = pymupdf.open(p)
        page_norm = [nrm(doc[i].get_text() or "") for i in range(doc.page_count)]
        n = doc.page_count
        doc.close()
    except Exception as exc:  # noqa: BLE001
        return {"status": f"error:{type(exc).__name__}", "source_path": item["source_path"]}
    rows_out, stats = {}, {}
    for reader, rows in (res.get("rows") or {}).items():
        bound = [bind_row(r, page_norm, n) for r in rows]
        rows_out[reader] = bound
        stats[reader] = {
            "rows": len(bound),
            "per_cell_v2": sum(1 for b in bound if b["provenance"] == "per-cell-v2"),
            "partial": sum(1 for b in bound if b["provenance"] == "partial-synthetic"),
            "synthetic": sum(1 for b in bound if b["provenance"] == "synthetic"),
        }
    return {"status": res.get("status"), "source_path": item["source_path"],
            "sha256": res.get("sha256"),
            "rows": rows_out, "binder_stats": stats,
            "wall_ms": round((time.time() - t0) * 1000, 1)}


_RUN_PATH_MAP = []

def _process_one(d):
    try:
        return process_doc(d, _RUN_PATH_MAP)
    except Exception as exc:  # noqa: BLE001 — per-doc fail-closed
        return {"status": f"error:{type(exc).__name__}",
                "source_path": d.get("source_path"), "rows": {}}

def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--queue", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--map", action="append", default=[],
                    help="path prefix rewrite OLD=NEW (repeatable)")
    args = ap.parse_args()
    q = json.loads(Path(args.queue).read_text())
    docs = q.get("documents") or q
    if args.limit:
        docs = docs[: args.limit]
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    from collections import Counter
    agg = Counter()
    fh_rows = {r: (out / f"{r}.jsonl").open("a") for r in ("thermal", "mode", "aec", "pillars")}
    fh_led = (out / "ledger.jsonl").open("a")
    path_map = []
    for m in args.map:
        old, _, new = m.partition("=")
        if old and new:
            path_map.append((old, new))
    from concurrent.futures import ProcessPoolExecutor
    t0 = time.time()
    global _RUN_PATH_MAP
    _RUN_PATH_MAP = path_map
    if args.workers > 1:
        with ProcessPoolExecutor(max_workers=args.workers) as ex:
            results = list(ex.map(_process_one, docs, chunksize=4))
    else:
        results = [_process_one(d) for d in docs]
    for i, r in enumerate(results, 1):
        sha = r.get("sha256")
        for reader, rows in (r.get("rows") or {}).items():
            for row in rows:
                row.update({"pdf_sha": sha, "document_sha256": sha,
                            "source_path": r["source_path"], "verdict": "printed_row"})
                fh_rows[reader].write(json.dumps(row, sort_keys=True) + "\n")
        led = {"status": r["status"], "source_path": r["source_path"], "sha256": sha,
               "binder_stats": r.get("binder_stats")}
        fh_led.write(json.dumps(led, sort_keys=True) + "\n")
        for reader, st in (r.get("binder_stats") or {}).items():
            agg[f"{reader}_v2"] += st["per_cell_v2"]
            agg[f"{reader}_partial"] += st["partial"]
            agg[f"{reader}_synthetic"] += st["synthetic"]
        if i % 25 == 0:
            print(f"{i}/{len(docs)} {dict(agg)} {time.time()-t0:.0f}s", flush=True)
    for fh in fh_rows.values():
        fh.close()
    fh_led.close()
    print(json.dumps({"docs": len(docs), "agg": dict(agg),
                      "wall_s": round(time.time() - t0, 1)}, indent=1))


if __name__ == "__main__":
    main()
