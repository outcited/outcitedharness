# CURVE-08 Report — Evidence-Grounded Engineering Decision Engine

Branch `curve/08-decision-engine` @ (this commit), base
`curve/07e-supply@f08d365b`. Baseline assertions independently confirmed
before implementation (bundle v3 rows/SHA, 100/104 bound + 4 ambiguity
refusals, 2 cross-manufacturer cells, 0 approvals, probes 16/16).

## What the engine answers today

Question (PRD example): *"buck at 48 V in, 5 V out, 0.5 A — which
families, how do efficiency curves compare under matching conditions,
what next?"*

- **Candidates:** 21 (16 catalog OPNs + 4 SiC46x devices from admission
  packets + 1 family node), carrying 123 evidence rows — evidence never
  inflates candidate counts.
- **Hard eligibility first:** 14 eligible, 6 ineligible with exact rules
  (e.g. `TPS548C26: VIN rating 16.0 V must be >= required 48.0 V`), 1
  unknown (family node, missing ratings — retained, never eliminated).
- **Condition-matched comparison @0.5 A:** SiC461 93.94 %, SiC463
  92.96 %, SiC464 92.95 %, LM5161 77.95 % — regenerated from immutable
  evidence ids (frozen test asserts the four PRD target values), each
  entry disclosing supporting points, method, matched conditions,
  uncertainty, limitations, source SHA/page/figure, and
  `human_review_pending`.
- **Comparison level:** `cross_manufacturer` (LM5161 vs SiC46x), reported
  through the five-level vocabulary, never merged.
- **At 3 A:** LM5161 (1 A) and SiC464 (2 A) hard-ineligible before any
  curve is consulted; ranking contains only rating-eligible devices.
- **Unknowns surfaced:** thermal feasibility stays
  `curve_evidence_advisory`/`unconfirmed`; family node ratings missing;
  SiC462's 48 V trace support starts at 1.03 A so 0.5 A refuses with a
  reason.
- **Approximate mode:** engineer-requested scenario comparisons appear in
  a separate labeled list with their mismatched/missing dimensions; the
  condition-matched list is byte-identical with or without the flag.

## Verification

- `tests/test_curve08.py`: 16/16.
- Full harness suite: **1432 passed** (base 1416 + 16).
- Pilot scorer and CURVE-05B/06/07E suites unchanged and green.
- v1/v2 bundles byte-identical (frozen test); bundle v3 SHA-verified at
  every load.

## Deliverables

- `CURVE_08_ARCHITECTURE.md` (R0 inventory, contracts, identity flow,
  invariants, ownership boundaries, acceptance tests)
- `harness/electronics/candidates.py`, `eligibility.py`,
  `decision_engine.py`
- `tests/fixtures/gold/curve_evidence_pilot/_catalog_slice.json`
  (frozen, provenance-stamped M4 catalog slice; live catalog untouched)
- `tests/test_curve08.py`

## Not done / handed forward

1. MCU and connector candidate paths: contracts designed (grain +
   typed-value vocabulary shared with FACET-02) but data incomplete —
   declared, not faked.
2. Approximation models (thermal, scenario efficiency) remain
   unimplemented by design; scenario mode discloses mismatches without
   inventing values.
3. FACET-02 merge: `to_facet_candidate()` is the reconciliation seam;
   their branch stays untouched.
4. Human adjudication still 0/123 — every value advisory.
