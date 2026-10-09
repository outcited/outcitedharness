# DISCOVERY-01 PRODUCTION READINESS — REASSESSMENT AFTER B1–B6 REMEDIATION

Supersedes the assessment recorded at `discovery/01-final-parity@fe6a7e60`
(verdict `NOT PRODUCTION-READY — BLOCKERS REMAIN`, six blockers). All six
are now closed. Two new blockers remain, and neither is closable by code in
this lane.

**No production state transition was performed.** The experimental flag
posture and localhost-only binding are unchanged. Machine-readable verdict:
`results/discovery01-production-readiness/READINESS_VERDICT.json`.
Per-blocker evidence: `results/discovery01-remediation/`.

Reproduce:

```
.venv/bin/python -m pytest tests/ -q                                  # 1571 passed, 1 skipped
.venv/bin/python scripts/discovery01_live_parity.py --url http://127.0.0.1:8798 \
    --out results/discovery01-remediation/parity_after_remediation.json  # 21/21 PASS
.venv/bin/python scripts/discovery01_production_pilot.py                 # 15 scenarios
.venv/bin/python scripts/discovery01_production_pilot.py --perf
.venv/bin/python scripts/discovery01_production_pilot.py --post-restart
.venv/bin/python scripts/discovery01_remediation_evidence.py b3 b2 b4 b5 b6 b6restart
.venv/bin/python scripts/discovery01_readiness_probes.py
.venv/bin/python scripts/discovery01_identity_authority.py
.venv/bin/python scripts/prime_discovery_release.py --dry-run
```

## Remediation summary

| Blocker | Before | Fix | Tests | Live evidence | Status |
|---|---|---|---|---|---|
| **B3** release pin bypassed by cache | unpinned request warmed the cache; a stale-pinned request hit it → `failures: []`, `partial_result: false` | `canonical_release_pin()` added to `SessionCache.key`; order-insensitive; absent/empty/None canonicalise alike; failures still never cached | 9 new (7 failed first) | `b3_release_pin_cache.json`: stale pin after warm cache → `cache_hit false`, `release_mismatch`, `partial true`, cohort 1577 preserved; valid pin misses then hits clean | **CLOSED** |
| **B2** provenance unresolvable | 4 cited ids, `documents {}`, 4/4 unresolved — though the shas match 9 and 22 index units | `entry_provenance()` reads `source.*` as authoritative, legacy top-level as fallback, disagreement fails closed as `provenance_conflict`; per-id resolution with locator carry-through and explicit unresolved reasons | 15 new (13 failed first) | `b2_provenance_resolution.json`: 4/4 resolved, 2 documents (22 + 9 units, `evidence_grade`), 0 unresolved, 0 conflicts; LM5161's cited page honestly flagged absent from index pages | **CLOSED** |
| **B4** auth failed open | M5 session served 200 with no credentials; M4 authorized everything with no token; no M4 bind guard; `!=` compare | Both hops fail closed: 401/403/503, `hmac.compare_digest`, loopback dev posture requires flag **and** loopback bind **and** loopback client, M4 bind guard + startup refusal, M5 non-local bind requires a session token, auth decided before any store is opened, health/release routes made read-only | 19 new M5 (16 failed first) + 22 new M4 (11 failed first) | `b4_auth_fail_closed.json`: M4 401/403/200 + evidence route 401; M5 401/403/200; M5 no-token → 503; three startup refusals rc=1; rollback flag-off → 404 | **CLOSED** |
| **B5** per-request full re-fingerprint | 2.84–3.49 s/request, ~2.9 s = two fingerprint passes over 443,604 rows, uninstrumented, paid on cache hits; served release never persisted | policy-keyed `release_cache` + `policy_release()`; `_mark_release_dirty()` on unit **and** vector writes; `release_view` routes policy lookups; lookup instrumented; cache-hit timings honest; priming script with invariants | 17 new (15 errored/failed first) | `b5_release_priming.json`: PRIMED, lookup 1436.9 → 0.2 ms, release id unchanged, all invariants held, ledger +1. `b5_release_fingerprint_latency.json`: cold 643.3 ms, hit 28.5 ms, lookup 0.4 ms, replay preserved | **CLOSED** |
| **B1** identity/catalog/branch reconciliation | assessment reported 626 proposed / 0 promoted and 15 proposed (a mis-ordered query); docs said nothing was promoted; `parts.family` 0/1882 | authority determined and recorded, not duplicated: identity DB is the source of truth (878 relationships, **all verified**; 19 nodes; 23 approval rows; 3 owner refs); catalog deliberately **not** populated because the serving path never reads it; generator script + operator document; six documents corrected with dated STATUS CORRECTION blocks | 12 new (5 failed first) | `b1_identity_authority.json`; `DISCOVERY01_IDENTITY_AUTHORITY.md` answers all six operator questions | **CLOSED** (pre-promotion action R2) |
| **B6** fixture crosswalk, disjoint populations | 21-row fixture; no candidate list sent; 20/20 M4 parts absent from catalog; `identity_mapped_exact 0`; facets could not influence ranking | owner architecture implemented: `select_candidates()` extracted; crosswalk generated from the identity store (versioned, release-bound, evidence-backed, coverage measured, no fuzzy joins); explicit scope payload with canonical ids, manufacturer, state, identity, provenance; unplaced unknowns transported not dropped; oversized population fails closed; unsolicited M4 entries stripped as `ranking_population_violation`; full `ranking_population` disclosure with accounting assertion; M4 accepts `candidate_scope`, matches exact identities only, never substitutes its universe | 20 new M5 (17 failed first) + 21 new M4 | `b6_candidate_transport.json`: 17/17 checks over J1–J7b; `b6_restart.json`: J8 identical payload hash, releases, disclosure, counts | **CLOSED** (coverage gap R1) |

## A. Exact M4 SHA

| Field | Value |
|---|---|
| Repo / branch | `beargallbladder/outcitedharness` / `curve/08c-parity-repair` |
| Before remediation | `42644eacd779cd91324215fe48c2cb2a78944587` |
| **After remediation** | **`f02f08a21021240de11e951749613b1123ef2071`** |
| Commits added | `3c183d49` (CURVE-08D, B4 auth fail-closed), `f02f08a2` (CURVE-08E, B6 candidate scope) |
| Worktree note | found in detached HEAD at `42644eac` with no ref containing it; the branch named by `curve08b.assignment.json` was (re)created at exactly that commit, which also restored the assignment/branch isolation lock to green |
| Live bundle | `curve-evidence-bundle-v3-5875789dcf3f` / `0c047e516c10c489134efe226d12e2422651665c4e394379108e9ec10ea4b16f` — unchanged |

## B. Exact M5 SHA

| Field | Value |
|---|---|
| Assessment commit | `fe6a7e60a66509970c16eab29d660c46710160b0` (`discovery/01-final-parity`) |
| Remediation branch | **`discovery/01-production-remediation`** (from `fe6a7e60`) |
| Commits | `2b971541` B3 · `324e542c` B2 · `df60d2a7` B4 · `c74927b0` B5 · `1cf22043` B1 · `73b575e6` B6 · plus this reassessment |
| Ancestry | CURVE-08 `23b17634`, FACET-03 family signoff `961ef951` present; admission commits `6ba8c66e`, `b76f14be`, `4e4398aa`, `0c23a34d` and cohort cache `49557b74` **absent** (R2) |

## C. Current state

**EXPERIMENTAL-INTEGRATED** — unchanged. The session route remains behind
`SEARCH_ENABLE_ENGINEERING_SESSION` and now additionally behind
`SEARCH_SESSION_TOKEN`; both services bind loopback only. The production
(default) ranking population is the M5 discovery cohort; the transitional
M4-universe population remains available, labeled `out_of_cohort`, with
`promotion_blocked_while_normal_path: true`.

Served M5 release is `search-release-v1-d95199aecb45`, not the assessment's
`3f8b74ece642`: the index gained three unit rows from a concurrent
extraction on another lane (`extraction_version '...+prov2-opnid'`) during
remediation. The id is content-derived, so moving is correct behaviour; M4's
bundle release is unaffected.

## D. R11 criteria status (repository-defined)

| # | Criterion | Status now |
|---|---|---|
| R11.1 | Admission decisions | **DONE in the live store** — 878 relationships all verified (626 OPN→family, 237 OPN→series, 15 series→family), 19 verified nodes, 23 approval rows, three owner refs. Executing commits are not in this branch (R2) |
| R11.2 | Provenance backfill completion | **CLOSED for the resolver** (4/4 resolve). Residual data gaps documented: 7/1617 catalog docs unhashed, 88.6 % evidence-grade, 58 absent + 120 unverifiable quotes, all active units `unverified` |
| R11.3 | Cohort caching | **Release fingerprint fixed** (0.4 ms; cold 643 ms; hit 28.5 ms). Cohort build (~604 ms) is still in-memory only; the persistent release-keyed cohort cache exists on another lane (`49557b74`), not here |
| R11.4 | Auth policy | **CLOSED for localhost serving** (fail closed both hops, constant-time, bind guards, startup refusals). TLS, per-client credentials, rate limiting and audit logging remain prerequisites for any cross-machine exposure |
| R11.5 | Family-grain population | **Authority resolved, materialization intentionally not done**: `parts.family` 0/1882 and index `units.family` 0/356,061 are documented as non-authoritative for discovery; the serving path reads the identity store. Materializing the search surface would change the index release and needs owner authorization |
| R11.6 | No mcu/connector facet exposure | Honoured (served facets: subcategory, vendor, family, spec axes) |

## E. Remaining blockers

### R1 — In-cohort ranking coverage is zero (data acquisition, not code)

| Measurement | Value |
|---|---|
| M4 evidence universe | 20 distinct parts — TI `LM*`/`TPS*` buck converters + Vishay `SiC46x`, from `tests/fixtures/m4-handoff/curve_evidence_bundle_v3.jsonl` and `curves-pilot-v1/` |
| M5 catalog | 1,882 parts — infineon 1032, rohm 675, unknown 161 (TI `BQ*` battery), ti 13 (`BQ*`), esp 1 |
| Intersection | **0** — verified exact, case-insensitive, normalized and prefix-level |
| In-cohort journey J1/P10 | 1577 sent = 1577 received, **0 matched, 0 ranked, 1577 unresolved-no-evidence** |
| Legacy journey J3b (for contrast) | 4 ranked, 16 excluded, 1 unknown retained — all outside the cohort |

The plumbing the owner approved now works and is fully disclosed, but a
production discovery system whose own cohort can never produce a ranked
candidate is not operationally usable, and the "normal supported cohort"
journey returns an empty ranking. Closing this needs curve extraction over
catalog parts (Infineon/Rohm datasheets) — evidence that does not exist and
must not be fabricated or inferred.

Smallest safe path: owner/data-lane decision to extend curve extraction to
the catalog population, or to admit the M4 pilot parts into catalog and
identity. Until then the in-cohort path stays the honest labeled default and
`out_of_cohort` must never be presented as facet-constrained.

### R2 — The promotion branch cannot reproduce the state it serves

Absent from this branch: `6ba8c66e` (family admission executed), `b76f14be`
(series-grain v2), `4e4398aa` (15/15 family→series edges, `promote_packet`),
`0c23a34d` (series signoff) and `49557b74` (release-keyed persistent cohort
cache). This branch's `identity.py` cannot even write a verified
relationship, so the live verified state is not reproducible here. Merging
another lane brings unrelated changes (grain-weighting experiment
`4c1f45a6`), so it requires owner authorization.

## F. Already satisfied (verified again in this reassessment)

- **Parity preserved**: 21/21 extended checks PASS, verdict PASS, all three
  journeys `baseline_match: true`, SAME_RELEASE
  `curve-evidence-bundle-v3-5875789dcf3f`
  (`parity_after_remediation.json`). Unscoped M4 request bodies are
  byte-identical to before; no frozen fixture was regenerated; the parity
  runner requests the M4-universe population explicitly and records it. The
  18 original gates' logic was not touched.
- **Deterministic replay**: every pilot journey replays to an identical
  semantic hash (P1, P10–P14 and all B6 journeys `true`).
- **Restart reproducibility**: P9 `PASS` (release identity stable, semantics
  identical, legacy values byte-identical, 659.3 ms cold) and J8 `PASS`
  (in-cohort `candidates_sha256`, releases, disclosure and counts identical
  after restarting both services).
- **Rollback**: flag off → session route 404, facets 200, M4 unaffected;
  re-enabling restores 200. Auth changes did not alter rollback.
- **Fail-closed empty ranking**: P6 (no defensible comparison) → empty
  ranking, `partial_result false`, no fabricated values; J6 (empty
  population) → 0 sent, 0 received, honest empty.
- **Hard eligibility**: preserved on the labeled legacy path (16 exclusions
  with rule/rated/required/reason) and enforced inside a supplied population
  by M4's own contract (M4 test `test_hard_exclusion_still_applies_within_the_population`).
- **Unknown retention**: preserved (`missing_ratings` + "retained, never
  eliminated"), and now also survives facet narrowing — 612 unplaced
  candidates are transported flagged rather than dropped (J4).
- **Interpolation disclosure**: unchanged (supporting points present, values
  differ correctly between operating points).
- **Approximate-mode disclosure**: unchanged and still honest — the entry
  list is present and empty; M5 does not emit M4's `mode` field (OH2).
- **Release pinning semantics**: strengthened (B3) — cold, warm and repeated
  pinned requests all report `release_mismatch` with the cohort preserved.
- **Bundle immutability / frozen locks**: v1/v2/v3 bundles and frozen
  journeys untouched; M4's manifest pin still fails closed on tampering.
- **Verified family/series identity state**: untouched — the identity store
  was opened read-only; no promotion, demotion or reinterpretation.
- **Suite**: 1571 passed, 1 guarded skip (was 1479 + 1) — 92 new regression
  tests, no regressions. M4: 98 passed + 1 pre-existing environment failure.

## G. Tests run

| Suite | Result |
|---|---|
| Full M5 suite (`pytest tests/ -q`) | **1571 passed, 1 skipped** in 54.71 s |
| Affected M5 suites (discovery01 + projection + B1–B6 + search api/units/gate-curves/query/facets + facet02 ×3 + facet03 identity + search isolation) | **215 passed** |
| New B1–B6 regression files | 12 + 15 + 9 + 17 + 20 = **73 M5 tests** |
| M4 `test_curve08b` + `08c_parity_repair` + `08d_auth` + `08e_scope` | **98 passed**, 1 pre-existing env failure |
| New M4 regression files | 22 (auth) + 21 (scope) = **43 tests** |
| Live parity runner | **21/21 PASS**, verdict PASS |
| Production pilot (15 scenarios, both populations) | recorded |
| `--perf`, `--post-restart`, `b6restart`, rollback probe | recorded |

Every blocker was implemented failing-test-first; the pre-fix failure counts
are recorded in the table above and in each commit message.

## H. Pilot results (real HTTP, both populations)

| Scenario | Population | Result | Key evidence |
|---|---|---|---|
| P1 normal supported ranking | m4_bundle (labeled) | **PASS** | SiC461 93.944 / SiC463 92.956 / SiC464 92.955 / LM5161 77.948; replay identical; `out_of_cohort: true` |
| P2 hard exclusion | m4_bundle | **PASS** | 16 excluded with rated/required/reason |
| P3 unknown retention | m4_bundle | **PASS (caveat)** | retained, never eliminated; `part: null` (family-grain node) — DQ2 |
| P4 approximate | m4_bundle | **DEGRADED (unchanged)** | 0 approximate entries; M5 emits no `mode` (OH2) |
| P5 interpolation | m4_bundle | **PASS** | disclosed with supporting points |
| P6 no defensible comparison | m4_bundle | **PASS** | empty ranking, fail closed |
| P7 auth failure | direct M4 | **PASS** | 401 / 403; M5 session now also 401 without a token |
| P8a/P8b stale release | m4_bundle | **PASS** | `release_mismatch`, `partial true`, cohort 1577 |
| P8c stale pin after warm cache | m4_bundle | **PASS (was FAIL)** | `release_mismatch` now reported on the warm path — B3 closed |
| P9 service restart | m4_bundle | **PASS** | releases and semantics identical, 659.3 ms |
| P10 in-cohort supported ranking | discovery_cohort | **PASS (empty)** | 1577 sent = received, 0 matched, 0 ranked, 1577 unresolved, assertion holds |
| P11 in-cohort facet narrowed | discovery_cohort | **PASS** | `family=CoolSiC` → 127 sent = received |
| P12 in-cohort hard exclusion | discovery_cohort | **PASS (empty)** | 0 exclusions — no M4 evidence for cohort parts |
| P13 in-cohort unknown-heavy | discovery_cohort | **PASS** | 359 narrowed + 612 unplaced-unknown = 971 sent |
| P14 in-cohort empty population | discovery_cohort | **PASS** | 0 sent = 0 received, `partial false` |

## I. Performance after B5

| Metric | Before | After |
|---|---|---|
| Cold request | 3493.2 ms | **643.3 ms** |
| Session-cache hit | 2897.3 ms | **28.5 ms** |
| Release identity lookup | 2814–2929 ms (uninstrumented, two passes) | **0.4 ms** (instrumented, policy-keyed) |
| In-cohort journeys | n/a | 633.7–678.8 ms |
| Dominant remaining cost | — | `cohort_build_ms` ~604 for 1,577 candidates (in-memory LRU only) |
| Cache-hit timings | stale copy of the miss | honest: actual work + `cached_stage_timings_ms` + note |

## J. Security / access after B4

| Check | Result |
|---|---|
| M4 no bearer / bad bearer / correct bearer | 401 / 403 / **200** |
| M4 evidence route without bearer | **401** |
| M4 with no token configured | **503 `auth_not_configured`** + startup refusal (rc=1) |
| M4 non-local bind without `ENGINEERING_DECISIONS_ALLOW_REMOTE` | **refused at startup** |
| M5 session no credentials / wrong / correct | **401 / 403 / 200** |
| M5 with no session token | **503** (never open) |
| M5 non-local bind with session route and no token | **refused at startup** |
| Constant-time comparison | both hops |
| Loopback development posture | explicit opt-in + loopback bind + loopback client; a configured token always wins |
| Health/release routes | read-only; no longer recompute-and-persist or clobber `meta.policy_identity` |
| Token literals committed | 0 |
| Remaining | no TLS, single static shared token, no rate limiting or body bound, no audit trail — all prerequisites before any non-loopback exposure |

## K. Rollback, restart and failure-mode evidence

| Item | Evidence |
|---|---|
| Rollback (flag off) | session 404, facets 200, M4 health 200, restore 200 — `rollback_check.json`, re-verified in `b4_auth_fail_closed.json` |
| Restart | P9 `PASS`; J8 `PASS` (`b6_restart.json`: identical payload hash, releases, disclosure, counts) |
| Deterministic replay | every journey replays to an identical semantic hash |
| Stale release | cold, warm-from-valid-pin and warm-from-unpinned all report `release_mismatch`, `partial_result true`, cohort preserved |
| M4 unavailable | structured failure, `partial_result true`, cohort preserved |
| Unsolicited M4 candidate | stripped from the ranking and reported as `ranking_population_violation` |
| Oversized population | `candidate_population_too_large`, nothing sent |
| Provenance conflict | left unresolved with an explicit reason |
| Frozen transport given a scope | refuses (`frozen_transport_no_scope`) rather than improvising |
| Missing secret | 503 on both hops; startup refusal |

## L. Unresolved risks

1. **R1** zero in-cohort ranking coverage (data acquisition).
2. **R2** promotion branch lacks the admission and cohort-cache commits.
3. Approximate/scenario mode unreachable through M5 (OH2/DQ6).
4. Cohort build ~604 ms is in-memory only; the persistent cohort cache is on
   another lane (SP2).
5. No TLS, per-client credentials, rate limiting, body bound or audit trail
   (SA1–SA3) — localhost-only must remain until they exist.
6. No structured logs, metrics or alerting; M4's `evidence_availability` and
   seven-way counts are still not projected on the legacy path (OB2/OB4).
7. No supervision or auto-restart; services can die with an empty log (OH1).
8. External-volume dependency with no mount guard (OH4).
9. The shared index was mutated by another lane mid-remediation (release id
   moved); there is no cross-lane coordination lock (OH7).
10. Search-surface family materialization gap: `units.family` 0/356,061 and
    1,882 family-grain units with empty labels (DQ4).
11. All active units remain `verification_state='unverified'`; 58 quotes
    absent from substrate and 120 unverifiable across 5 unresolved stems —
    `machine_verified_advisory` remains the only defensible posture (DQ5).
12. Legacy top-ranked entries still carry
    `adjudication_state=human_review_pending`, `evidence_class=unspecified`,
    `guarantee=false`; disclosed in the payload, no consumer surface renders
    them (DQ7).
13. No production runbook and no single R11 checklist artifact (DR1/DR2);
    no client-facing limitations note (DR5).

## M. Recommended next actions

1. **Owner decision on R1** — authorize curve extraction over the catalog
   population (or admit the M4 pilot parts into catalog + identity). Nothing
   else can make the production path rank.
2. **Owner decision on R2** — authorize merging or cherry-picking `6ba8c66e`,
   `b76f14be`, `4e4398aa`, `0c23a34d` and `49557b74` into the promotion
   branch, then re-run this assessment from the reconciled branch.
3. Run `scripts/prime_discovery_release.py` after any index mutation (it is
   idempotent and verifies invariants), and add it to the deploy runbook.
4. Write `DISCOVERY01_PRODUCTION_RUNBOOK.md`: start order (M4 then M5),
   required env (`ENGINEERING_DECISIONS_ENABLED/_TOKEN/_HOST/_PORT`,
   `SEARCH_ENABLE_ENGINEERING_SESSION`, `SEARCH_SESSION_TOKEN`,
   `M4_BEARER_TOKEN`, `SEARCH_M4_URL`), priming, rollback, restart,
   M4-unavailable behaviour, external-volume dependency.
5. Adopt the persistent cohort cache (`49557b74`) once R2 is resolved, then
   define an SLO and run a concurrency/load test (SP1/SP2).
6. Close OH2 (propagate `mode`/`approximate`), OH3 (request schema
   validation), OB2/OB3 (project M4 coverage accounting and
   `field_mismatches`), OB4 (structured logs, metrics, audit trail).
7. Before any cross-machine exposure: TLS, per-client credentials with
   rotation, rate limiting, request-body bound, decision audit trail.
8. Optionally materialize `parts.family` / `units.family` from the identity
   store — owner-authorized, deterministic, provenance-retained, followed by
   a re-prime (it changes the index release fingerprint).

## N. Promotion decision

**NOT PRODUCTION-READY — BLOCKERS REMAIN**

All six assessed blockers (B1–B6) are closed: failing-test-first regressions,
live HTTP evidence, parity preserved (21/21 PASS, SAME_RELEASE, frozen
baselines byte-matched), full M5 suite 1571 passed with 1 guarded skip, M4
98 passed with 1 pre-existing environment failure.

Two blockers remain, and neither is closable by code in this lane:

- **R1** — the production in-cohort path ranks nothing, because M4's
  curve-evidence universe (20 TI/Vishay pilot parts) has zero intersection
  with the 1,882-part catalog. The gate item "controlled real HTTP pilot
  passes" therefore fails on substance: every structural check passes
  (17/17 B6 checks, 15 scenarios, 21/21 parity), but the normal supported
  cohort journey returns an empty ranking. Closing it requires evidence that
  does not exist; fabricating or inferring it is prohibited.
- **R2** — the promotion branch does not contain the commits that executed
  the admissions or the persistent cohort cache, so it cannot reproduce the
  state it serves; reconciling requires an owner-authorized merge.

Per the directive, the production state transition was **not** executed.
This is returned for owner review with the two decisions listed in
`READINESS_VERDICT.json → owner_decisions_required`.
