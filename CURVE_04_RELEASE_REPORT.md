# CURVE-04 — release report

Branch `curve/04-adjudication-raster` (worktree, base
`audit/curve-evidence-v1@28772cf1`). All gates below are asserted by
`tests/test_curve04.py` and the ablation/scorer runs; numerators,
denominators, and exclusions are stated explicitly.

## Gate results (R6)

| Gate | Result | Numerator / denominator |
| --- | --- | --- |
| Invented quotations | 0 | 0 invented / 66 curve packets' printed verbatim conditions (adjudication manifest) |
| False family-to-OPN expansion | 0 | applicability maps family→family in every packet; 0 widening events / 81 packets |
| Unsupported hard eliminations | 0 | 0 / 6 ablation questions (A/B eligibility identical; curves advisory) |
| Typical-as-guaranteed assertions | 0 | 0 / 21 comparable values across ablation + retrieval tests (`guarantee:false` asserted) |
| False condition comparability on challenge cases | 0 | 0 false / 3 no-defensible-comparison cases refused correctly (Q3, Q5, Q6) |
| Gold promotion without sign-off | 0 | 0 human_approved / 81 packets (all `human_review_pending`; machine cannot promote — state-machine tested) |
| Source traceability | 100 % | 21 / 21 comparable values carry doc SHA + page + figure + curve_id |
| Reproducibility | pass | packet hashes deterministic; index rebuild idempotent; renders SHA-recorded |
| Rollback | pass, tested | flag-off and missing-index both return explicit statuses (tests) |
| Raster improvement quantified | pass | 200/300/600 DPI measured (see architecture report); 300 DPI chosen, not assumed |
| Decision improvement | measured, review pending | B adds 1–12 cited values per question where evidence exists; expert relevance PENDING (not self-graded) |

Coverage failures stay visible: raster recovery 5/6 plots at 300 DPI
counts in the denominator; retrieval coverage gaps (Q3/Q6: 0 comparable)
are reported as refusals with reasons, never as zeros.

## Deliverables (R7)

1. `CURVE_04_ARCHITECTURE.md` — this release's architecture and gaps.
2. Immutable packets + ledger + reviewer workflow —
   `harness/electronics/curve_adjudication.py`,
   `scripts/adjudicate_curves.py`, `results/curve-adjudication/`
   (81 packets, renders, `ledger.jsonl`; CLI `emit|status|approve|reject|
   rework`).
3. Challenge manifest — `results/curve-04-challenges/`
   (SHA-addressed, timestamps, per-attempt failure reasons).
4. Raster lane + resolution experiments —
   `harness/electronics/raster_curves.py`,
   `scripts/run_raster_resolution_experiment.py`,
   `results/curve-04-raster/resolution_experiment.json`.
5. Experimental retrieval — `harness/electronics/curve_retrieval.py`
   (`CURVE_RETRIEVAL_ENABLED=1`), own DB, unit-store laws adopted,
   live search index untouched.
6. Ablation — `scripts/run_curve_ablation.py`,
   `results/curve-04-ablation/ablation.json`.
7. This report.

## Test results

`tests/test_curve04.py`: 15 passed (adjudication state machine +
tampering, synthetic raster ground truth + refusals + OCR-robust fits,
flag/rollback, match classes, extrapolation refusal, condition mismatch,
ablation gates, challenge manifest, adversarial provenance tamper).
CURVE-02/03 regression: existing suite green in this worktree
(see final report run). No production APIs changed; the discovery
curve-evidence route and pilot scorer are untouched.

## Limitations

- No human visual reviewer was available (no image input in this
  environment): every packet is review-ready, approval pending — no
  simulated sign-off.
- Four of five challenge documents could not be acquired (network egress
  filtered): recorded coverage gaps, not fabricated.
- Raster series names are not legend-bound yet (next lane step); pairing
  tails in the DPI experiment reflect that.
- The PRD's `search-release-v1-9c07373d690d` was not found in the live
  index's `releases` table (present: `…71adcae3c83f`); recorded rather
  than assumed.

## Next actions

1. Human review session over `results/curve-adjudication/` (renders +
   checklists included) → first human_approved gold.
2. Retry acquisitions from an unfiltered network; LT8292 first
   (log-axis gold).
3. On sign-off: migrate retrieval rows into the search indexer as
   figure-grain units (contract in the architecture report).
4. Legend OCR for the raster lane to bind series names and shrink
   pairing tails.
