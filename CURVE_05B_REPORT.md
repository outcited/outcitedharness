# CURVE-05B — comparative evidence report (A/B/C discovery validation)

Branch `curve/05b-discovery-validation`, base `curve/05a-qualification@
f96dedbf`. Everything below runs off the frozen, SHA-verified bundle
**`curve-evidence-bundle-v1-5cc1a6e41ad8`** (bundle SHA `bc0bb4ab…`,
verified at load; a tampered bundle refuses to load — tested). No UI work
was done, per instruction. Curves remain advisory; no hard eliminations;
no verified claims from pending packets; typical never guaranteed.

## 1. Cross-family comparability matrix

`scripts/build_comparability_matrix.py` →
`results/curve-05b/comparability_matrix.json`. Method: curves group into
comparison cells by (phenomenon, engineer-matchable conditions: rails,
temperature, mode). Device-rating keys (e.g. `iout_a` from
"SiC461 (10 A)" headers) are identity CONTEXT, not test conditions —
they ride every row but never widen a cell. Rows whose own printed
conditions CONFLICT are excluded from cell membership and listed as
`conflicted_members` with their conflict.

Result on the 81-row bundle:

| | count |
| --- | --- |
| comparison cells | 18 |
| **genuine cross-family cells** | **1** |
| single-family cells | 16 |
| unclassified rows (no phenomenon) | 22 |

The one genuine cross-family cell: **case-temperature derating at
48 V→5 V, SiC462 (2 series) vs SiC463 (2 series), shared support
0.50–4.00 A**. The 48 V→5 V efficiency cell collapses to single-family
(SiC462) because SiC461's and SiC464's figures print 12 V/24 V legend
curves whose traces are not legend-bound — their own conditions conflict
and the comparison law refuses them. This is the honest answer to "which
scenarios have enough condition-compatible evidence": **thermal-envelope
comparison at 48 V→5 V across two Vishay devices, and nothing else
cross-family today.**

A newly surfaced risk is recorded rather than hidden: SiC462's
efficiency figure captured no legend rows and therefore relies on the
page-default conditions (VIN = 48 V) while sibling figures on sibling
pages print 12 V/24 V legends — a legend-capture ambiguity flagged in
the matrix and in the case caveats, with the confirming question placed
in its cold-review packet.

## 2. Frozen A/B/C experiment

`scripts/run_abc_discovery.py` → `results/curve-05b/abc_discovery.json`.
Configurations: A parametric (frozen quoted cohort), B = A + semantic
flags, C = B + bundle curve evidence. Same cohort, same questions, full
per-case reporting (intent, activated knife/question, cohorts, ranking
change, exact evidence, defensibility verdict, missing evidence).

### Case 1 — 48 V → 5 V at 3 A, warm enclosure (cross-family)

- **A:** VIN knife eliminates everything except the SiC46x family
  (60 V) plus two UNKNOWN rows; IOUT stays UNKNOWN for SiC46x — no
  elimination.
- **B:** PSM flag adds no ranking.
- **C (change):** cross-family **thermal ranking forms** — SiC462
  sustains a higher case temperature than SiC463 at the shared 3 A
  point (best typical readings **47.6 °C vs 45.0 °C**, four cited
  series across the two devices). Efficiency at 3 A answers for SiC462
  only (**95.48 % typical**, cited); SiC461/SiC464 refuse with recorded
  reasons (legend conflicts).
- **Defensible?** Yes, with caveats: typical values, pending
  adjudication, unnamed derating variants, and the SiC462
  legend-capture ambiguity above.
- **Missing:** legend binding for SiC461/SiC464 (vector-text legends),
  per-device IOUT ratings, a second manufacturer at 48 V→5 V, airflow
  context.

### Case 2 — 12 V → 1.1 V at 30 A (within-family variant selection)

- **A/B:** exactly one eligible family (TPS548C26, 35 A); A and B
  cannot rank its variants at all.
- **C (change):** 12 cited typical values at 30 A separate the variants:
  best efficiency **88.20 %** and lowest dissipation **4.30 W** both on
  the **800 kHz** configuration (evidence IDs in the ledger); unpinned
  mode/bias dimensions are explicitly flagged, so the winner is stated
  as conditional.
- **Defensible?** Yes, with caveats: no matched-condition derating
  curve exists, and 30 A is 86 % of rating — margin not computable.

### Case 3 — 12 V → 3.3 V sensor supply, 95 % sleep (claim → number)

- **A:** five families eligible; **B:** LMR36502 flagged "light-load
  optimized" — a quoted claim.
- **C (change):** the claim becomes a measured typical **84.77 % at the
  exact 100 mA operating point** (legend `VIN = 12 V` overriding the
  13.5 V page default — override recorded), plus the only measured
  12 V standby figures (LMR33610 shutdown **61.7 µW**, quiescent
  **300.6 µW**, cited). Other families' efficiency comparisons refuse
  with reasons (never guessed).
- **Defensible?** Yes, with caveats: legend-override condition and
  sensor's own sleep draw must be added by the engineer.

## 3. Three reproducible examples — verdict

**Delivered: 3**, each reproducible by re-running
`scripts/run_abc_discovery.py` against the pinned bundle (tests assert
every consequential number cites a bundle evidence_id, every refusal has
a reason, and no curve eliminates). Honest classification:

1. genuine cross-family ranking (thermal, 2 families);
2. within-family variant ranking (12 cited values);
3. semantic-claim-to-measurement conversion.

**NOT claimable today:** cross-family EFFICIENCY ranking at any rail —
blocked by legend binding (Vishay vector-text legends) and by
single-manufacturer cells. Exact unlockers, in priority order:

1. legend binding for SiC461/SiC464 efficiency figures (vector-text
   legend OCR) — immediately yields a 4-way 48 V→5 V efficiency cell;
2. efficiency curves at 12 V→3.3 V for TPS548C26 / TPS563203 / SiC448;
3. a second manufacturer with vector or recoverable-raster curves at
   matched rails (LT8292 / ADP5003 acquisitions, still network-blocked);
4. per-device IOUT ratings for SiC46x (title-page table read) — turns
   UNKNOWN eligibility into hard parametric facts.

## 4. Gates (asserted by `tests/test_curve05b.py`, 10/10)

SHA-verified bundle, fail-closed on tampering; conflicted rows never
support cells; cross-family cells require shared support; context keys
never widen cells; every case carries all seven required fields; no hard
eliminations from curves; every value cites bundle evidence with
`guarantee:false`; every refusal reasoned; cross-family efficiency not
overclaimed.

Frontend investment is deliberately deferred: this report is the
comparative-evidence deliverable requested before any UI work.
