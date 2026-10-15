# PINOUT-ADMISSION-02 — Promotion Packet (for Store/GOLD release owner review)

**Baseline commit:** `18fb446d76232339077c9d8d61e23ab758b703b2`
**Prepared:** 2026-10-15 · m5-opencode · **NOT executed — staged for review**

## 1. Inputs (pinned)
- Served-catalog snapshot: `api-store-export/parts_{burn,catalog,cr_core,family}.jsonl`
  - parts_burn.jsonl sha256 `81afa88934cfa91f…` (47,182,718 B)
  - parts_catalog.jsonl sha256 `ecab80712463d7a3…` (30,610,366 B)
  - parts_cr_core.jsonl sha256 `ce11e3933a61f62a…` (39,386,615 B)
  - parts_family.jsonl sha256 `8463ce68397f1c57…` (9,019,332 B)
  - Release ID: the export feeds carry no explicit ReleaseId — treat this
    4-file SHA set as the pinned release for this review.
- Batch outputs: `results/pin-tables-20261014/pin-rows-batch.jsonl`
  (23,318 docs, 210,193 verified rows; see PIN_BATCH_RECEIPT.md).

## 2. Admission disposition counts (evidence-based, conservative)
| Tier | Docs | Rule |
|---|---:|---|
| COMPLETE/VERIFIED | 794 | printed count == rows AND package printed in doc |
| PARTIAL/VERIFIED | 2,078 | printed count > rows (rows individually quote-verified) |
| UNCONFIRMED COMPLETENESS | 11,384 | verified rows; completeness not provable |
| BLOCKED | 0 | (no citation failures) |
| **total staged** | **14,256** | |

Counter is high-precision by design (two misfire rounds documented); 7
regression tests pin the defect classes (`tests/test_pin_count_parse.py`,
including the observed "318-pin" anomaly — confirmed regex defect, fixed).

## 3. Served-catalog intersection (pinned snapshot above)
- Served OPNs: 15,872 · already with pinout: **0**
- Staged with evidence: 14,147 part identities
- Exact canonical matches: **10** · alias/normalized: **6** · unmatched: 14,131
- **NET NEW served OPNs gaining pinout evidence after promotion: 16**
- **Known gap (the honest headline): staged identities are delivery-URL slugs
  (e.g. `infineon-infineon-bts5030-2eka-datas`), not canonical OPNs.** The
  25.6% board figure is STAGING coverage, not production coverage. Unlocking
  the remaining ~14K requires a slug→OPN mapping (manifest MPN data or
  cr-core's mpn-bearing stratum) — recommended as the follow-on workstream.

## 4. QA evidence (reproducible)
- `qa-stratified-60.json` — 60 docs (20/tier, seed 20261015):
  **60/60 sha-verified, 60/60 page-located, 0 blocked.** 21 multi-page tables;
  68 NC pins, 178 power pins, 102 repeated-name instances (all row classes
  verified on-page). Full N/N pin presence on the primary page in 44/54
  primary-page cases (remainder = multi-page spread, consistent).
- `qa-sample-20.json` — original sample; both "missing" PDFs resolved
  (sha-verified after path repair).
- 3/318 anomaly: CONFIRMED regex defect (spurious "N-pin <any-word>");
  fixed + regression-tested.

## 5. Source-path drift — RESOLVED
All previously "missing" PDFs located. Two causes, both deterministic:
1. Duplicate wave deliveries (same sha in datasheet-tail + incrementals) —
   resolver now tries all queued locators.
2. Mirror layout normalization (`blobs/blobs/` → `blobs/`) — deterministic
   fallback added. Repair recorded per-record (`locator_repair`).
No document was marked verified without its source.

## 6. Continuing extraction (preserved, per directive)
- TP4 whole-queue lane: untouched, still running (18,900+ docs).
- Batch: NOT rerun (retriever dedupes by custom_id).
- Remaining queue reconciled after TP4-overlap removal: **4,632 docs** on the
  subscription lane (deepseek-v4.1-flash only, per policy), running.

## 7. Expected post-promotion effect
- Served pinout coverage: 0 → 16 OPNs (+16 net; feeds currently carry none).
- Staging board unchanged by promotion itself.

## 8. Rollback / release verification (for the release owner)
- Promotion = fold `pinout` sections from staging into the served export
  feeds + re-run `extraction_board.py`; feeds are append-only JSONL —
  rollback = drop the appended pinout blocks (recorded line ranges) and
  re-run the board. Verify by: served OPN count with pinout == 16, and
  `dw-truth::verify_claims` still green on the N4 corpus.

## 9. Exceptions / unresolved
- Slug→OPN mapping (§3) — blocks the bulk of served-side value.
- 988 retry docs (834 unparseable + 154 unverified) — in the subscription lane.
- Pin-for-pin compatibility: NOT claimable from this evidence; per PRD,
  requires COMPLETE package-specific reads (794 qualify today).

## 10. Recommendation
Promote the 16 exact-match OPNs (append-only, reversible) **after** the
release owner validates this packet; treat the slug→OPN mapping as the P0
follow-on that converts the remaining 14K staged identities into served value.
No speculative compatibility claims. No release executed by this agent.