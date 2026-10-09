# DISCOVERY01 RELEASE REPORT (D11)

Branch `discovery/01-facet-decision-integration` @ (this commit).
Localhost pilot only; feature-flagged; nothing pushed/merged/deployed.

## Classification: **EXPERIMENTAL-INTEGRATED**

Transition executed 2026-10-09 under owner approval
`DISCOVERY01-OWNER-APPROVAL-2026-10-09-42644ea`, after M5 independently
verified M4 remediation commit
`42644eacd779cd91324215fe48c2cb2a78944587` (remote
`beargallbladder/outcitedharness` `refs/heads/curve/08c-parity-repair`,
pushed, no rebase/squash/amend): the unmodified M5 live-parity runner
returned exit 0 with 21/21 checks (SAME_RELEASE mode; the six
divergent-mode checks are structurally inapplicable when releases match),
release relation SAME_RELEASE against bundle v3 `…5875789dcf3f` /
`0c047e51…`, live ranked values 93.94/92.96/92.95/77.95 at 48 V→5 V
@0.5 A, cited 3 A exclusions for LM5161 (1.0 A) and SiC464 (2.0 A),
unknown retained, per-entry interpolation disclosure, 26 labeled
approximate-scenario entries none ranked, auth 401/403/200 with env-only
secrets, M4 frozen locks 10/10 at `91e44f04`, M5 runner+fixtures
byte-unchanged. Still feature-flagged and localhost-only. **Not
production-ready** (R11 list outstanding). Prior CONTRACT-READY and
REFUSED records remain in git history, unaltered.

## Journey results (frozen-contract transport, regenerated from the M4
evidence bundle — never hardcoded)

| Journey | Result | PRD expectation | Status |
|---|---|---|---|
| A: 48V→5V @0.5A | SiC461 93.944 / SiC463 92.956 / SiC464 92.955 / LM5161 77.948 %; level cross_manufacturer; 14 eligible / 6 ineligible (each with rule+rated+required+scope) / 1 unknown retained | 93.94 / 92.96 / 92.95 / 77.95 | ✓ exact |
| B: load step 3A | eligible 14→4; LM5161 hard-ineligible ("1.0 A ≥ 3.0 A" rule cited); SiC464 excluded; fresh ranking (SiC463 93.70 / SiC462 90.76, within_family) — 0.5A ranking NOT reused; unknown retained | LM5161 + SiC464 ineligible, no stale reuse | ✓ |
| C: 24V→3.3V @1A | SUCCESS with zero condition-matched curves; 11 parametric eligible retained; suggestions derived from M4 counts: 11 missing-evidence + 1 noncomparable + 1 unknown = 13 | 11 eligible, 13 suggestions, zero comparable, success not error | ✓ |

## Identity crosswalk coverage (live measurement, journey A cohort)

21 M4 candidates: 16 opn-grain (exact string-identity joins where the M5
catalog carries the OPN), 4 device-grain m4_only (SiC46x — no M5 catalog
identity; visible, unmapped, never fuzzy-joined), 1 family node
(unmapped by design). 0 ambiguous. No similar-name substitution exists in
code (regression-tested).

## Facet narrowing (M5 side, live catalog)

Power cohort 1,577 → vendor=infineon 898 → VDS 500-1000V 263 → RDS(on)
20-50mΩ 69 (FACET-02/03 machinery reused unchanged; subcategory
crosswalk buck→switching-regulators explicit and reported).

## Latency (measured, frozen transport, n=25)

| Path | p50 | p95 |
|---|---|---|
| cache hit | 88 ms | 94 ms |
| cache miss | 742 ms | — (n=1) |

Stage breakdown (miss): cohort_build 646 ms (dominant — the known
FACET-02 per-request rebuild blocker), identity snapshot 10 ms, crosswalk
<1 ms, M4 frozen decision <1 ms, evidence resolution 0.1 ms (after
replacing LIKE scans with exact PK lookups: 816 ms → 0.1 ms). Live M4
decision latency unmeasured (service down); HttpTransport carries bounded
timeout (10 s) + 1 retry.

## Cache (R8)

Release-keyed (category, subcategory, grain, constraints, identity
snapshot, evidence release, policy, transport class); bounded LRU (64);
deterministic eviction; **errors never cached** (tested); key computed
before expensive work (hit path skips cohort build).

## R10 test mapping (16 new tests, all green)

1 same counts regardless of doc count (FACET-02/03 regressions) ✓ ·
2 no family/OPN double count ✓ · 3 proposed never verified (identity
status flows, promote() gated) ✓ · 4 no string joins (crosswalk test) ✓ ·
5 unknown≠ineligible (journeys A/B) ✓ · 6 no unsupported eliminations
(M4 rules carry evidence; unknown retained) ✓ · 7 condition-mismatch
never compared (M4-owned; C returns zero comparable) ✓ · 8
typical-never-guaranteed (M4 laws passthrough + advisory suggestions) ✓ ·
9 no stale results after load change (B≠A fingerprints) ✓ · 10 M4 failure
→ partial_result + structured error, cohort preserved ✓ · 11
release-mismatch structured, never silent ✓ · 12 provenance never
upgraded (resolver note; FACET-03 states intact) ✓ · 13 SI validity
(FACET-02 axes suite) ✓ · 14 sparse facet can't win (coverage floor) ✓ ·
15 bucket boundaries half-open ✓ · 16 worktree isolation ✓ · 17
localhost-only bind ✓ · 18 deterministic replay (byte-equal) ✓ · 19 cache
invalidation on release/constraint change ✓ · 20 feature-off = 404,
existing API untouched ✓.

Full suite: **1,474 passed, 0 failed** (1,458 FACET-02/03 regressions +
16 new).

## Evidence-reference resolution rate

Journey A: 4/4 comparison evidence_ids carried through; sha-keyed
eligibility evidence (SiC461 admission packet quote) resolved to indexed
provenance views (document sha, pages, grades, verification states — never
filesystem paths, verified by leak test). M4 bundle evidence ids are M4's
namespace: unresolved-in-M5 ids are listed honestly
(`unresolved_evidence_ids`), not faked.

## Blockers / remaining

1. **Live M4 service** — :8793 down; `18893df`/OpenAPI absent on M5.
   Live contract test is the single gate to EXPERIMENTAL-INTEGRATED.
2. **Cohort build 646 ms/miss** — release-keyed cohort cache (PRD R8
   "prepare") is specified but not built; hit-path already avoids it.
3. **M5 catalog coverage** — buck subcategory has 17 M5 candidates vs
   M4's 21-candidate decision set; catalog growth (mcu/connectors) still
   gated upstream.
4. **Family admissions** — 626 memberships remain `proposed` (owner
   sign-off pending); sessions render status honestly either way.
5. SiC46x devices have no M5 catalog identity (m4_only) — visible in
   every response; acquisition/catalog lane decision.

## Production-readiness judgment

**CONTRACT-READY for localhost pilot behind
`SEARCH_ENABLE_ENGINEERING_SESSION=1`.** Do not expose to the frontend
production surface until: live M4 verification, cohort caching, and the
owner's FACET-03 admission decisions. All hard invariants (grain honesty,
unknown taxonomy, no fabricated joins, no eligibility on M5, release
mismatch detection) are regression-tested.
