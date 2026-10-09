# DISCOVERY-01 PRODUCTION READINESS

Assessment of what remains before DISCOVERY-01 can move from
EXPERIMENTAL-INTEGRATED to PRODUCTION-READY. **Assessment and
evidence-gathering only: no production state was transitioned.** No flag
was flipped, no release promoted, no admission decided, no index written.
The pilot ran read-only against the live integrated path (see "State
hygiene" below for the one by-design write the serving path performs).

Machine-readable companion: `results/discovery01-production-readiness/`
(`READINESS_VERDICT.json`, `pilot_records.json`, `pilot_summary.json`,
`performance_observations.json`, `restart_check.json`,
`rollback_check.json`, `data_quality.json`, `provenance_probe.json`,
`release_identity.json`, `security_posture.json`,
`identity_reconciliation.json`).
Reproduce with `scripts/discovery01_production_pilot.py` (modes: default,
`--perf`, `--post-restart`) and `scripts/discovery01_readiness_probes.py`.

## A. Exact M4 SHA

| Field | Value |
|---|---|
| Repo | `beargallbladder/outcitedharness` |
| Branch | `curve/08c-parity-repair` |
| SHA | `42644eacd779cd91324215fe48c2cb2a78944587` |
| Verified | `git rev-parse HEAD` in the serving worktree returned exactly this SHA; the pilot ran against a service started from it |
| Live bundle | `bundle_sha256 = 0c047e516c10c489134efe226d12e2422651665c4e394379108e9ec10ea4b16f`, `evidence_release = curve-evidence-bundle-v3-5875789dcf3f` — matches the closure record |

## B. Exact M5 SHA

| Field | Value |
|---|---|
| Branch | `discovery/01-final-parity` |
| SHA | `4198e5cd6df92d388474e738113be9820c0e112b` |
| Ancestry | CURVE-08 (`23b17634`) and FACET-03 family signoff (`961ef951`) confirmed ancestors |
| **Not** in ancestry | `0c23a34d` (FACET-03 *series* signoff / promotion) — present on `discovery/01-facet-decision-integration`, absent here |
| Working tree | clean apart from this assessment's two new scripts and results |

The assessed branch is therefore **behind** the branch that carries the
series admission work, while both share the same live databases. See L.1.

## C. Current state

**EXPERIMENTAL-INTEGRATED.** The M5 session route reaches the M4 08C
service over HTTP behind `SEARCH_ENABLE_ENGINEERING_SESSION`, bound to
localhost, serving the release parity verified. Production readiness was
intentionally withheld at closure; this assessment confirms the
withholding is still correct, and identifies two defects not previously
recorded (B3, B5) plus one structural gap (B6).

## D. Discovered R11 criteria (repository-defined, not invented)

| # | Criterion | Source | Status (live-measured) |
|---|---|---|---|
| R11.1 | Admission decisions | `DISCOVERY01_LIVE_PARITY_HANDOFF.md` §6 | **LARGELY DONE at the identity layer, stale in this branch's docs** — 626 family + 237 series memberships `verified` with recorded owner approvals; 15 series→family links still `proposed` |
| R11.2 | Provenance backfill completion | handoff §6; `FACET02_REPORT.md` | **OPEN** — resolver field-path defect (B2) + residual unhashed/pageless data |
| R11.3 | Cohort caching | handoff §6; `DISCOVERY01_ARCHITECTURE.md` | **OPEN** — in-memory only; the dominant cost is an uncached per-request release fingerprint (B5) |
| R11.4 | Auth policy | handoff §6; ARCHITECTURE (auth before cross-machine) | **OPEN** — session route unauthenticated; M4 auth fails open without a token; no non-local bind guard on M4 (B4) |
| R11.5 | Family-grain population | `DISCOVERY01_ARCHITECTURE.md`; `FACET02_REPORT.md` | **OPEN** — `parts.family` **0/1882** despite 626 verified memberships |
| R11.6 | Do not expose mcu/connector facets until coverage exists | `FACET02_REPORT.md` | Honoured — served facets are subcategory/aisle only |

Delta vs. the generic promotion gate supplied for this assessment: the
repository rules are **stricter**, so repository rules govern. Two
additions matter: family-grain population (R11.5) is an explicit upstream
blocker, and the facet-exposure restriction (R11.6) has no generic
equivalent. `QUALIFICATION.md` was checked and is irrelevant (DGX
transport).

Live admission evidence (`identity_reconciliation.json`), from
`facet_identity.db`:

| Relationship | Status | Count |
|---|---|---|
| family (OPN→family) | verified | **626** |
| series | verified | **237** |
| family (series→family) | proposed | 15 |

Owner approval references recorded in `meta`:
`FACET03-OWNER-APPROVAL-2026-10-09-961ef951` ("Approval covers exactly
the 626 proposed …") and `FACET03-SERIES-OWNER-APPROVAL-2026-10-09-0c23a34d`
("exactly the 237 memberships represented by the 15 packet hashes").
19 approval keys total. The verified children are catalog OPNs
(`opn:AIMBF170R1K0M1`, `opn:AIMBG120R040M1`, …).

## E. Blockers

Ranked in fix order: cheapest correctness first, owner/data-scope decisions
last.

### B3 — Stale-release pinning silently bypassed on session-cache hits  *(new; found by this assessment)*

A client that pins an old release can be served newer decision data with
`partial_result: false` and **no failure recorded**.

| Sequence | Cache | `failures` | `partial_result` | Release served |
|---|---|---|---|---|
| P8a pinned-stale, cold | miss | `release_mismatch` | `true` | v3 (cohort preserved 1577) |
| P8b pinned-stale, repeated | miss | `release_mismatch` | `true` | failure results are never cached |
| P8c unpinned first, then pinned-stale | **hit** | `[]` | **`false`** | v3 to a client pinned to v2 |

Root cause: `SessionCache.key(...)` (`harness/search/orchestrator.py:259-266`)
omits `expected_releases`, while `cache.put` correctly stores only
failure-free results (`orchestrator.py:406`). The contract test
`tests/test_discovery01.py::test_release_mismatch_is_structured_not_silent`
passes because it uses a cold cache; the behaviour it enforces — *"stale
decision data never silently used"* — is violated on the warm path.

Smallest safe remediation: include a canonical hash of `expected_releases`
among the cache-key inputs, plus a regression test for the
unpinned→pinned-stale sequence asserting `release_mismatch` and
`partial_result: true` on a cache **hit**.

### B2 — Provenance unresolvable end-to-end (R11.2)

Ranked values cite 4 curve evidence ids; the M5 resolver resolves **0**
documents and reports all 4 unresolved. The evidence exists: the two
`document_sha256` values behind those rankings match **9 and 22 active
units** in the M5 index.

Root cause: M4 08C nests provenance at `entry.source.document_sha256`
(with `page_1based`, `figure_index`, `locator`, `caption`,
`source_artifact`), while `resolve_evidence_refs` reads only top-level
`entry.document_sha256` / `entry.datasheet_sha256`
(`harness/search/orchestrator.py:189-191`), which are `null` on every live
entry.

Residual data gaps (independent of the defect): catalog `7/1617` distinct
docs still `unhashed:`; evidence-grade active units `315,318/356,061 =
88.6%`; `58` quotes genuinely absent from substrate and `120` unverifiable
across the 5 unresolved stems (`BQ2000T, BQ24003, BQ24008, BQ24071,
BQ24401`); all `356,061` active units remain
`verification_state='unverified'`.

Smallest safe remediation: read the `source.*` paths in addition to the
top-level keys, with a regression test asserting non-empty `documents` and
empty `unresolved_evidence_ids` for a supported journey. The 5 unresolved
stems and pageless units stay discovery-only — never grade-promoted on sha
alone.

### B5 — Latency dominated by an uncached, uninstrumented release fingerprint (R11.3)

| Measurement | Value |
|---|---|
| Session latency (cold / warm) | 3493.2 ms / p50 2897.3 ms |
| Instrumented stages total | ~621 ms (`cohort_build` 604.6, `m4_decision` 6–8, `identity_snapshot` ~9) |
| `release_view()` cost, **every** request | 2814–2930 ms |
| Uninstrumented share of wall time | ~85% |

Root cause: `units.release_view` honours the cached release only when the
stored `policy_identity` matches. The index carries
`hybrid-v1.1(sem=0.50,…)` while DISCOVERY-01 serves
`discovery01-v1(facets=…;eligibility=m4-only;curves=m4-only)`, so the cache
never matches and all **443,604** rows are re-fingerprinted per request;
the read-only serving connection can never persist the result
(`harness/search/units.py:530-566`). A session-cache **hit** therefore
still costs ~2.9 s, so R11.3's cohort-caching lever alone would not fix
latency.

Release-identity side effect: the served id
`search-release-v1-3f8b74ece642` differs from the db-cached
`search-release-v1-76d6e320c492`, and the served discovery release is
never written to a ledger — deterministic and stable across restart
(verified), but not durable.

Smallest safe remediation: key the release cache by policy identity (or
memoise `release_view` per serving process with invalidation on index
mutation), then add persistent cohort caching. **Do not** simply prime the
single-row `meta` cache: that overwrites the hybrid search path's policy
identity and invalidates its release.

### B4 — Auth policy not production-grade beyond localhost (R11.4)

| Check | Result |
|---|---|
| M4 no bearer / bad bearer | 401 / 403 ✅ |
| M4 when `ENGINEERING_DECISIONS_TOKEN` unset | **auth disabled entirely (fails open)** |
| M4 token comparison | `!=`, not constant-time |
| M4 non-local bind guard | **absent** (`ENGINEERING_DECISIONS_HOST` unrestricted) |
| M5 session route, no credentials | **200 — served** |
| M5 non-local bind guard | present, fails closed (`SEARCH_ALLOW_REMOTE=1` required) |
| TLS / rate limiting / body-size limit / audit log | none / none / none / none |
| Token literals committed | 0 (env-only) ✅ |

The dangerous combination is M4's missing bind guard plus fail-open auth:
one host variable away from an unauthenticated public decisions endpoint.

Smallest safe remediation: fail closed — refuse to start (or refuse
non-local bind) when no token is configured; mirror M5's guard in M4; use
`hmac.compare_digest`. Then design per-client credentials, TLS and an audit
trail before any non-localhost exposure. Localhost-only remains correct
until then.

### B1 — Family grain not propagated to the catalog (R11.5), and this branch's records contradict the live data

Admission decisions **have** been made and are recorded in the live
identity store (626 family + 237 series `verified`, owner approval refs
dated 2026-10-09 — see D). What has **not** happened:

- `parts.family` is **0/1882** populated — the verified memberships are not
  reflected in the catalog the discovery layer serves from.
- 15 series→family relationships remain `proposed`.
- This branch's documents still assert the opposite: `FACET03_ADMISSION_REVIEW.md`
  ("**Nothing here is approved**"), `FACET03_OWNER_SIGNOFF.md`
  ("no `promote()` called"), `DISCOVERY01_RELEASE_REPORT.md:110` and
  `DISCOVERY01_LIVE_PARITY_HANDOFF.md:89` ("all 626 family memberships
  remain `proposed`"). The series promotion commit `0c23a34d` is not in
  this branch's ancestry at all.

Smallest safe remediation: propagate the verified memberships into
`parts.family` (or serve family grain from the identity store), decide the
15 outstanding series→family links, then reconcile the branch: either
rebase onto / merge `discovery/01-facet-decision-integration` or re-issue
the admission records here, and correct the stale statements so promotion
is never decided from documents that contradict the data.

### B6 — M4 ranking population and M5 discovery cohort are disjoint; the crosswalk is a test fixture  *(new; found by this assessment)*

| Measurement | Value |
|---|---|
| M5 cohort candidates | 1,577 OPNs (catalog is Infineon-family: `AIMBG120R040M1`, `2N7002L`, …) |
| M4 bundle distinct parts | 20 (TI `LM*`/`TPS*` + Vishay `SiC46x`) |
| M4 parts present in the M5 catalog | **0 / 20** (exact and case-insensitive) |
| M4 parts present in the identity DB | **0 / 20** |
| `identity_mapped_exact` | **0** (`identity_unmapped_m4_only` 20, `identity_unmapped_family_nodes` 1) |
| Crosswalk source | `tests/fixtures/discovery01/m4_identity_crosswalk_v1.json` — **test fixture**, 21 candidates, 8,777 bytes (`orchestrator.py:40-41, 289`) |
| Question sent to M4 | requirements/conditions/operating point only — `build_m4_question` transmits **no candidate list**, so the cohort does not constrain the ranking |

Consequence: the session returns a 1,577-part cohort with facets and
guidance, alongside a ranking of parts that cohort does not contain and
the catalog cannot identify. The top-ranked candidate (`SiC461`, 93.944 %)
is not an M5 catalog part; the retained unknown has `part: null`. Facet
narrowing cannot influence the ranking, and no ranked part can be linked to
catalog identity, family, or a datasheet row in M5.

Partial mitigation already present: `cohort_state` discloses
`identity_mapped_exact: 0`, `identity_unmapped_m4_only: 20`,
`identity_unmapped_family_nodes: 1` and a `grain_note` ("grains are never
mixed or double-counted"), and each ranked entry carries
`adjudication_state`, `evidence_class` and `guarantee` (the top entry is
`human_review_pending` / `unspecified` / `false`). So the gap is
**disclosed, not hidden** — but nothing states that ranked parts lie
outside the served cohort, and `discovery_guidance` carries no identity or
grain disclosure.

Smallest safe remediation: replace the fixture with a crosswalk generated
from the identity store, and resolve the population mismatch as an
owner/data-scope decision — either extend M4 curve extraction to catalog
(Infineon) parts, or admit the M4 bundle's TI/Vishay parts into the
catalog and identity layers. Until then, surface an explicit
"ranked parts are outside the discovery cohort" disclosure on every
response.

## F. Already satisfied

- 18/18 original gates and 21/21 extended parity checks
  (`results/discovery01-final-parity/INTEGRATION_CLOSURE_RECORD.json`).
- **Release identity stable across a full restart** (P9 `PASS`): identical
  release ids and byte-identical ranked values after restarting both
  services.
- **Deterministic replay** (P1 `true`): identical semantic hash across
  repeated live calls (timings/cache/session metadata excluded).
- **Rollback verified**: flag off ⇒ session route `404`, facets still
  `200`, M4 unaffected; re-enabling restores service.
- **Fail closed on no defensible comparison** (P6): empty ranking, no
  fabricated values.
- **Hard exclusions correct** (P2): 16 parts excluded with
  rule/rated/required/reason (e.g. LMR33610 — *"VIN rating 36.0 V must be
  >= required 48.0 V"*).
- **Unknowns retained, never eliminated** (P3): `missing_ratings` listed
  with the note *"electrically unconfirmed; retained, never eliminated"*.
- **Interpolation disclosed** (P1, P5): supporting points present; values
  differ correctly between the 0.5 A and 0.3 A operating points.
- **Per-entry honesty fields present**: `adjudication_state`,
  `evidence_class`, `guarantee` on every ranked entry.
- **Cohort preserved** (1,577) on release-mismatch and M4-failure paths.
- **Identity/grain accounting disclosed** in `cohort_state`, including the
  zero-exact-match fact and the grain note.
- M4 bearer auth 401/403/200; tokens env-only; zero token literals
  committed; M5 localhost guard fails closed.
- Identity snapshots are content-addressed and idempotent
  (`ON CONFLICT DO NOTHING`); the same payload yields the same
  `identity-snapshot-v1-872f0c3b0442869c` across restarts.
- 46 readiness-adjacent tests pass; full harness previously 1479 passed +
  1 guarded skip.

## G. Tests run

| Test | Result |
|---|---|
| `pytest tests/test_discovery01.py tests/test_discovery01_m4_projection.py tests/test_search_isolation.py tests/test_facet02_cohort.py tests/test_facet02_axes.py` | **46 passed** in 61.09 s |
| `scripts/discovery01_production_pilot.py` (P1–P8c, live integrated path) | 10 scenarios recorded |
| `scripts/discovery01_production_pilot.py --perf` | miss 3493.2 ms; hit p50 2897.3 ms |
| `scripts/discovery01_production_pilot.py --post-restart` | **PASS** |
| Rollback flag-off probe | **PASS** (404 / 200 / 200) |
| `scripts/discovery01_readiness_probes.py` | data-quality, provenance, release-identity, security, identity-reconciliation artifacts captured |
| `git merge-base --is-ancestor` for CURVE-08 (`23b17634`) and FACET-03 (`961ef951`) | both in ancestry; `0c23a34d` is not |

No production code was modified. New files are the two scripts above and
the results directory.

**State hygiene.** Catalog and search index were opened `mode=ro`
throughout; no flag, release, admission, or index mutation was performed.
The serving path does perform one by-design write: it persists a
content-addressed identity snapshot row into `facet_identity.db.releases`
(idempotent — identical content returns an identical `snapshot_id`), which
is inherent to exercising the integrated endpoint and was already the
behaviour during parity runs. All three databases are WAL-mode; `catalog.db`
mtime moved during the window with a 0-byte WAL and unchanged size, which
is WAL/shared-memory housekeeping rather than an assessment write.

## H. Pilot results

| Scenario | Result | Key evidence |
|---|---|---|
| P1 normal supported ranking | **PASS** (with B6 caveat) | 4 ranked: SiC461 93.944, SiC463 92.956, SiC464 92.955, LM5161 77.948; releases `search-release-v1-3f8b74ece642` + `curve-evidence-bundle-v3-5875789dcf3f`, transport `http`; replay hash identical; ranked parts are not catalog parts |
| P2 hard exclusion | **PASS** | 16 parts excluded with rated/required/reason |
| P3 unknown retention | **PASS with caveat** | 1 unknown retained, never eliminated — but `part: null`, `identity_class: unmapped`, so it cannot be shown or investigated (DQ2) |
| P4 approximate scenario | **DEGRADED** | `approximate_scenario_entries` present but **0 entries**; `comparison.approximate=true` never reaches M4 (`build_m4_question` emits no `mode`) and nothing warns the client (OH2) |
| P5 interpolation | **PASS** | interpolation `disclosed` with supporting points; values differ correctly from P1 |
| P6 no defensible comparison | **PASS** | empty ranking, fail closed, no fabricated values |
| P7 auth failure | **PASS** | direct M4: 401 without bearer, 403 with bad bearer; the M5 session route itself needs no credentials (B4) |
| P8a stale release (cold) | **PASS** | `release_mismatch`, `partial_result: true`, cohort preserved 1577 |
| P8b stale release (repeat pinned) | **PASS** | mismatch re-reported; failure results never cached |
| P8c stale pin after unpinned cache | **FAIL** | cache hit ⇒ `failures: []`, `partial_result: false` (B3) |
| P9 service restart | **PASS** | release identity stable, semantics identical, values byte-identical |

## I. Performance observations

- Per-request wall time 2.84–3.49 s. Instrumented stages account for
  ~621 ms; `release_view()` accounts for ~2.9 s and is absent from
  `timings_ms` (OB1).
- Session-cache hits save only the cohort build (~0.6 s) and still cost
  ~2.9 s. On a hit, `timings_ms` replays the **original miss's** numbers,
  so the reported breakdown is misleading (OB1).
- Cohort build 604 ms for 1,577 candidates; cache is in-memory LRU
  (`max_entries=64`), lost on restart (SP2).
- Fingerprint cost is O(unit rows) — 443,604 — and grows with the index
  (SP3).
- Coverage accounting is dropped from the session envelope: M4 reports 21
  candidates considered, 14 hard-eligible, **4 with comparable curve
  evidence**, 10 missing curve evidence, 11 requiring verification, 123
  evidence rows attached. Consumers cannot see why only 4 ranked (OB2).
- No SLO, concurrency test, or load test exists (SP1); both hops are
  single-process `ThreadingHTTPServer`.

## J. Security / access observations

Blocking items are in B4. Additionally: no TLS on either hop (bearer token
in clear text — acceptable on loopback only); one shared static token with
no rotation, per-client identity, or revocation; no audit trail of which
client received which decision under which release; non-constant-time token
comparison; no rate limiting or request-body bound. Positives: M5's
non-local bind guard fails closed with an explicit message; tokens are
env-only with zero literals committed; the assessment token default was
removed from the pilot script so no credential-looking value is stored in
the repo.

## K. Rollback evidence

`results/discovery01-production-readiness/rollback_check.json`:

| Probe | HTTP | Expectation |
|---|---|---|
| `POST /v1/discovery/engineering-session`, flag **off** | **404** | route removed entirely |
| `GET /v1/discovery/facets?category=power`, flag off | **200** | rest of API unaffected |
| M4 `/v1/engineering/health` during flag-off | **200** | M4 unaffected |
| Session route after re-enabling the flag | **200** | service restored |

Verdict `PASS`: rollback is a single env-var change, removes the integrated
route completely (no partial or stale decision path stays reachable), and is
reversible. Restart behaviour is separately verified by P9.

## L. Unresolved risks

1. **Branch/data divergence.** The live databases reflect admissions and a
   series promotion (`0c23a34d`) that this branch neither contains nor
   describes; four documents here still state that nothing was promoted.
   Any promotion decision taken from this branch's paperwork would be
   wrong. Reconcile before assessing again.
2. **B1–B6** above. B6 and the catalog side of B1 need an owner data-scope
   decision, not just code.
3. Approximate/scenario comparisons are silently unavailable through the
   integrated path (OH2/DQ6): a client asking for approximation receives
   exact-match-only results with no warning.
4. The top-ranked candidate carries `adjudication_state:
   human_review_pending`, `evidence_class: unspecified`, `guarantee: false`.
   The flags are present in the payload, but no consumer surface displays
   them, so a user could read 93.944 % as settled.
5. Unknown candidates are retained correctly but unidentifiable (DQ2).
6. Serving depends on an external volume
   (`/Volumes/M5_4TB/extract-results`) opened per request with no mount
   guard; an absent volume yields a generic `500 orchestration_failed`
   (OH4).
7. No supervision: services can terminate silently with an empty log
   (observed during this assessment) — no auto-restart, no alerting (OH1).
8. All active units remain `verification_state='unverified'` (DQ5), so
   `machine_verified_advisory` is the only defensible posture.
9. Unknown or unsupported request fields are silently ignored (OH3).

## M. Recommended next actions

1. **Fix B3** (cache key includes `expected_releases`) + regression test —
   smallest change, removes a correctness defect in a safety path.
2. **Fix B2** (resolver reads `source.*`) + regression test asserting
   resolved documents and zero unresolved ids for a supported journey.
3. **Fix B5** (policy-keyed or memoised release fingerprint), re-measure,
   then implement persistent cohort caching (R11.3) and set an SLO.
4. **Fix B4** (fail-closed auth when no token; M4 bind guard;
   constant-time compare), then design cross-machine authN/Z, TLS and audit
   logging.
5. **Close B1**: propagate the 626 verified memberships into
   `parts.family`, decide the 15 outstanding series→family links, and
   reconcile this branch with `discovery/01-facet-decision-integration`
   plus the four stale documents.
6. **Take B6 to the owner**: choose the population strategy (extend M4
   curve extraction to catalog parts, or admit the M4 bundle's parts into
   catalog + identity), replace the fixture crosswalk with one generated
   from the identity store, and add an explicit out-of-cohort disclosure to
   every response in the meantime.
7. Add observability (OB1–OB5): instrument `release_view`, project M4
   coverage accounting and refusal reasons, surface `field_mismatches`, add
   structured logs/metrics and a decision audit trail.
8. Write `DISCOVERY01_PRODUCTION_RUNBOOK.md` (DR1): start order (M4 then
   M5), env vars, release priming, flag-off rollback, restart behaviour,
   the M4-unavailable partial-result path, and the external-volume
   dependency. Publish one authoritative R11 checklist (DR2) and a
   served-contract limitations note (DR5).
9. Re-run this assessment's pilot and probes after each fix; promotion
   requires all six blockers closed and a clean re-run.

## N. Promotion decision

**NOT PRODUCTION-READY — BLOCKERS REMAIN**

Three of the four repository-defined R11 criteria are open outright
(provenance completion, cohort caching, auth policy) and the fourth —
admission decisions — is satisfied in the live identity store but not
propagated to the catalog, not reflected in this branch, and contradicted
by this branch's own documents. This assessment also found three items that
were not previously recorded: the stale-pin cache bypass (B3), the uncached
per-request release fingerprint that dominates latency (B5), and the
disjoint M4/M5 part populations served through a test-fixture crosswalk
(B6). Green parity is necessary but not sufficient.

Per the assessment directive, no production transition was performed and
none is recommended. The experimental flag posture and localhost-only
binding should remain exactly as they are until B1–B6 are closed and the
pilot re-runs clean.
