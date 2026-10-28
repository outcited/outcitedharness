# DW-TRUTH-FUNNEL-02 — 13-claim gap ledger (issue #5 phase 2)

System of record: `outcited/outcitedharness#5`.
Source set: the pilot's 13 HELD claims in
`evidence/truth-funnel-20261028/sampled_source_proof_v1.json`
(DW-TRUTH-FUNNEL-01, PR #56 — declared dependency, unmerged, read-only).

Every classification below is INDEPENDENT. No repairs, no promotion, no
substitution. A gap is recorded where the link fails; where evidence is
insufficient the cell reads UNKNOWN, never a guess.

## Gap taxonomy (independent, may co-occur)

- **G1 missing OPN identity** — the claim carries no manufacturer + exact OPN
  that joins to a served record.
- **G2 missing fold membership** — the claim row is not present in the export
  pinned by `fold_manifest.json` + `cube.json`.
- **G3 unsupported MCU feed lane** — the MCU lane has no spine claim-batch
  format; claims exist only as staged pillar records, so no fold lane carries
  them.
- **G4 stale release** — the claim's fold/release predates the claim (or the
  claim source precedes the release that would cover it).
- **G5 missing source resolution** — no browser-accessible authorized route
  resolves the claim's source artifact (sha + page → served page).

All 13 claims are HELD in the pilot because they failed the pilot's served
join; this ledger decomposes *why* each one is held.

## Ledger

| # | lane | part | field | doc sha256 (16) | src | G1 OPN | G2 fold | G3 MCU feed | G4 stale | G5 resolution |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | POWER | UNKNOWN | VDS Drain to Source Voltage | `5a81e58ac243c0f9` | present, quote PASS | **YES** — no part/OPN in batch | YES — no export row for this doc+axis | n/a | UNKNOWN | YES — no bound served PNG |
| 2 | POWER | UNKNOWN | tPZL/tPLZ Propagation Delay | `557562822614bec5` | present, quote PASS | **YES** — no part/OPN | YES — not in export | n/a | UNKNOWN | YES |
| 3 | POWER | UNKNOWN | Connector Type | `8938fb84c480ffb0` | present, quote PASS | **YES** — no part/OPN | YES — not in export | n/a | UNKNOWN | YES |
| 4 | POWER | UNKNOWN | Drain-source reverse voltage VSD | `d44f2098f8553487` | present, quote PASS | **YES** — no part/OPN | YES — not in export | n/a | UNKNOWN | YES |
| 5 | POWER | UNKNOWN | Pull-High Drive ON Resistance | `fb9d92704cc03377` | present, quote PASS | **YES** — no part/OPN | YES — not in export | n/a | UNKNOWN | YES |
| 6 | MCU | 00001561E | n_gpio | `3d50372dd19b6208` | present, quote PASS | **YES** — slug/OPN not in export | **YES** — staged pillar, not folded | **YES** — no MCU spine lane | UNKNOWN | YES |
| 7 | MCU | MSPM0G3507-Q1 | flash_kb | `c44df29a9981ebcd` | present, quote PASS | **YES** — OPN not in export | **YES** | **YES** | UNKNOWN | YES |
| 8 | MCU | fpf1039-d | temp_range_c | `f5739109445cd62b` | present, quote PASS | **YES** — slug, not a join key | **YES** | **YES** | UNKNOWN | YES |
| 9 | MCU | infineon-psoc-4-psoc-4100-family-datasheet | flash_kb | `96f5358c3c46932a` | present, quote PASS | **YES** — family slug, ambiguous | **YES** | **YES** | UNKNOWN | YES |
| 10 | MCU | nxp-cbtl06dp213 | esd_hbm_v | `395da9dcbb641c14` | present, quote PASS | **YES** — slug | **YES** | **YES** | UNKNOWN | YES |
| 11 | MCU | onsemi-74lcx08-d | esd_hbm_v | `4f03c3eca3840aad` | present, quote PASS | **YES** — slug | **YES** | **YES** | UNKNOWN | YES |
| 12 | MCU | silabs-efr32bg24-datasheet | n_gpio | `c504de69a4db88f4` | present, quote PASS | **YES** — slug | **YES** | **YES** | UNKNOWN | YES |
| 13 | MCU | ti-sdaa486-pdf-ts-1791471178438 | sram_kb | `f80c89f69321e0a9` | present, quote PASS | **YES** — filename-shaped, not an OPN | **YES** | **YES** | UNKNOWN | YES |

## Per-gap summary (independent counts, never summed)

- **G1 missing OPN identity:** 13 / 13.
  - POWER (5): the move-batch rows carry no part/OPN field at all (the pilot
    recorded `identity.part = UNKNOWN`, join UNKNOWN).
  - MCU (8): the "part" is a staged file slug or filename
    (`infineon-psoc-4-psoc-4100-family-datasheet`, `ti-sdaa486-pdf-ts-…`), which
    is not an exact manufacturer+OPN that joins a served record. Family slugs
    are additionally ambiguous (G1 + ambiguity).
- **G2 missing fold membership:** 13 / 13. None of these claim rows appear in
  the export pinned by `fold_manifest.json` (`cube.sha256 =
  fe0f85c90e000583d7912dd6ae895d0263ba0954d0eb579d6f3c8df0bc327c5a`).
- **G3 unsupported MCU feed lane:** 8 / 13 (all MCU). The MCU claims exist only
  as per-part staged pillar records under
  `nas://m5-4tb/staging/datasheet_extractions/mcu_family_content`; there is no
  MCU spine claim-batch lane and no fold path for them. POWER claims do not
  have this gap (POWER has a batch lane; they still fail G1/G2).
- **G4 stale release:** UNKNOWN for all 13. Deciding "stale" requires an
  approval/release timeline that does not exist in the pinned inputs
  (no approval record; deployments API empty). Recorded UNKNOWN, never
  asserted.
- **G5 missing source resolution:** 13 / 13. No browser-accessible authorized
  route resolves any of these claim artifacts. The FE bundle's only claim-bound
  served evidence (`public/labs/r2/evidence/<sha>-<page>.png`) covers the
  LMR14030 R2 fixture documents
  (`59a9293668bd4bcb…`, `60fab340a8d414e2…`, `ae39c9952368cb88…`) — none of
  which are among these 13 doc shas. The deployed site is auth-gated (every
  lab asset path 307-redirects to `/login`), so even a present artifact would be
  UNKNOWN, not clickable.

## Cross-check against Task A

The Task A cohort (10 FE-bundle claims, `doc_sha_overlap` selection) is drawn
from the FE bundle's OPN rows, a different lineage from these 13 pilot HELD
claims (which come from the export/POWER-batch + staged-MCU stores). The
overlap between the two sets is empty: the pilot HELD doc shas do not appear in
the Task A cohort. Both sets independently converge on the same two
systemic blockers: no exact OPN identity into a served record (G1) and no
claim-bound served resolution (G5).

## Standing (unchanged)

No repair, no promotion, no model substitution. All source docs are present in
vault CAS (`nas://m5-4tb/vault/cas`); the blockers are identity, fold coverage,
and served resolution — not missing source bytes.