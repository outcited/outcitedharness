# CONTEXT-FIRST INTEGRATION — Receipt B — 2026-10-18

**Commit:** this commit (`agent/power-data-01`, on top of `d98d2009`).
No production cutover, no paid calls (TP4 local only), no release changes.

## 1. Modified call sites (isolated, reviewable)

| file | role |
|---|---|
| `scripts/vision_envelope.py` | v2 source-bound envelope: real verified doc SHA, page + coordinate system, figure/table identity, bbox + region id, caption+locator, axes/units/ticks/scale, legend + printed conditions with locators, source-derived package token, caller identity marked `hint_not_verified_applicability`, evidence method + version, per-field `ok/unknown/missing` status — **nothing fabricated**; `trust_policy` = source-derived evidence, conflict → HOLD/REVIEW (model never told to trust blindly) |
| `scripts/vision_router.py` | smallest-seam routing: deterministic-first (vector extractor passes its own bars → **no vision call**), tight-crop baseline default, full-page/difficult → envelope, raster/identity → locator-filtered envelope (only ok+located fields); completeness flags recorded |
| `scripts/vision_context_experiment.py` | controlled A/B harness |
| `scripts/map_context_canary.py` + `scripts/empty_classifier.py` | empty-population classification + staged recovery |

Production call paths were READ first (`cloud_vision_direct.py` prompt,
`local_prompt`/`focused_page_context`, `run_datasheet_structural_extraction`);
they are **not modified** — the patch is presented for review, execution
stays with the production owner (Happy) per the boundary.

## 2. A/B experiment (local TP4, 3 interleaved reps/arm, same docs/
images/decoding; vector ground truth; strict 5%-span usable bar)

| class | A blind | B envelope |
|---|---|---|
| tight crop (2 vector-GT cases, 12 reps) | 0/12 usable · RMSE 0.30–0.99 · **42 false-supported** | **7/12 usable · RMSE 0.034–0.042** · 9 false-supported · figure-selection 8/8 |
| full-page multi-figure | 0/6 · RMSE 2.12 | **4/6 · RMSE 0.025** |
| misleading repeated labels | 0/3 (empty) | 0/3 (right figure named, digitized badly, rmse 9.5) — **regression class: router already routes to deterministic tier (rmse 0.0); envelope alone is not trusted here** |
| empty / raster / partial controls | 3/3 correct-empty each | 3/3 correct-empty each (zero false results) |
| deterministic tier (vector available) | — | RMSE 0.0, 5/5 + 1/1 series, 0 s inference, 0 GPU |

- **Source/figure selection:** envelope 8/8 correct figure numbers; blind mixed.
- **Numerical accuracy:** envelope 6–20× lower error on tight/fullpage.
- **False SUPPORTED:** blind fabricated heavily (50 series over the 5% bar
  across tight+fullpage); envelope far less (13) and all within named
  classes; misleading-class bad reads are contained by the deterministic route.
- **Latency:** envelope build ≈ ms (reported separately in results JSON);
  inference mixed (B −4%…+50%) — no systematic cost beyond prompt length.
- **Regression check (directive rule):** on tight crops the envelope is
  strictly better under repeats (supersedes the earlier n=1 "no gain"
  reading). The one regression class (misleading multi-figure) keeps the
  deterministic/hold-for-review route.

## 3. Empty-document recovery (staged canary, no widening)

Population classified deterministically (5,141 empties at measurement
time): **1,097 confirmed_recoverable** (Python baseline proved claims
exist), 3,538 primary, 257 secondary, 128 wrong-doc-class, 56
raster/partial, 66 no-table-signals.

Staged canary — **same 25 doc identities**, exact original failure reasons
preserved (`no_claims`), only change = deterministic context map added:

| run | recovered | gate pass | notes |
|---|---|---|---|
| v1 (6k max_tokens) | 6/25 | 343/346 (99.1%) | 19 `no_json_array` = **truncation artifact**, not model failure |
| v2 (12k + salvage parser) | **25/25 (100%)** | **1,577/1,653 (95.4%)**, 76 P0 | all real recovery |

**Full-processing estimate** (1,097 confirmed-recoverable): ≈ 1,097 ×
~7 min ≈ **5–7 h of free TP4** at 1-2 concurrent (bounded), $0; expected
recovery ~95-100% of the class at 95%+ gate precision. Primary class
(3,538) needs the same canary before any widening — NOT started.

## 4. Rollback behavior

Router/envelope are additive wrappers: deleting the two scripts (or the
prompt-augmentation flag) restores the exact prior behavior — the bare
prompt path is byte-identical to `cloud_vision_direct.PROMPT`. No shared
state; no production writes; claims verification/promotion rules
untouched. Evidence: `scratch/m5-soak/{context-ab-run2.log,
map-context-canary-v2.json}`.

## 5. Verdict: READY FOR A LARGER PILOT (staged, review-gated)

Ready: envelope+router on the map-context text route (100% recovery,
95%+ gate precision) and on vision full-page/raster/tight classes with
the misleading-class carve-out. NOT ready without review: production
call-site patch (owner = Happy), the 3,538-doc primary expansion, and
anything customer-facing (adjudication gates unchanged).

Outstanding externals (recorded, not blocking): cr-core SiC46x + 7,181
OPN packet · M4 bundle v4 · Agent 2 ack · Seven ingest (receipt posted,
comment 6252460816).
