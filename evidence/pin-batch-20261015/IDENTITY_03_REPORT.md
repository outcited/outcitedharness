# PINOUT-IDENTITY-03 — Final Report & Incremental Admission Packet (batch 2)

**Baseline:** `bbfb34ae` · **Prepared:** 2026-10-15 · m5-opencode · **No release executed**

## Deliverable A — Root-cause breakdown of all 14,131 unmatched identities

| Bucket | Count | Evidence rule | Example |
|---|---:|---|---|
| **6a. Valid OPN absent from served catalog** | **11,195** | cleaned stem is OPN-shaped, guards pass, no served counterpart | `mic2208`, `ntms4802n`, `20005624B` |
| 7. Ambiguous / not OPN-shaped | 1,791 | charset/shape guard | locale/rev stems, doc refs (`en.DM00653929`) |
| 4. Family/series/title-derived (protected) | 782 | TITLEY guard — never joins | `MCP16361-Family-Data-Sheet`, `11AAXXX-…-Family` |
| 3→joined. Canonicalizable alias | 363 | canon(stem) == canon(served OPN) | `stm32wba5mmg` → `STM32WBA5MMG` |
| 2→joined. Stem-exact after cleaning | 16 | mfr-prefix/doc-id/locale strip | `MIC4600-Data-Sheet-20005584B` → `MIC4600` |

**The principal finding: identity resolution was never the main blocker —
catalog onboarding is.** 79% of staged identities are valid-looking OPNs the
served catalog simply does not carry. They are candidates for catalog intake
(a Store function), not for alias tricks.

## Deliverable B — Join chain (authority: served export feeds, SHA-pinned)

`PDF SHA → delivery manifest (source_url, manufacturer) → cleaned URL-stem
(mfr-prefix, doc-id, locale/rev suffix strips) → guards (family/title/shape)
→ R1 exact | R2 canon-equality → canonical served OPN + feed`

Every join row (`identity-joins.jsonl`) carries: original slug, manufacturer,
source SHA, stem, rule version `identity-join-v1`, status, canonical target,
feed, alias rule. Unresolved rows carry their cause.

## Deliverable C — Rules and guards (all deterministic, 8 regression tests)
`scripts/pin_identity_join.py` + `tests/test_pin_identity_join.py`:
- family/datasheet-summary/title stems NEVER join (3 tests)
- mfr prefix, Microchip doc-id (7-8 digit), locale, rev strips (4 tests)
- canon equality is reversible identity (1 test)
No fuzzy similarity anywhere. No family→OPN fanout. No new served OPNs created.

## Deliverable E — Measured improvement
- **Before: 16** net-new served OPNs (ADMISSION-02 measurement)
- **After: 378 distinct canonical OPNs** (16 exact + 363 proven aliases)
- Joined-cohort tier mix: 54 COMPLETE/VERIFIED, 44 PARTIAL/VERIFIED,
  281 UNCONFIRMED COMPLETENESS
- Pin-for-pin compatibility claims remain restricted to COMPLETE tier (54).

## Deliverable D — Three-store baseline reconciliation (why M4 ≠ 16 ≠ 0)
| Store | Pinout coverage | Snapshot ID |
|---|---|---|
| M5 staging (board) | 17,826 MCU parts, 25.6% | BOARD 20261015T153719Z |
| Served export (4 feeds) | parts with pin-related claims: 2,189 (burn 212, catalog 62, cr-core 1,562, family 353) — these are pin-COUNT/feature claims; **zero pin tables** | feeds SHA-pinned in §B |
| Facade/cr-core stratum (M4's source) | 3,821 parts pinout-bearing (95.7% of its MCU aisle) — lives in the cr-core stratum store, not flattened into export feeds | cr-core delivery r1 |

**Root cause of the discrepancy:** three different stores and two different
measurement definitions (pin TABLES vs pin CLAIMS vs stratum facets). The
prior "0 served pinout" was a top-level-key measurement artifact on the flat
claims feeds. Newly joinable to served OPNs: **378**; customer-visible net
new after a release flip: **378** (none carry pin tables today; all gain
staged pin-table evidence, tier-mixed as above).

## Deliverable F — Incremental admission proposal (batch 2, for cr-core)
- Batch 1 (10 exact + 6 alias) remains with cr-core untouched.
- Batch 2 = the **363 alias + 16 exact** joins in `identity-joins.jsonl`
  (status exact/alias). Alias proof = deterministic canon equality, rule
  version pinned, per-row stem+manufacturer evidence, reproducible by
  re-running `pin_identity_join.py` against the pinned feed SHAs.
- Regression fixtures: `tests/test_pin_identity_join.py` (8) +
  `tests/test_pin_count_parse.py` (7).
- Rollback: joins are a mapping artifact, not a store write — drop the
  mapping file and re-export; nothing mutated in the feeds.
- Unknowns: 11,195 valid-but-absent OPNs (catalog intake backlog),
  1,791 ambiguous, 782 family docs (retained as family-level evidence).

## Recommendation
1. cr-core reviews batch 1 (16) + batch 2 (379) together → single flip adds
   378 OPNs of pin-table evidence to the served store.
2. Store team treats the **11,195 absent-OPN list as the catalog intake
   backlog** — that is where the remaining 99% of this batch's value sits.
3. Family docs (782) stay family-associated; no fanout.

**No release flip, no promotion, no extraction rerun. TP4 lane untouched.**