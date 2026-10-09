# CURVE-07E — Production Evidence Supply for DesignWins Power Discovery

Branch `curve/07e-supply` (worktree `/Users/samkim/Harnessv1-curve07e`),
base `curve/05b2-corrections@3b926a00`. M5 evidence lane only: no
recommendation engine, no customer API, no frontend. CURVE-06 code in
`outcited.com` is untouched and preserved for M4 (`curve/06-decision-engine@23838f1`).
v1/v2 bundles verified byte-identical (frozen-test).

## R1 — SiC46x evidence gaps: before / after

| item | before (v2) | after (v3) |
| --- | --- | --- |
| SiC46x efficiency series legend-bound | 21 of 25 | 100 of 104 corpus-wide (0.962 resolved fraction) |
| 48 V→5 V traces bound per device | 461,462,463,464 partial | all four, with L-values (10/8.2/15/22 µH) |
| unnamed traces on multi-legend figures | silently inherited page VIN | **refused** as `legend_ambiguity` (4 series), never defaulted |
| black/short-segment traces | dropped (`no_vector_curves`) | recovered via color-cloud aggregation (LM5161) |
| in-frame tick labels (LM5161 layout) | `axis_fit_failed` | inside-band tick fallback + log10 fit |
| physics validation | none | VIN-monotonicity check on every multi-VIN bound figure (frozen test) |

Trace binding is positional on the document's own inline-label convention,
validated by buck-physics monotonicity (efficiency non-increasing in VIN
at matched load); where two legend rows and one unbound trace remain, the
row refuses rather than guesses.

## R2 — genuinely different families: FOUND in the held corpus

Target kept at **48 V → 5 V, 0.5–3 A, efficiency vs load** — no retarget
needed. Search of the local TI corpus (1,897 dcdc datasheets, first-page
VIN screen → typical-characteristics page → vector extraction) found
**LM5161 (Texas Instruments, 100 V buck)** printing VOUT = 5 V efficiency
figures with VIN = 36/48/60 V legends at 300 kHz, log load axis.

Resulting canonical cross-manufacturer cells (distinct families
LM5161 vs SiC46x; manufacturers TI vs Vishay):

| cell | families | shared support | example @0.5 A |
| --- | --- | --- | --- |
| 48 V→5 V efficiency | LM5161, SiC46x | 0.025–0.99 A | LM5161 77.95 % vs SiC461 93.94 % / SiC463 92.96 % / SiC464 92.95 % |
| 36 V→5 V efficiency | LM5161, SiC46x | 0.025–0.99 A | same ordering |

Eligibility checks applied BEFORE ranking: LM5161 (1 A rating) excluded at
3 A; SiC464 (2 A) excluded at 3 A — hard ratings, not curve opinion.
External components disclosed per trace (L = 47 µH LM5161 vs 8.2–22 µH
SiC46x); fsw aligned at 300 kHz; delta 10–16 points dwarfs tick-fit
uncertainty (≤0.014 % of span) → significant. At 1 A and 3 A the
comparison reverts to within-family (LM5161 support ends at 0.99 A) —
stated, not hidden.

## R3 — catalog admission readiness

Four deterministic admission packets (`curve_evidence_bundle_v3_manifest.json`
→ `catalog_admission_packets`): SiC461/462/463/464 with manufacturer,
canonical family SiC46x, device identity, datasheet SHA
`0cef2db3…a175` + revision S25-1437-Rev. S, ratings with verbatim header
quotes (10/6/4/2 A), curve associations by evidence_id, part-vs-family
scope, and unresolved-mapping notes. **Not applied** — delivered to the
authorized catalog owner (M4) as a request.

## R4 — coverage matrix (five levels, never blended)

20 condition cells; per cell the matrix reports same-configuration
observations, within-device configurations, within-family device
comparisons, cross-family comparisons, cross-manufacturer comparisons
separately: 3 cross-family cells (48/5, 36/5, 24/5 incl. SiC448 vs
SiC46x), 2 cross-manufacturer, 2 within-family, 15 single-device.
9 canonical families, 2 manufacturers, 54 figures, 123 rows.

## R5 — human adjudication preparation

117 cold-review packets regenerated (reviewer-first: render + printed
conditions + blind checklist; extraction sealed). Priority order for the
first human session (manifest `cold_review_priority`): the four 48 V→5 V
SiC46x efficiency figures, the three ambiguous unbound traces, LM5161
Figure 2 (cloud-bound), LM5161 Figure 3, TPS548C26 Figure 6-1 anchor.
Approval pipeline accepts authorized verdicts later without identity
change; 0 approved, 123 pending — nothing simulated.

## R6 — decision-driven evidence requests

| M4 request | outcome |
| --- | --- |
| second family at 48 V→5 V | **satisfied** — LM5161 extracted from held corpus, physics-checked |
| Vishay SiC46x catalog admission | prepared, not applied (owner-gated) |
| human adjudication | blocked pending human (packets ready) |
| IR3883/IR3447/ADP5003/LT8292 | blocked by source access (egress filtered; CURVE-04 manifest) |

No corpus-wide vision extraction was run; the LM5161 find came from a
targeted VIN-screened scan of held TI documents.

## R7 — versioned handoff

`curve-evidence-bundle-v3-5875789dcf3f` (123 rows, SHA in
`curve_evidence_bundle_v3_manifest.json`, supersedes v2, v1/v2 frozen).
Rows carry: full points, provenance (sha/page/figure/revision), canonical
identity (device→family_group→manufacturer), exact conditions incl.
legend bindings and ambiguity flags, comparability rules, adjudication
state, binding method + physics check. Manifest carries coverage matrix,
admission packets, evidence-request outcomes, review priorities, numeric
fixtures list, and the comparator fingerprint (M5/M4 parity anchor).
Consumable from pinned fixtures only — no M5 databases.

## R8 — acceptance metrics (numerator/denominator)

1. operating-condition cells covered: **20** (of 20 non-conflicted cells)
2. independent canonical families: **9**
3. defensible cross-family comparisons: **3 cells**
4. defensible cross-manufacturer comparisons: **2 cells**
5. meaningful legend ambiguities resolved: **100/104 = 0.962** (4 refused as ambiguous)
6. operating-point numeric error: tick-fit residual ≤ **0.0123 % of span**
   (vector); raster lane median **1.9 %** at 300 DPI (frozen experiment)
7. catalog-admission packets ready: **4**
8. evidence requests satisfied: **1 of 4** (1 prepared, 2 blocked)
9. human-approved vs pending: **0 / 123**
10. discovery scenarios unlocked: **48 V→5 V and 36 V→5 V at 0.025–1 A
    cross-manufacturer; 24 V→5 V cross-family (SiC448 vs SiC46x)**

Primary metric — additional defensible engineering decisions enabled:
**three** (the two cross-manufacturer cells plus the cross-family 24 V
cell), each reproducible from frozen evidence ids.

## R9 — safety gates

All asserted by `tests/test_curve07e.py` (10 tests) plus the frozen
suites: no invented quotations (verbatim-only conditions), no OPN/family
expansion (family-scoped rows carry `coverage_hint` and never a part),
no gold promotion (0 approved), no typical-as-guaranteed (`guarantee:
false` on every row), no extrapolation (support-bounded, tested), no
silent legend binding (ambiguity refuses; physics check frozen), no false
cross-family classification (grain tests), no fabricated approvals, no
release flips, no M4 branch changes. v1/v2 bundles byte-identical.

## Test results

- `tests/test_curve07e.py`: 10/10
- pilot scorer: 16/16 probes, precision 1.000, coverage 0.684 (47/76
  printed figures; misses listed with reasons)
- full harness suite: **1416 passed**

## Unresolved limitations

1. Zero human approvals — every value remains machine-verified advisory.
2. LM5161 support ends at 0.99 A: the 1–3 A cross-manufacturer band needs
   a second wide-VIN family's mid-load curves (evidence request open).
3. Four ambiguous SiC46x traces (36 V-vs-48 V) await cold review.
4. Vishay absent from M4's catalog until admission packets are applied
   by the owner.
5. Raster-lane evidence (TLS4125D0EPV) still outside the bundle.
