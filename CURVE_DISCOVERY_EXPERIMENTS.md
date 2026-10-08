# CURVE-03 — curve-driven design discovery: validation and experiments

Status: Phase 1 expanded validation complete (machine references, human
sign-off pending); Phases 2–4 experiments executed deterministically with
a full evidence ledger. Branch: `audit/curve-evidence-v1`. No production
changes; no automatic gold promotion.

## Phase 1 — expanded independent validation

**Sample.** 13 frozen pages, 40 extracted plots / 81 curve series across
11 device families (TPS548C26, LMR33610, LMR36502, TPS542941, TPS563203,
TPS628302, SiC448, SiC461–464) and 2 vector-extractable manufacturers
(Texas Instruments, Vishay), plus the 14 hand-labeled MCU gold curves.
Independent figures and devices are counted separately from series, per
the instruction.

**Verified candidates from the review** (resolved against the vault by
SHA — see `tests/fixtures/gold/curve_evidence_pilot/_sample_manifest.json`):

| device | status | reason |
| --- | --- | --- |
| TLS4125D0EPV (both held revisions) | held, refused | p.18 plot areas are raster images (3 drawings); titles/conditions are text-layer only. Deterministic tool refuses; needs the vision lane |
| IR3883, IR3447 | not held | absent from every manifest and cache; pending acquisition |
| ADP5003, LT8292 | not held | pending acquisition — LT8292 would provide the first real logarithmic-axis reference |

**Corpus boundary.** Scanned Microchip (14 docs), Murata (12), onsemi,
Infineon iPOL, Würth: all raster plots or vector-text ticks
(`axis_fit_failed`). A third manufacturer requires either acquiring the
reviewed ADI parts or extending the tool with a vector-text tick reader.

**Metrics with numbers, not pass/fail alone**
(`scripts/score_curve_evidence_pilot.py`):

- plot precision **1.000** (40/40 extracted plots match independently
  transcribed printed captions) — a wrong extraction never counts;
- plot coverage **0.571** (40 of 70 printed figures) — missing plots and
  series ARE penalized; misses listed per figure; skip reasons recorded
  (legend_unresolved 35, axis_fit_failed 11, no_vector_curves 11,
  points_outside_axis 4);
- axis/unit correctness 1.000, series identification 0.979, printed
  condition capture 1.000 (hand-expected pages);
- **measured numeric error**: tick-fit residual ≤ **0.014 % of axis span**
  (median 0.008 %) — interpolation error inherits this bound; point
  retention ≥ 0.973; points outside axis ≤ 9.5 % per kept plot;
- decision-grade probes **16/16**: false-comparability **0.00**,
  out-of-range correct rejection **1.00**, condition classification on
  temperature mismatches, mode mismatches (FCCM/DCM), unit conversions,
  sweep-variable queries, legend-vs-page-default overrides
  (`VIN = 12 V` legend beating `VIN = 13.5 V` default), envelope-vs-default
  resolution (`VIN = 2.25–5.5 V` envelope + "typical at 5 V").

**Challenge coverage** — all seven items exercised; see the manifest's
`challenge_coverage` block. Log axes: log10 tick fit + log-space
interpolation implemented and unit-tested; no real log-axis reference in
the extractable corpus (open item).

**Human-review status.** Machine references are NOT gold. Review packets
(render + reference + checklist per page) emit via
`scripts/emit_curve_review_packets.py` into `results/curve-review/`;
`signoff.json` gates the `human_signed` count, which is currently **0**.

## Phases 2–4 — A/B/C experiments

Cohort: 8 family rows frozen with verbatim title-page quotes
(`_cohort.json`). Runner: `scripts/run_curve_discovery_experiments.py`
→ `results/curve-discovery-experiments/ledger.json` (+ summary.md).
Every quantitative row carries curve_id + document SHA + page + figure.

### Experiment A — light-load sensor supply (12 V → 3.3 V)

- **A (parametric):** eligible {TPS548C26, LMR33610, LMR36502, TPS563203,
  SiC448}; eliminated {TPS628302} (VIN 5.5 V max); UNKNOWN {TPS542941,
  SiC461-464} — never eliminated.
- **B (+semantic):** light-load/PSM/FPWM flags raise LMR36502 and SiC448
  to "PASS+evidence" — a preference signal, quoted, not measured.
- **C (+curves):** the active segment (100 mA, 5 % duty) is answered from
  LMR36502's printed efficiency curve **whose legend states VIN = 12 V**
  (overriding the 13.5 V page default, override recorded): η = 84.77 %
  typical → 0.467 Wh/day, cited (sha 6b3d562a…, p.10, fig 7-1). Standby at
  12 V is quantified for LMR33610: shutdown 61.7 µW (EN = 0 V) and
  quiescent 300.6 µW (TA = 25 °C), both cited. Every other family's
  efficiency comparison at 12 V → 3.3 V is **refused with reasons**
  (measured at 48 V/5 V, 13.5 V/3.3 V, or 12 V/1.1 V).
- **Net effect:** eligibility unchanged (advisory law); preference is now
  *measured* for exactly one family and explicitly unknown for the rest —
  plus the clarifying question "is 13.5 V acceptable?" which would unlock
  nothing further (LMR36502 already matched via legend) but "what is the
  sensor's own sleep draw?" decides whether standby dominates.

### Experiment B — 30 A continuous, warm enclosure (12 V → 1.1 V)

- **A:** exactly one eligible family (TPS548C26, 35 A); TPS542941 and
  SiC461-464 UNKNOWN (missing parametrics).
- **C:** 12 cited typical values at 30 A — efficiency 82.3–88.2 % and
  dissipation 4.3–6.9 W across fsw legends and FCCM/DCM/internal-LDO
  variants (mode and bias dimensions left unpinned are visible in each
  entry's matched conditions). Thermal derating: SiC case-temperature
  curves refuse at 12 V (measured at 48 V), TPS548C26 prints none —
  junction temperature and airflow are listed as unresolved, and 30 A =
  86 % of rating is flagged without inventing a margin.

### Experiment C — 24 V → 5 V tradeoff

- **A:** eligible {LMR33610, SiC448}; **B:** PSM flag lifts SiC448;
- **C:** **every** efficiency comparison refused (no curve measured at
  24 V → 5 V) — an explicit negative result: the shortlist stays
  parametric, the ledger says why per family, and the priority rankings
  (efficiency-first vs footprint-first vs simplicity-first) reorder the
  same parametric shortlist with reasons. Curve evidence adds nothing
  here and says so.

### Phase 4 metrics

| metric | A | B | C |
| --- | --- | --- | --- |
| hard eliminations (all parametric) | same | same | same (0 from curves) |
| quantitative preference evidence | none | none | 12 cited values @30 A; η + Wh/day @100 mA; 2 standby µW figures |
| refused comparisons recorded | — | — | 20 + 18 + per-intent, each with reason |
| clarifying questions generated | 0 | 0 | 2 (A), 1 (B) |
| typical-as-guarantee violations | 0 | 0 | **0** (asserted by tests) |

Shortlist accuracy against reviewed expert judgments: **pending M4
review** — the ledger is the review artifact; no self-graded accuracy is
claimed here.

## Acceptance criteria status

- no known false hard eliminations — **yes** (tested);
- no typical-as-guaranteed — **yes** (tested at query, compare, and
  ledger layers);
- condition-incompatible comparisons rejected — **yes** (probes + intents);
- every consequential comparison reproducible from source evidence —
  **yes** (deterministic runner, SHA-pinned citations);
- independent plot validation — machine-level complete, human sign-off
  pending (packets emitted);
- one demonstrated material improvement — Experiment A: the *only*
  measured 12 V→3.3 V efficiency and 12 V standby numbers, with the
  legend-override discovery; Experiment B: mode-separated dissipation at
  the operating point. Experiment C is an honest negative;
- no consumer-API or gold regressions — full suite green.

## Ranked missing evidence (what would unlock more DC-DC discovery)

1. **Efficiency curves at 24 V → 5 V** (any family) — unlocks Experiment
   C entirely; today every comparison is refused.
2. **Human sign-off of the 40 machine-reference plots** — converts
   coverage 0.571 into gold-backed evidence; blocking for any promotion
   discussion.
3. **Vision-lane runs over raster pages** — TLS4125D0EPV p.18 (log +
   linear axes, held), plus acquired IR3883/IR3447/ADP5003/LT8292 — adds
   manufacturers 3–4 and the first real log-axis references.
4. **Thermal derating / Zth curves at matched rails** — unlocks warm-
   enclosure clearance (Experiment B's open question).
5. **Light-load efficiency below 100 mA for the 12 V families** — the
   sleep-dominant regime where standby wins or loses designs.
6. **TPS542941 / SiC461–464 family parametrics** (title pages not
   machine-readable) — five UNKNOWN eligibility verdicts collapse once
   quoted rows exist.

## Recommendation for the first curve-driven family-shortlist experiment

Run Experiment A's pattern (M4 intent → parametric cohort → sufficiency
questions → curve evidence → ledger) as the pilot workflow: it is the
only intent where the corpus already yields cited measurements at matched
conditions, and it exercises override, refusal, and question generation
in one pass. Ship the ledger + summary.md to DesignWins for display in
existing components (no new frontend infrastructure), and gate any
broader rollout on items 1–3 above.
