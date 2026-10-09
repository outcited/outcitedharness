# FACET02 PILOT HANDOFF — controlled power pilot (release decision 2026-10-08)

**Status:** APPROVED TO PUSH. NOT merged. NOT deployed to production.
**Branch:** `facet/cohort-narrowing-v1` → pushed to `origin`
(origin.cursor.com/samkim2dgx/harnessv1). Base commit `60280ac6`;
pilot-hardening commits follow it on the same branch.
**Pilot:** localhost-only, `127.0.0.1:8791`, launched from worktree
`/Users/samkim/Harnessv1-facet02`.

## Running / stopping the pilot

```
cd /Users/samkim/Harnessv1-facet02
SEARCH_HOST=127.0.0.1 SEARCH_PORT=8791 \
  /Users/samkim/Harnessv1/.venv/bin/python -m harness.search.api \
  >> /tmp/search_api_pilot.log 2>&1 &
# stop: kill <pid>  (current pid in /tmp/search_api_pilot.log header)
```

Feature flags in effect:
- **Semantic serving DISABLED** — `SEARCH_EMBED_URL` is not set; `/v1/search`
  runs FTS+sections only. Staged vectors (356,061 × bge-m3-cr-tapes-v1,
  fingerprint `19b9f90628f8`) remain in search_index.db for offline
  experiments. No ANN index built; no further embedding runs (directive 8).
- **Facet pilot gate** — `SEARCH_FACET_CATEGORIES=power` (default). `mcu`
  and `connectors` return HTTP 403 `category_gated` with the gate reason
  (directive 9). Evidence search is unaffected by the gate.
- **Localhost hard bind** — non-localhost refuses to start without
  `SEARCH_ALLOW_REMOTE=1` (not set).

## Measured per-axis coverage (live power cohort, n=898 infineon)

| Axis | Coverage | Unknown | Axis | Coverage | Unknown |
|---|---|---|---|---|---|
| vds_rating_v | 0.984 | 14 | tj_max_c | 0.349 | 585 |
| vgs_th_v | 0.982 | 16 | tstg_range_c | 0.341 | 592 |
| rds_on_ohm | 0.970 | 27 | thermal_resistance_c_per_w | 0.744 | 230 |
| gate_charge_nc | 0.956 | 40 | output_voltage_v | 0.000 | 898 |
| coss_pf | 0.948 | 47 | output_current_a | 0.000 | 898 |
| id_continuous_a | 0.881 | 107 | switching_frequency_hz | 0.000 | 898 |

Full-cohort (1,577) numbers are in FACET02_REPORT.md. The converter-IC axes
are structurally empty for a discrete-MOSFET population — shown with true
coverage, never hidden, never faked (R2). Numeric axes below the 0.30
coverage floor are never recommended as the next facet.

## Complete API examples (verified against the running pilot)

```
GET /v1/health
→ {"ok": true, "release": "search-release-v1-8ce00bbf318b", "unit_count": 356061, ...}

GET /v1/discovery/facets?category=power
→ candidate_count 1577, evidence_units 74018, grain "opn",
  recommended_next "coss_pf", 14 facet dimensions

GET /v1/discovery/facets?category=power&c.vendor=infineon
→ candidate_count 898, evidence_units 48992, recommended_next "coss_pf",
  vendor dimension no longer offered (chosen dims are not re-offered)

GET /v1/discovery/facets?category=power&c.vendor=infineon&c.vds_rating_v=500-1000 V
→ candidate_count 263, evidence_units 15226, recommended_next "gate_charge_nc",
  unknown_excluded {"vds_rating_v": 14},
  candidates[0..49] = {opn, vendor, evidence_units, evidence_refs[3]},
  candidates_truncated true

GET /v1/discovery/facets?category=mcu
→ HTTP 403 {"error":"category_gated","allowed":["power"],"notice":"...coverage gates..."}

POST /v1/search {"query":"rdson 3.9 milliohm","limit":3}
→ FTS-only (zero semantic rationale entries), qualification "none"

GET /v1/units/{evidence_ref}
→ full evidence unit (locator, provenance, applicability, grade)
```

Every facet value carries `{value, candidates, evidence_units,
evidence_coverage, unknown_candidates, reduction}` — candidate counts and
evidence-unit counts are separate fields everywhere (hard invariant).

## Verified pilot behaviors (directive item 5)

| Check | Result |
|---|---|
| Three-step journey | 1,577 → 898 → 263 (API, live catalog) |
| Candidate counts ≠ evidence counts | 263 candidates vs 15,226 evidence units at step 3 |
| Unknown handling | 14 no-VDS candidates reported in `unknown_excluded`, not eliminated |
| SI normalization | mΩ→ohm buckets, MHz case-sensitivity, µC≠°C — 9 regression tests |
| Provenance | every candidate lists evidence_refs → GET /v1/units/{id} → sha/page/locator/quote |
| Deterministic replay | two identical requests → byte-identical responses (cmp verified) |
| Family identity missing | grain reported "opn", no family facet, note in every response |
| Boundary conditions | half-open buckets; interval touching a boundary handled per point-set semantics — adversarial tests |
| Sparse-axis ranking | coverage floor 0.30; near-empty axes never recommended — adversarial tests |
| Conflicting claims | best-per-op with its own condition; never averaged/merged — adversarial tests |
| Semantic off | 0 semantic rationale entries; vectors+fingerprint retained staged |

Test status at handoff: **1,445+ tests green** (40 facet/adversarial +
full repo suite) on this branch.

## Unresolved limitations

1. **Candidate grain is OPN only.** Catalog `family`/`mpn_base` are empty
   (0/1,882) — MCU series-grain and connector class-grain narrowing are
   impossible until upstream fills them. No fabrication.
2. **Power claim evidence is discovery-only.** Burn-wave `unhashed:`
   identities → `evidence_grade_units=0` in live journeys. Honest, but the
   pilot UI must render the discovery-only marking.
3. **Cohort rebuilds per request** (~7 s cold on 86K claims; the running
   pilot answers in <2 s because the OS caches the DB). A cohort cache
   keyed to the catalog release is required before multi-user serving.
4. **tstg_range_c facet labels** are distinct printed values, not buckets
   (interval-bucketing for storage-temperature spans is unsolved; low
   traffic axis).
5. **Search-side section dilution**: `/v1/search` power recall dropped
   0.243→0.176 when sections were added (mcu rose 0.118→0.265). Field
   weighting (claims > sections for symbol queries) is the known fix; not
   in this release.
6. **403 gate is category-level only**; subcategory gating within power
   (e.g. hiding converter axes) is coverage-floor behavior, not policy.

## Explicit GA blockers

1. Family/series identity populated upstream (blocks R1 grain promise for
   MCU/connectors).
2. Provenance backfill: unhashed → real sha + page for burn-wave claims
   (blocks evidence-grade display for the power pilot itself).
3. Cohort caching keyed to catalog release (blocks multi-user latency).
4. MCU/connector candidate coverage in catalog (blocks lifting the 403
   gate; today the live catalog has 0 mcu/connector candidates).
5. Auth + network policy before any non-localhost bind (unchanged).
6. Bucket review by a power engineer (labels are deterministic but the
   boundaries are librarian-chosen, not committee-approved).

## Hard-invariant checklist (all regression-tested)

- [x] No unsupported hard exclusions (unknown → `unknown_excluded`, never FAIL)
- [x] No unknown-as-failed conversions
- [x] No evidence-unit inflation of candidate counts
- [x] No family-to-OPN scope expansion (grain=opn reported; family not fabricated)
- [x] No unapproved evidence promoted to gold (nothing writes verification; M4 untouched)
