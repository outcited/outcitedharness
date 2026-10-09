# FACET-02 REPORT — candidate-centric cohort narrowing (deliverables 7-8)

Branch `facet/cohort-narrowing-v1` (isolated worktree, off search @ 0088b5e4).
Per PRD: not pushed, not merged — awaiting authorization.

## Deliverable map

| # | Deliverable | Where |
|---|---|---|
| 1 | Candidate-grain facet schema | `harness/search/cohort.py` — `COHORT_SCHEMA`, `Candidate` (opn, vendor_raw+canonical, attributes, evidence counts kept separate) |
| 2 | Canonical vendor identity registry | `harness/search/vendors.py` — 24 canonical vendors, alias table, ambiguity guard, provenance on every resolution |
| 3 | Numeric spec-axis facets | `harness/search/axes.py` — 24 axes across power/mcu/connectors, SI normalization (case-sensitive prefixes), value typing (point/inequality/interval/unknown/text), typed-condition attachment |
| 4 | Cohort-reduction ranking | `cohort.facets_for_cohort` — reduction × coverage, deterministic; mirrors discovery next_question semantics |
| 5 | Versioned endpoint | `GET /v1/discovery/facets` in `harness/search/api.py` — constraints via `c.<axis>=<bucket>`, evidence_quality policy param, existing search routes untouched |
| 6 | Three narrowing journeys | `scripts/facet_journeys.py` (output below) |
| 7 | Coverage & correctness report | this document |
| 8 | Release recommendation | below |

## Measured journeys (deliverable 6)

**Power — LIVE catalog, 1,577-candidate population:**

| Step | Candidates | Evidence units | Unknown-excluded | Next recommended | ms |
|---|---|---|---|---|---|
| open (all power) | 1,577 | 74,018 | — | coss_pf | 10.1 |
| vendor=infineon | 898 | 48,992 | — | coss_pf | 6.3 |
| + VDS 500-1000 V | 263 | 15,226 | 14 (no VDS evidence) | gate_charge_nc | 1.8 |
| + RDS(on) 20-50 mΩ | 69 | 4,120 | 14 | gate_charge_nc | 1.2 |

23× narrowing in three deterministic steps; candidate counts and evidence
counts never conflated; the 14 candidates without VDS evidence are reported
as unplaced at the VDS step, never judged unsuitable.

**MCU — fixture corpus** (live catalog is power-only; mechanics demonstrated
on real data shapes): 3 candidates → flash 128-512 KB → 1 → freq 80-150 MHz
→ 1. **Connectors — fixture corpus**: 1 candidate → pitch <1.0 mm → 1 →
positions 7-24 → 1.

## Live axis coverage (power cohort, 1,577 candidates)

| Axis | Coverage | Unknown | Axis | Coverage | Unknown |
|---|---|---|---|---|---|
| vds_rating_v | 0.804 | 309 | tj_max_c | 0.543 | 720 |
| vgs_th_v | 0.783 | 343 | thermal_resistance | 0.587 | 651 |
| coss_pf | 0.764 | 372 | tstg_range_c | 0.362 | 1,006 |
| id_continuous_a | 0.628 | 586 | output_voltage_v | 0.029 | 1,531 |
| rds_on_ohm | 0.612 | 612 | output_current_a | 0.018 | 1,549 |
| gate_charge_nc | 0.595 | 639 | switching_frequency | 0.015 | 1,554 |

The three converter-IC axes are near-empty because the catalog population is
discrete devices — coverage reporting makes that visible instead of offering
a facet that cannot narrow. Per R2, dimensions with no supporting data are
shown with their true coverage, never hidden and never faked.

## Correctness (R6 — all ten, regression-tested)

`tests/test_facet02_cohort.py` (12 tests) + `tests/test_facet02_axes.py`
(9 tests), all green; full suite 1,431 passed:

1. candidate ≠ evidence-unit counts (2-doc candidate counted once) ✓
2. no family→OPN expansion (grain=opn reported; claims bind to their own
   opn only; family grain not fabricated — catalog family is empty) ✓
3. range + SI filtering: mΩ→ohm, MHz case-sensitivity (regression caught
   live: 80 MHz ≠ 0.08 "mhz"), µC=coulomb not Celsius, KB never rescaled,
   composites (°C/W) unscaled ✓
4. unknowns counted per axis, never eliminated ✓
5. vendor normalization deterministic; `unknown`/empty never resolved;
   original + rule preserved ✓
6. hard requirement + missing evidence → `unknown_excluded`, noted
   "unplaced, never judged unsuitable" ✓
7. successive selections: monotone narrowing, chosen dims not re-offered ✓
8. evidence_grade vs discovery_only separated in every count ✓
9. multi-document support: one candidate, N evidence units ✓
10. deterministic rebuild: identical facet payloads across runs ✓

Intervals: min+max qualified claims merge to an explicit interval
(VDD 2.0-3.6 V) that honestly overlaps both neighboring buckets — no
guessed midpoint. Curves are never converted to spec limits (figure units
are not claim sources for any axis).

## Known limits (disclosed)

- Candidate grain is OPN everywhere: catalog `family`/`mpn_base` are empty
  (0/1,882). MCU series-grain and connector interface-class grain arrive
  when upstream fills them; the schema already carries `grain` per response.
- Live MCU/connectors cohorts are empty (catalog is power-only) — journeys
  for those aisles run on the frozen fixture corpus until the catalog grows.
- Power claim evidence is discovery-only (unhashed burn-wave identities);
  `evidence_grade_units=0` in the live journey is the honest number and
  rises as the substrate backfill lands.
- Cohort build is in-memory per request (~7s cold on 86K claims); the
  endpoint rebuilds per call. A cohort cache keyed to the catalog release
  is the obvious next optimization, not done here.

## Release recommendation (deliverable 8)

**Recommend: merge to a PILOT behind the existing localhost-only rule, for
the power aisle only.** Rationale: all ten R6 validations pass on frozen
fixtures; the live power journey narrows 1,577→69 deterministically in
<15 ms/step with honest unknowns; no invented values anywhere (every number
traces to a catalog claim with provenance). **Do not expose mcu/connector
facets as product** until catalog coverage exists — the endpoints return
empty cohorts honestly, but a UI should not offer them yet. Blocking items
for general availability: family-grain population upstream, evidence-grade
provenance backfill (unhashed → real sha), cohort caching.
