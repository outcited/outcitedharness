# CURVE-05A — qualification & evidence readiness report

Branch `curve/05a-qualification` (worktree `/Users/samkim/Harnessv1-curve04`),
base `curve/04-adjudication-raster@492bbdff`. No production changes, no
gold promotion, no other agents' worktrees touched.

## Starting-state verification (independently re-measured)

| Reported | Verified | Note |
| --- | --- | --- |
| 81 packets, 0 human-approved | 81 / 0 | ledger replay |
| 59 indexed curves | **81 rows** (59 with phenomena + 22 phenomenon-null) | the "59" was the with-phenomenon stat; the DB holds 81 rows — recorded as a discrepancy |
| TLS raster partial | 2 plots at 300 and 600 DPI | confirmed |
| 300 DPI 5/6, median 1.9 %; 600 DPI 2/6, median 1.45 % | 300 confirmed; **600 result was a detector artifact** (see WS1) | after the fix 600 = 5/6, median 1.92 % |
| 1,384 tests | 1,384 at base | plus CURVE-05A tests below |

## Implemented changes (actual)

1. **Raster frame-cap fix** (`harness/electronics/raster_curves.py`):
   the `[:60]` candidate cap cut bottom-of-page frames at 600 DPI — the
   direct cause of "600 loses plots". Raised to 240; 600 DPI now equals
   300 DPI in coverage and error at 4.6× compute (30.1 s vs 6.5 s per
   page). Resolution choice stays evidence-based: **300 DPI operating
   point, 600 DPI reserved for selective crops of disputed figures**.
2. **Stage-separated audit** (`scripts/audit_raster_stages.py` →
   `results/curve-05a/stage_audit.json`): per stage × DPI × plot on the
   frozen page — detection 6/6 at all DPIs; the axis (tick-OCR) stage is
   the sole remaining loss (5/6; single-digit y labels); series stage
   limited to colored traces (saturation clustering — black traces are a
   recorded gap); conditions text-layer-only in the raster lane.
3. **Legend-frequency dimension law**: unpinned fsw legends ("600 kHz"
   series names) are now flagged in `condition_dimensions_unpinned` —
   previously invisible to the comparison contract.
4. **Cold-review packets** (`scripts/emit_cold_review.py` →
   `results/curve-05a/cold-review/`): 81 series packets, reviewer-first
   ordering (render + printed conditions + blind checklist before the
   sealed extraction), all `human_review_pending`; plus Infineon
   coverage from the held TLS4125D0EPV raster recovery (2 plots,
   text-layer conditions verbatim) — **42 independent power plots, 12
   device families, 3 manufacturers** (TI, Vishay, Infineon-raster),
   subject to held documents and acquisition blocks recorded in
   CURVE-04's challenge manifest.
5. **Comparison stress suite** (`tests/test_curve05a_comparison.py`):
   12 cases covering all eleven PRD challenge types; every refusal
   carries an explicit reason; typical-never-guaranteed asserted at
   query, comparison, and assumption layers.
6. **M4 evidence bundle** (`scripts/build_m4_evidence_bundle.py` →
   `tests/fixtures/m4-handoff/`): release
   `curve-evidence-bundle-v1-5cc1a6e41ad8`, 81 advisory rows carrying
   every contract field (evidence_id, source SHA + revision, locator,
   applicability + coverage_kind, phenomenon, quantity/units, conditions
   + missing dimensions, operating-point method + example, uncertainty,
   evidence class, adjudication state, comparability rules, release IDs,
   full points). Pinned fixtures only — **M4 never needs M5's SQLite
   databases**; operating points are recomputable from row data.
7. **Frozen evaluation** (`scripts/run_curve05a_evaluation.py` →
   `results/curve-05a/evaluation.json`): aggregates scorer, stage audit,
   stress suite, and ablation with numerators/denominators and
   per-manufacturer/per-class breakdowns.

## Test results

- `tests/test_curve05a_comparison.py`: **12/12**
- pilot scorer: **16/16 probes** (false comparability 0.0, out-of-range
  rejection 1.0)
- frozen evaluation gates: pass (condition accuracy 1.0 on the probe
  suite, stress 12/12, 0 unsupported claims)
- full suite after changes: see commit message (regression suite green;
  no extraction-pipeline regressions — vector extractor untouched except
  no changes; raster lane change is additive)

## Figure-level qualification results

- **Vector (deterministic ground truth)**: precision 1.000 (40/40
  extracted plots match independently transcribed captions); coverage
  0.571 (40/70 printed figures — misses counted, listed, reasons
  recorded: legend-vector-text 35, axis-fit 11, no-vector-curves 11,
  points-outside-axis 4); tick-fit residual ≤ 0.014 % of span.
- **Raster (same-page comparison)**: 300 DPI 5/6 plots, median 1.91 %
  span, p95 18.4 %; 600 DPI 5/6, median 1.92 %; 200 DPI 4/6, 2.35 %.
  Tails are dominated by cross-substrate series rank-pairing; series
  names unbound (legend OCR pending). The PRD's warning is honored: 600
  DPI is NOT selected for its lower subset error — parity at 4.6× cost
  loses; 300 DPI stays the operating point.
- **Infineon raster**: TLS4125D0EPV p.18 — 2 of 4 efficiency plots
  recovered (axis-coherent), conditions verbatim from the text layer,
  machine-reference-only.

## Unresolved limitations

1. 0 human approvals (no image-capable reviewer in this environment);
   cold-review packets complete and pending.
2. Black/grayscale traces unrecovered by the raster lane (saturation
   filter); legend OCR not implemented → raster series unnamed.
3. Single-digit y-tick labels defeat OCR at all tested DPIs (1 plot).
4. Four challenge documents remain network-blocked (CURVE-04 manifest).
5. Retrieval index rows: 22 phenomenon-null rows (unclassified
   quantities) — usable only by direct curve_id, excluded from
   phenomenon queries.

## Evidence artifact manifest

- `tests/fixtures/m4-handoff/curve_evidence_bundle.jsonl` (81 rows) +
  `release_manifest.json` (release `curve-evidence-bundle-v1-5cc1a6e41ad8`,
  bundle SHA recorded)
- `results/curve-adjudication/` — 81 packets, ledger (append-only)
- `results/curve-05a/cold-review/` — 81 series packets + TLS raster
  record + summary
- `results/curve-05a/stage_audit.json`, `evaluation.json`
- `results/curve-04-challenges/challenge_manifest.json`,
  `results/curve-04-raster/resolution_experiment.json`,
  `results/curve-04-ablation/ablation.json`

## M4 handoff instructions

1. Pin the fixture directory `tests/fixtures/m4-handoff/`; treat
   `release_manifest.json` as the version anchor (any row change mints a
   new release digest).
2. Every row is advisory: `advisory_only: true`, `guarantee: false`,
   evidence class carried; refuse comparisons whose stated conditions
   are absent or mismatched (`conditions`, `missing_dimensions`).
3. Operating points: recompute from `row.curve.points` with
   `row.operating_point_query.method` (linear or log10 interpolation,
   strictly inside `supported_region`; extrapolation forbidden).
4. Adjudication: rows are machine-verified only; filter or flag by
   `adjudication.state` — nothing is human gold yet.
5. Comparability verdicts and refusal reasons follow CURVE-02 laws;
   examples in `results/curve-04-ablation/ablation.json`.

## Recommended next categories for curve-driven discovery

1. **MOSFETs** — RDS(on) vs VGS/ID and SOA/switching-loss curves: the
   corpus is deepest there (4,000+ Infineon parts) and family-level
   tradeoffs (conduction vs switching loss) are exactly curve-shaped.
2. **LDOs** — dropout vs load and PSRR vs frequency (log axes): small
   plot count per datasheet, high decision value at family level.
3. **Gate drivers** — propagation delay vs supply/temperature: modest
   corpus, simple legends, good third category to prove generality.

Each supplies its own semantics under the same contracts — no DC-DC
knives required.
