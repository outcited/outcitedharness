#!/usr/bin/env python3
"""reextract_held.py — staged page-drift repair for held claims (APPROVED
2026-10-21: staged only, originals preserved, before/after receipts).

For each HELD claim whose reason is page drift (verbatim_not_on_cited_page):
scan the document for the verbatim fragments. If they appear on EXACTLY ONE
other page, emit a staged correction (claim_id, original page, corrected
page, receipt). Multiple/zero hits are classified, never guessed. Nothing
mutates L1/L2 artifacts; output is a new staged file for review.
"""
import argparse, json, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from spine import PdfCache, verbatim_fragments, norm_text  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--claims", required=True, help="L1 claims jsonl")
    ap.add_argument("--l2", required=True, help="L2 ledger jsonl")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--shard", default="0/1")
    ap.add_argument("--map", action="append", default=[])
    args = ap.parse_args()

    idx, n = (int(x) for x in args.shard.split("/"))
    claims = {}
    for line in open(args.claims):
        c = json.loads(line)
        if int(c["claim_id"][:8], 16) % n == idx:
            claims[c["claim_id"]] = c

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    f_stage = (out / "reextract-staged.jsonl").open("a")
    f_receipt = (out / "reextract-receipts.jsonl").open("a")
    cache = PdfCache(args.map and [tuple(m.partition("=")[::2]) for m in args.map] or [])
    from collections import Counter
    counts = Counter()
    t0 = time.time()

    for line in open(args.l2):
        r = json.loads(line)
        cid = r.get("claim_id")
        if cid not in claims or r.get("verdict") != "held":
            continue
        if not any(x.startswith("verbatim_not_on_page") or
                   x == "verbatim_not_on_cited_page" for x in r.get("reasons", [])):
            counts["not_page_drift"] += 1
            continue
        c = claims[cid]
        import pymupdf
        p = cache.resolve(c["source_path"])
        try:
            doc = pymupdf.open(p)
        except Exception:
            counts["source_unreadable"] += 1
            continue
        frags = verbatim_fragments(c.get("verbatim"))
        hits = []
        if frags:
            for pi in range(doc.page_count):
                text = norm_text(doc[pi].get_text() or "")
                if all(f in text for f in frags):
                    hits.append(pi + 1)
        doc.close()
        orig_page = c.get("page_1based")
        if len(hits) == 1 and hits[0] != orig_page:
            counts["staged_page_correction"] += 1
            rec = {"claim_id": cid, "action": "page_correction_staged",
                   "before": {"page_1based": orig_page},
                   "after": {"page_1based": hits[0]},
                   "receipt": {"fragments": len(frags), "unique_hit_page": hits[0],
                               "original_evidence_untouched": True}}
            f_stage.write(json.dumps({**c, "page_1based": hits[0],
                                      "staged_correction_of": cid,
                                      "receipt": rec["receipt"]}, sort_keys=True) + "\n")
            f_receipt.write(json.dumps(rec, sort_keys=True) + "\n")
        elif orig_page in hits:
            counts["actually_on_cited_page"] += 1  # drifted under hardening? re-verify class
        elif len(hits) == 1:
            counts["already_correct"] += 1
        elif len(hits) > 1:
            counts["ambiguous_multi_page"] += 1
            f_receipt.write(json.dumps({"claim_id": cid, "action": "ambiguous",
                                        "hit_pages": hits[:10]}, sort_keys=True) + "\n")
        else:
            counts["not_found_in_doc"] += 1
            f_receipt.write(json.dumps({"claim_id": cid, "action": "needs_full_reextraction",
                                        "note": "verbatim absent from every page"}, sort_keys=True) + "\n")

    f_stage.close(); f_receipt.close()
    print(json.dumps({"shard": args.shard, "counts": dict(counts),
                      "wall_s": round(time.time() - t0, 1)}, indent=1))


if __name__ == "__main__":
    main()
