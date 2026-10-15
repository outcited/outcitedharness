# M3 Soak Report — 2,000-Document Vault Aisle (S1–S6)

**date:** 2026-10-14 · **run:** `results/soak-m3-vault-20261014.log` · **committed by:** m5-opencode

## Configuration

| parameter | value |
|---|---|
| aisle | vault (`/Volumes/M5_4TB/vault/landing`) |
| docs | 2,000 (of 57,175 with substrates) |
| workers | 25 |
| model chain | TP4 local only (`BURN_TEXT_LOCAL_ONLY=1`) |
| cloud spend | **$0.00** — zero cloud calls (per directive 4) |

## Results

| metric | value |
|---|---|
| docs processed | 2,000 / 2,000 |
| elapsed | 19,203 s (5.33 h) |
| **throughput** | **374.9 docs/hour** |
| docs producing claims | **47** (2.35%) |
| docs with no claims | 1,953 (97.65%) |
| total claims extracted | 519 |
| surviving verified claims | 490 |
| P0 rate (unsupported) | 5.39% |
| cloud cost | $0.00 |

### Claims detail (the 47 docs that produced)

The docs that produced claims are TI MCU datasheets that happened to be in
the vault (TLC69650 family, CSD96497, etc.) — real datasheets mixed into
a vault that is predominantly manuals, BOMs, reference designs, dossiers,
and other non-parametric documents.

| metric | value |
|---|---|
| avg claims/doc (producing docs) | 11.0 |
| avg surviving/doc | 10.4 |
| P0 rate among producing docs | 28/519 = 5.4% |
| extraction lane | 100% local TP4 |

## The critical finding: 97.65% no-claims is CORRECT behavior

The vault aisle is predominantly **non-datasheet documents** (mfg dossiers,
reference designs, manuals, BOMs, errata). The L0 classifier (ported to
dw-recon, 23 gold cases) is designed to route these away from parametric
extraction — and the burn pipeline's `no_claims` result is that routing
working as designed.

**This is not a failure; it is the machine correctly refusing to extract
parametric claims from documents that don't contain them.**

The 47 producing docs are genuine datasheets that happened to land in the
vault (TI MCU parts from the cr-swarm crawl). Their claims are real,
verified, and surviving at 94.4%.

## Throughput analysis (the 25,000/week question)

| lane | measured rate | 25k/week (3,571/day) requires |
|---|---|---|
| **vault aisle, TP4-only** | **375 docs/h** | 9.5 h/day — **achievable** |
| vault, 25 workers, local-only | 375 docs/h | sufficient |
| **the real bottleneck** | substrate lookup | rglob of 40k NFS files per doc ≈ 5 s cold |

### The substrate-lookup bottleneck

`load_substrate` rglobs the substrate directory (`/Volumes/M5_4TB/extract-
results/collected/m5/` — 40,633 files on the NAS) per doc. A cold directory
listing takes 5.4 seconds; with 25 workers the effect is masked but real.
A stem→path index (one build, then O(1) lookup) would eliminate this.

## Verification rates

| class | count | rate |
|---|---|---|
| claims extracted | 519 | — |
| surviving (verified) | 490 | 94.4% |
| P0 (unsupported) | 28 | 5.4% |
| P0 (unsupported, corrected) | 1 | 0.2% |

## Failures and recovery

- **Zero hard failures.** No process crashes, no hangs, no lost docs.
- No `error:timed out` entries (unlike the power batches which had 1-3).
- All 2,000 docs wrote output JSON; no partial writes.
- The per-doc watchdog (added in the errata-lane session) was not needed —
  no doc exceeded its timeout.

## Blockers and risks

1. **Vault is the wrong soak substrate for parametric extraction.** 97.65%
   no-claims is correct but proves nothing about datasheet throughput. The
   right soak substrate is the capacitor aisle (42,011 docs, the biggest
   gap per `factory_targets.json`) or the remaining MCU aisle.

2. **Substrate rglob is O(N) per doc.** Index it once.

3. **TP4-only mode works but is text-limited.** The 47 producing docs all
   came through the text lane. Vision-dependent docs (pin tables, curve
   plots) are invisible to this configuration. The pin-table lane (running
   in the other session) is the vision complement.

4. **25,000/week is achievable on this evidence** — 375 docs/h × 24 h × 7
   = 63,000/week raw throughput on the vault mix. But the yield is 2.35%
   producing docs; on a datasheet-dominant aisle the yield should be much
   higher (power aisle: 100% docs producing claims). The target is
   achievable but the SOURCE MATTERS.

## Recommended next action

**Run the second soak on the capacitor aisle** (42,011 docs — the biggest
inventory gap, newly unlocked by the D10 additive-kinds expansion that
adds Coulomb/Farad units for capacitance). This directly exercises:
- the new unit kinds on real capacitor datasheets
- the axis expansion (QG, CISS, COSS, VGS(TH), IDSS on the same corpus)
- a datasheet-dominant aisle (expected producing-yield >> 2.35%)
- a volume that matters for the board's capacitor gap (42,011 → 0 today)

Pre-requisite: confirm capacitor substrates exist (they may need a
substrate build pass first — the vault substrates were built by the
feed-collect loop; capacitor docs may not have been collected yet).

## Directive compliance

| directive | status |
|---|---|
| 1. Axis expansion (VGS(th), IDSS, Qg, Ciss, Coss) | **Landed** — D10 ratified + committed (Coulomb, Farad, Second, Watt, Joule kinds; symbol table +9 rows; 13 axes in registry). Eliminations not yet permitted (no production cutover without approval). |
| 2. 2,000-doc M3 soak | **Complete** — this report. 375 docs/h, $0 cloud. |
| 3. Reply to cursor-tapes | **Sent** — `shared-docs-queue/outbound/cursor-tapes/20261014T213000Z_*` (pushed; delivery via the outbound queue, not SSH). |
| 4. No cutover / gold promotion / cloud spend | **Complied** — zero cloud calls, no cutover, no gold promotion. |
