# DISCOVERY-01 — M4 Remediation Handoff (PRD: five required outcomes)

Branch: `curve/08c-parity-repair` · Commit: see `git log -1` (this file lands
with it) · Worktree: `/Users/samsonkim/Dev/Harnessv1-curve08c` (base
`curve/08b-decision-api` @ `18893df`) · **NOT pushed, NOT merged — awaiting
owner review.**

## Premise corrections (evidence, not argument)

1. **"`18893df` is confirmed phantom" is disproven.** `git cat-file -t
   18893df` → `commit` in `beargallbladder/outcitedharness`
   (`~/Dev/Harnessv1-curve08b`, branch `curve/08b-decision-api`, local-only).
   Its tree builds, its 47 tests pass, and its service is what now passes
   M5's parity suite. Per this PRD's own rule ("do not accept a claimed
   missing artifact until you have searched …"), the artifact was found.
   Treating it as non-authority would discard the passing implementation.
2. **The "current verdict" block describes the pre-08C state** (M4 serving
   v2/35dbd73c via the outcited-ai adapter, 13/18, `ranked: []`). That
   service was replaced on the 8793 tunnel mouth by the real harness
   engine during 08C. Everything below is measured against the CURRENT
   live service.

## A. Branch / commit

`curve/08c-parity-repair` (isolated worktree; supersedes nothing; 08B
branch + worktree preserved intact @ `18893df`).

## B. Files changed (this remediation, on top of the 08C repair commit)

- `harness/electronics/decision_api.py` — auth contract: 401 (missing
  bearer) vs 403 (present-but-invalid), previously both 401.
- `tests/test_curve08c_parity_repair.py` — +4 auth locks (hermetic
  env save/restore; order-independence verified both orders).
- `results/discovery01-final-parity-08c/` — evidence transcripts (see F-M).
- `DISCOVERY_08C_REMEDIATION_HANDOFF.md` — this file.

(08C repair commit already delivered: the question-dialect branch, 5 parity
locks, contract addendum, assignment-file repoint.)

## C. Tests added/changed

Added: `AuthContractTests` (4). Changed: none of M5's; none of the ten
frozen locks (`tests/python/test_engineering_decisions_08b.py` @
`91e44f04` re-run: **10/10 green, worktree clean**).

## D. Before / after live behavior (M4 `127.0.0.1:8793`)

| | before (08B adapter) | after (this remediation) |
|---|---|---|
| service | outcited-ai projection @ `91e44f04` | harness engine, question dialect |
| release | v2-3b5658b629cc / 35dbd73c | **v3-5875789dcf3f / 0c047e51…b16f** |
| ranked | `[]` | J-A 4, J-B 2, J-C 1 |
| eligibility | 1777/0/0 | verdict triple 14/6/1 (J-A) |
| schema | fae-engineering-decisions-v1 | **harness.electronics-decision-answer.v1** |
| bad bearer | 401 | **403** (missing stays 401) |

## E. Release ids / fingerprints (both sides)

- M4 live: `curve-evidence-bundle-v3-5875789dcf3f`, sha256
  `0c047e516c10c489134efe226d12e242265166c5c4e394379108e9ec10ea4b16f`
  (health + every journey response).
- M5 pinned: identical (frozen baseline release).
- Relation: **SAME_RELEASE** (v2 values never mixed; v1/v2/v3 bundles
  byte-preserved, 08B locks green).

## F. J-A / J-B / J-C live outputs + hashes (real HTTP, over the tunnel)

| journey | new response sha256 (first 16) | ranked | old baseline |
|---|---|---|---|
| J-A 48V→5V @0.5A | `7ec66df49c7c377d` | SiC461 93.94 / SiC463 92.96 / SiC464 92.95 / LM5161 77.95 | `7ceff21b…` |
| J-B 48V→5V @3A | `24a427eab0aedd68` | SiC463 93.70 / SiC462 90.76 | `0d174023…` |
| J-C 12V→3.3V @0.1A | `e5e3a3e99bc0df75` | LMR36502 84.77 | `ccfdb900…` |

Hash-change record (per spec): previous hashes were of the WRONG service
(the v2 adapter); cause = the 8793 tunnel mouth was repointed to the real
harness engine and the question dialect added; the semantic contract
changed from the adapter's projection to the frozen `answer.v1` contract —
which is exactly what M5's baseline freezes (all three journeys
`baseline_match: true` against `m4_frozen_journeys_v1.json`). Full bodies:
`results/discovery01-final-parity-08c/08c-journey-J-{A,B,C}.json`.

## G. Ranking live-observable

J-A/J-B/J-C ranked lists above, over real HTTP, deterministic (engine has
no wall-clock; native fixtures byte-stable). No cohort widening: same
21-candidate frozen cohort (14/6/1 triple); ranking participants are
rating-eligible members of that cohort only.

## H. The 3 A exclusions, live

J-B `hard_eligibility.ineligible` contains LM5161 (rule `iout_min`:
required 3.0, rated 1.0) and SiC464 (rated 2.0), each with rule + required
+ rated + scope + cited evidence. Runner checks `B:LM5161_ineligible`,
`B:SiC464_ineligible` PASS.

## I. Unknown retention, live

Every journey carries 1 unknown candidate (`missing_ratings` listed,
"electrically unconfirmed; retained, never eliminated"). Runner check
`*:unknown_retained` PASS on all three journeys.

## J. Interpolation disclosure (machine-readable, live)

Every condition-matched entry carries an `interpolation` block:
`method: log10_interpolation_on_digitized_polyline`, `supporting_points`
(the two bracketing samples), `operating_point`, `conditions`, and
`uncertainty` (fit residuals, retained-point fraction, `human_signed:
false`, resolution). Full example in
`results/discovery01-final-parity-08c/08c-journey-J-A.json`
(SiC461 entry). Lock: `test_journey_a_frozen_values_regenerated` asserts
presence; runner asserts per-entry `evidence_id`.

## K. Approximate-mode example (live)

Request mode `condition_matched+approximate` returns 26 entries under
`comparison.approximate_scenarios`, each labeled
`mode: approximate_scenario`, `reason: condition_mismatch` (with the exact
mismatched key/curve/required values), `guarantee: false`, and
`note: "scenario comparison requested by the engineer; NOT
condition-matched evidence"`. Approximate entries never enter the
condition-matched ranking. Transcript:
`results/discovery01-final-parity-08c/response-approximate-mode.json`.

## L. Auth evidence (live, this remediation)

- no bearer → **401** `{"code":"unauthorized"}`
- wrong bearer → **403** `{"code":"forbidden"}`
- valid bearer (env-provided `ENGINEERING_DECISIONS_TOKEN`) → **200**
- M5 side: env-only `M4_BEARER_TOKEN` (their client, pre-existing).
- No credential material committed; examples use env references.
- No auth bypass added; localhost bind + feature flag unchanged.

## M. Full parity matrix (M5's runner, unmodified, current service)

`results/discovery01-final-parity-08c/live-parity-report-08c-final.json`:
**verdict PASS, 21/21 checks, exit 0.** The frozen 18-check composition is
a subset (in SAME_RELEASE mode the runner emits 3 additional
`values_match_frozen_baseline` checks — all pass). All three journeys:
`ok`, `baseline_match: true`, `m4_evidence_release: v3-5875789dcf3f`.

Known M5-side tooling note (NOT an M4 failure, their code untouched by us):
their pytest wrapper `test_discovery01_live_parity.py` parses the runner's
stdout, but their runner caps stdout at 4000 chars
(`print(text[:4000])`, scripts/discovery01_live_parity.py main()). The
wrapper therefore cannot parse any full report, passing or failing; the
runner + `--out` file is the authority (used for all evidence above). Left
for M5 to reconcile; no guard weakened on either side.

## N. Test-suite results

- Repair branch: `test_curve08c_parity_repair.py` + `test_curve08b.py`
  = **56/56 green, both orders** (9 08C locks incl. 4 auth + 47 08B).
- Ten frozen locks @ outcited-ai `91e44f04`: **10/10**, worktree clean.
- Broader slice: 2 failures, both PRE-EXISTING at base `18893df`
  (proven by stashing this diff and re-running):
  `test_discovery_api.py::test_design_lifecycle` (sqlite OperationalError,
  environmental) and `test_electronics_admission.py::
  test_complete_train_package_is_admitted_to_cr_bundle`.

## O. Promotion evaluation (fail closed, condition by condition)

| gate condition | state | evidence |
|---|---|---|
| live checks passed (18) | TRUE | PASS 21/21 ⊇ 18, zero failures (M) |
| same parity-target release family | TRUE | v3/v3, sha 0c047e51 (E) |
| ranked observable | TRUE | J-A/B/C (F,G) |
| exclusion semantics observable | TRUE | LM5161+SiC464 (H) |
| unknown retention observable | TRUE | 1 per journey (I) |
| interpolation contract present + tested | TRUE | (J) |
| approximate contract present + tested | TRUE | (K) |
| auth contract current | TRUE | 401/403/200 (L) |
| no guard weakened | TRUE | M5 worktree clean @ 7c561441; suites unmodified |
| no frozen lock changed | TRUE | 91e44f04 clean, 10/10 |
| no v2/v3 mixing | TRUE | single-release engine, v1/v2/v3 immutable |
| all journeys under bounded timeout/retry | TRUE | runner policy, exit 0 |

**Verdict: PASS — 18/18 LIVE PARITY — ELIGIBLE FOR EXPERIMENTAL-INTEGRATED.**

The flip itself is NOT performed: per instruction, we report branch +
commit and wait for owner review before any integration-state transition
(and the M5-side flip is M5's to make on `discovery/01-final-parity`).
