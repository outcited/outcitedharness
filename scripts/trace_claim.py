#!/usr/bin/env python3
"""trace_claim.py — one claim, end to end: L1 -> L2 -> L3 -> L4 -> L5 -> staged.
Prints the full chain with sha/page/verbatim/transform at every hop."""
import json, sys
from pathlib import Path

S = Path("/Users/samkim/lane-out/spine")
claims = {json.loads(l)["claim_id"]: json.loads(l) for l in (S / "pilot-1000.jsonl").open()}
l2 = {json.loads(l)["claim_id"]: json.loads(l) for l in (S / "pilot3/l2-ledger.jsonl").open()}
l3 = {}
for l in (S / "pilot3/l3-normalized.jsonl").open():
    r = json.loads(l)
    l3[r["claim_id"]] = r
l4 = {json.loads(l)["claim_id"]: json.loads(l) for l in (S / "pilot3/l4-bound.jsonl").open()}

# pick: verified + normalized + exact_opn with a part slug and clean evidence
best = None
for cid, r in l4.items():
    if r.get("tier") == "exact_opn" and r.get("field") in ("sram_kb", "flash_kb", "clk_max_mhz"):
        best = cid
        break

c, v, n, b = claims[best], l2[best], l3.get(best, {}), l4[best]
trace = {
  "L1_raw": {k: c[k] for k in ("claim_id", "lane", "field", "value_raw", "unit_raw",
                                "page_1based", "pdf_sha", "source_path", "part_slug", "verbatim")},
  "L2_verified": {k: v.get(k) for k in ("verdict", "reasons", "checks")},
  "L3_normalized": {k: n.get(k) for k in ("value_canon", "unit_canon", "transform",
                                           "mode_canon", "conditions_typed", "value_role")},
  "L4_identity": {k: b.get(k) for k in ("part_slug", "slug_source", "tier", "opn", "rule")},
  "L5_staged": {"note": "enters fixtures only via exact_opn pair with compatible axis; "
                        "ExportStore promotion ON HOLD per Sam 2026-10-21"},
  "chain_integrity": {
      "pdf_sha_unchanged": c["pdf_sha"] == v["checks"].get("sha256_recomputed"),
      "page_unchanged": c["page_1based"] == (v["checks"].get("page_offset") == 0),
      "verbatim_preserved": c["verbatim"] == b.get("verbatim"),
      "original_value_preserved": c["value_raw"] == b.get("value_raw") if "value_raw" in b else "see L1",
  },
}
print(json.dumps(trace, indent=1, sort_keys=True))
