# Pin Batch Completion Receipt — 2026-10-15

## Completion facts
- 125/125 OpenRouter batches completed and retrieved (inline results; retriever
  handles list-shape, commit `pin retriever: handle inline results lists`).
- Model: deepseek/deepseek-v4.1-flash (Sam policy), batch endpoint.
- Unique docs processed: 23,318 (fully reconciled against 23,318 unique
  custom_ids submitted; the "25,000" earlier figure included one duplicate
  chunk + resubmissions).
- Estimated final spend: ~$10 token cost (batch rates); the ~$49 submission
  reserve released at completion. No rerun of successful batches — retriever
  dedupes by custom_id.

## Results accounting (docs, not rows)
| Verdict | Docs | Disposition |
|---|---:|---|
| ok (verified pin tables) | 14,256 | landed to staging `pinout` facet |
| no_pin_table (legit) | 8,074 | terminal — model says none, no rows staged |
| held/unparseable | 834 | retry pool (subscription/TP4) |
| unverified (quotes failed) | 154 | retry pool (subscription/TP4) |
| **total** | **23,318** | **= unique submitted, 100% reconciled** |

- Never-submitted docs (prefilter pass, over my 25K cap): **9,956** → run via
  subscription lane / TP4 (free).
- TP4 pin lane: whole-queue worker (scans ALL 174,360 docs incl. no-signal;
  different denominator than the 33,274 prefiltered) — 18,900+ done, still
  running; its results merge first-wins with batch rows.
- Pin ROWS staged: 210,193 (each row verbatim-quote-verified against substrate
  at retrieval).

## Board delta (pre 20261014T202714Z → post 20261015T153719Z)
- MCU pinout parts: **2,858 (4.4%) → 17,826 (25.6%)** (+14,968)
- MCU addressable: 64,272 → 69,607; Total addressable: 121,695 → **127,030**
- Records: 171,551 → 301,986; Served store: 17,455 (export feeds on primary)
- Newly pinout-covered parts also present in served export: intersection
  pending promotion review (served feed refresh is part of the release step).

## QA sample (20 random ok docs, seed=42; qa-sample-20.json)
- **sha256: 18/18 verified** against source PDFs on the primary mirror.
- **Pin designators + names: 16/20 samples show N/N located on a single
  identified page**; 1 multi-page partial hits (8/11p, 6/11n); page numbers
  recorded per doc (p1–p20 range observed).
- **Complete vs partial reads: 7 COMPLETE (row count ≥ printed pin count),
  8 FULL(unverified-ncount — no printed count to compare), 3 PARTIAL
  (11/16 ×2 — 16-pin packages read 11 rows; 3/318 — regex artifact on a
  non-package count), 2 MISSING_PDF** (source-path drift for 2 docs — under
  investigation, not staged from this sample).
- **Package identity: verified on the pin-table page 10/18**; the 8 "FAIL"s
  are package strings printed off-page (page-1 headers) — a QA-check scope
  limitation flagged for the promotion review, not confirmed extraction errors.

## Guardrails honored
- No gold promotion, no release flip — staged only, awaiting review.
- Board snapshot: results/api-coverage/board/20261015T153719Z.json (+ BOARD.md).
