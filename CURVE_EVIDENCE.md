# Curve evidence lane — gap review and promotion policy (PRD-CURVE-02)

Status: pilot implemented and green (34 new tests; frozen pilot gates at
100%). Owner: M5 curve / vision extraction lane.

## 1. Gap review of the existing curve lane (deliverable 1)

Inspected: `harness/electronics/typical_curves.py`,
`scripts/score_typical_curves_gold.py`, `scripts/extract_typical_curves.py`,
`scripts/build_curve_queue.py`, `harness/electronics/condition_model.py`,
`harness/electronics/claims.py` + `models.py` (claim/provenance schemas),
`harness/discovery/` (Intent-to-Knife laboratory: designs → requirements →
discover → ledger → next-question), gold fixtures
`tests/fixtures/gold/typical_curves/` and the wave-A burn rows
(`typical-curves-wave-a-20261006`: 3,010 rows, 1,738 `extracted`).

What the lane already had (kept, not duplicated):

- plot-region detection (caption-seeded + vector-frame, crop-don't-page);
- vision digitization contract (`typical_characteristics`) with grounding
  (labels must exist in the page word list) and the EC-table anchor check;
- wave-A verdict rows (`extracted` / `hold_ungrounded_axis` /
  `hold_anchor_disagreement` / `rejected_structure`) with provenance
  (document sha, page, figure, bbox, render sha);
- hand-labeled gold + acceptance scorer (axis exact ≥ 0.95, series-count
  ≥ 0.90, point error ≤ 2 % of y-span);
- the typed printed-condition model (`parse_condition` / `condition_covers`)
  and the discovery elimination ledger with its UNKNOWN-never-eliminates
  law.

Gaps against PRD-CURVE-02 — none of these existed before:

| PRD | Gap |
| --- | --- |
| R1 | No typed curve evidence object: rows carried payload + verdict but no quantity/unit normalization, evidence class, applicability, uncertainty, or verification-aware contract. |
| R2 | No curve condition semantics: conditions lived in free text; nothing distinguished fixed conditions from the swept x variable; no categorical conditions (MODE = FCCM, VCC bias); no not_comparable contract. |
| R3 | No operating-point query. `anchor_check._interpolate` even clamps silently at the ends — the opposite of the no-extrapolation law. |
| R4 | No derived comparisons; no citation/method/assumption/uncertainty bundle; nothing marked proposals. |
| R5 | No relevance tagging; curves were not discoverable by engineering use case (e.g. light-load efficiency). |
| R6 | Gold held 3 plots / 14 series (MCU aisle only); no Power pilot, no condition-compatibility or out-of-range evaluation. |
| R7 | The discovery API had no evidence provider; curves could not answer design questions. |

## 2. What was built

- `harness/electronics/curve_evidence.py` — the decision-grade layer
  (R1–R5). `CurveEvidence` upgrades wave-A rows or frozen reference plots;
  typed numeric conditions reuse `condition_model.parse_condition` behind a
  thin alias map (PVIN→VIN, ƒS→FSW, EN→VEN) plus categorical capture
  (verbatim-normalized, exact-equality). Laws enforced in code:
  - **no silent defaults** — `condition_compatibility` returns
    `not_comparable` with `condition_missing` / `condition_mismatch` /
    `condition_conflict`; `condition_sufficiency` refuses comparisons whose
    phenomenon-required conditions the engineer did not state;
  - **no extrapolation** — `query_operating_point` interpolates strictly
    inside the sampled support (log-space aware), returns `out_of_range`
    otherwise; resolution (median sample spacing) rides every value;
  - **typical ≠ guarantee** — evidence class carried end-to-end,
    `guarantee: false` on every computed result, comparisons are
    `status: proposal`;
  - **sweep exclusion** — the x-axis quantity is not a fixed condition;
    legend temperatures override page-level defaults (recorded, not
    silent).
- `scripts/extract_vector_curve_references.py` — deterministic
  gold-building tool: recovers curve points from vector PDF geometry
  (tick-anchored affine maps, colored polyline traces, swatch→legend
  matching). A labeling tool, not a runtime digitizer; fail-closed per
  plot.
- `harness/discovery/curves.py` + `POST /designs/{id}/curve-evidence` —
  the R7 evidence-query contract (applicable curves, matched conditions,
  supported region, computed values + uncertainty, citations, reasons a
  curve cannot be used, `promotion: "none"`).
- `tests/fixtures/gold/curve_evidence_pilot/` + `scripts/score_curve_evidence_pilot.py`
  — the frozen pilot (deliverable 5) and its gated scorer.
- Tests: `tests/test_curve_evidence.py` (R1–R5, R7 laws),
  `tests/test_curve_evidence_pilot.py` (frozen evaluation in CI),
  `tests/test_discovery_api.py::test_curve_evidence_route_is_advisory_only`.

## 3. Condition-aware comparability rules (deliverable 3)

For a comparison to run, ALL must hold:

1. the engineer stated every phenomenon-required condition key
   (`efficiency_vs_load` → `vin_v`, `vout_v`; otherwise
   `insufficient` + the missing keys are named);
2. every stated numeric key the curve also prints agrees (range overlap,
   same semantics as `condition_covers`); disagreement →
   `not_comparable/condition_mismatch` with both values;
3. categorical conditions compare by exact normalized equality
   (`MODE = FCCM` ≠ `MODE = DCM`); an unpinned categorical dimension in a
   multi-curve comparison is flagged (`condition_dimensions_unpinned`),
   never averaged over;
4. a repeated condition key with conflicting values →
   `condition_conflict` (fail-closed);
5. the swept x variable is excluded from fixed conditions; series-legend
   conditions (temperatures) override page-level defaults and the override
   is recorded.

Examples honored from the PRD: efficiency only at matched VIN/VOUT (and
mode/fsw when the legend splits them); legend-temperature series selected
per stated TA; profiles refused whole when any segment leaves the support.

## 4. Frozen pilot (deliverable 5, R6)

One Power subcategory: **DC-DC converters** (`power.dcdc`), 11 plots /
38 curves (TPS548C26 SLVSGM2 p.10: 6 figures × 4 fsw series with
PVIN/VCC/VOUT/MODE condition variants; LMR33610 SNVSBI9A p.8: 5 figures ×
2–3 legend-temperature series with EN/VFB/VOUT conditions and difficult
rotated labels, part-range legends and page-level defaults), plus the 14
MCU hand-labeled gold curves carried in for extraction metrics = 52
curves. Probes: 16 (5 must-reject false-comparability traps, 3
out-of-range traps).

Reference provenance is honest and two-tier: the 24 TPS + 14 LMR curves
are vector-extracted deterministically (tick-anchored affine fit,
residual-checked; legend swatch→text matched) and are marked
`verification: reference — human sign-off pending` in every record; the 14
MCU curves remain fully hand-labeled. Run:

```
.venv/bin/python scripts/score_curve_evidence_pilot.py            # decision-grade gates
.venv/bin/python scripts/score_curve_evidence_pilot.py --predictions ROWS.jsonl
```

Current results: plot identification 11/11, axis/unit correctness 1.00,
series identification 1.00, evidence class 1.00, printed-condition capture
1.00, condition-classification accuracy 16/16, **false comparability 0.00**,
out-of-range correct rejection 1.00. Extraction metrics (vision vs
references) report as `not_run` until `--predictions` is supplied — the
wave-A vision lane owns that measurement.

## 5. Integration contract (deliverable 6, R7)

`POST /designs/{id}/curve-evidence`
`{phenomenon|quantity|use_case, operating_point:{x}, conditions,
load_profile?, category?}` → applicable curves, matched conditions,
supported region, computed values (bounded, cited, uncertainty-bounded),
`derived` comparison proposal, and `not_usable`/`insufficient_conditions`
with reasons. Responses always carry `"promotion": "none"`. The route is
wired into the Intent-to-Knife laboratory as a potential evidence provider
only — see the policy below for the promotion path.

## 6. Promotion-policy proposal (deliverable 7)

Curve evidence may NEVER become a hard knife by itself. Proposed aisle
policy for ever admitting curve-derived statements into elimination rules:

1. **Eligibility** — curve row `verdict: extracted` with grounding pass
   AND anchor agreement ≥ 0.8 (or `verification: reference` signed off by
   a human against the frozen render), from a document in the pinned
   corpus release.
2. **Condition law** — the knife states its conditions; the evaluator
   applies the same `condition_covers` mismatch-is-UNKNOWN law as table
   claims. Typical curves can at most produce SOFT (scoring) evidence.
3. **Direction** — curves may only relax, never eliminate: a typical
   curve below a hard requirement cannot FAIL a part (the vendor may have
   updated the plot); a typical curve far above requirement may raise a
   NEAR_MISS flag for review. Guaranteed/minimum-class plots (rare) would
   need per-axis aisle approval.
4. **Bounded values only** — no interpolated value may cross the sampled
   support; resolution must exceed the decision margin by 3×, else the
   value is advisory text, not a number.
5. **Qualification** — before any production knife, a frozen evaluation
   (this pilot's scorer extended to the aisle's parts) must show 0.0 false
   comparability and 100 % out-of-range rejection on ≥ 30 hand-signed
   curves per subcategory — exactly the gates shipped here.
6. **Audit** — every promoted statement cites curve_id, document sha,
   page/figure/bbox, figure revision, evidence class, method
   (`linear_interpolation_on_digitized_polyline`), and the proposal
   artifact it graduated from.

## 7. Non-goals honored

No full-corpus re-extraction (pilot is 2 pages + existing gold); no
fabricated measurements (fail-closed everywhere); no assumed conditions
(sufficiency gate); no BOM reconstruction or thermal simulation; no
production knife promotion (advisory provider only).

## 8. Open items

- Human sign-off of the 38 vector-referenced pilot curves against the
  frozen renders (the records say so; the scorer cannot).
- Vision extraction metrics vs the power references (needs a wave-A run
  over the two pilot pages with `--cloud`).
- Curve-ID persistence: the evidence layer computes IDs from
  (document sha, page, figure, series); a catalog table would give them
  stable lifecycle tracking across corpus releases.
