# CURVE-08C — Decision Parity Repair Report

Date: 2026-10-09 · Branch `curve/08c-parity-repair` (isolated worktree
`/Users/samsonkim/Dev/Harnessv1-curve08c`, base = `curve/08b-decision-api` @
`18893df`) · **NOT pushed, NOT merged** (per PRD authorization rule).

## Outcome

**DISCOVERY-01 live parity: PASS 21/21 checks, exit 0** — M5's suite
(`discovery01_live_parity.py` @ `7c561441`, UNMODIFIED) run against the real
M4 HTTP service through the authenticated reverse tunnel
(`m5:127.0.0.1:8793 -> m4:127.0.0.1:8793`). All three journeys
`baseline_match: true` against the frozen v3 baseline. Verdict state:
**EXPERIMENTAL-INTEGRATED** (M4 side; M5 owns the flip on their branch).

Transcript: `results/discovery01-final-parity-08c/live-parity-report-08c.json`
(copied verbatim from the M5 run).

## R0 — What the failure evidence actually was (and a correction)

- `DISCOVERY01_FINAL_PARITY.md` + `results/discovery01-final-parity/` live on
  **M5's** worktree `/Users/samkim/Harnessv1-disc01final` @ `7c561441`
  (branch `discovery/01-final-parity`, base `6d809e55`). Retrieved read-only.
- The real CURVE-08B implementation is **outcitedharness**
  (`beargallbladder/outcitedharness`), checkout
  `/Users/samsonkim/Dev/Harnessv1-curve08b` @ `18893df` — **not** the
  outcited-ai repo my 08B report searched. CORRECTION to that report: the
  claim "18893df never existed in any reachable store" was wrong-scoped — the
  org-wide search missed the personal-namespace harness repo. M5's §1 note
  ("their search missed that worktree") is accepted.
- M5's 13/18 run tested MY outcited-ai adapter (`91e44f04`, v2 fae-query
  engine) because the tunnel mouth served it. The five failures
  (A/B/C unknown_retained, B:LM5161_ineligible, B:SiC464_ineligible) were
  the v2 adapter's empty qualification — the real engine produces all of
  them as frozen tests.

## R1 — v3 evidence (served, verified)

The real service serves `curve-evidence-bundle-v3-5875789dcf3f`
(bundle sha `0c047e516c10c489134efe226d12e242265166c5c4e394379108e9ec10ea4b16f`)
natively — verified on `/v1/engineering/health` and in EVERY decision
response (top-level `evidence_release` + `bundle_sha256`). v1/v2 bundles
byte-preserved (08B lock, re-run green). v2/v3 numbers never mixed: the
engine computes from v3 only; M5's runner re-verified
`values_match_frozen_baseline` per journey now that releases agree.

## R2 — hard eligibility (live, journey B @3 A)

LM5161 **ineligible** (rated 1.0 A < required 3.0 A) and SiC464
**ineligible** (rated 2.0 A < 3.0 A), each with rule + required + rated +
scope + cited evidence; 1 unknown retained (`missing_ratings`, never
eliminated). Locked by `tests/test_curve08c_parity_repair.py`.

## R3 — condition-matched ranking (live, journey A @0.5 A)

SiC461 93.94 / SiC463 92.96 / SiC464 92.95 / LM5161 77.95 at
`cross_manufacturer`, regenerated from evidence by the engine (no hardcoded
serving values — the lock test asserts the values the ENGINE produces, and
the 08B fixtures were frozen from the same producer). Every entry carries
candidate identity, conditions, value, unit, comparison level, evidence
reference, and review status.

## R4 — comparison semantics

Per-entry interpolation disclosure (supporting points, method, valid range,
conditions, uncertainty, limitations), separately-labeled
`approximate_scenario_entries` (empty for A; never blended), refusal
reasons, and `human_review_pending` review states — all present in the
answer.v1 envelope (see frozen fixture or live transcript).

## R5 — contract reconciliation

The ten recorded discrepancies split: (1) request-wrapper + response-shape
differences = **lossless adapter** (the single dialect branch, below);
(2) hard_eligibility / interpolation / approximate / seven-way counts =
**verified engine output already present** in the real service (M5's
projection no longer needs to synthesize them — its mismatch list is empty
when the answer schema matches); (3) nothing unresolved. Auth: FAE-style
bearer via `ENGINEERING_DECISIONS_TOKEN`; M5 sends it via
`M4_BEARER_TOKEN` env (never committed; the token used for the parity run
is a session-scoped probe key).

## The repair (one bounded change)

`harness/electronics/decision_api.py::handle_decision_request` gained ONE
dialect branch: bodies of the shape `{"question": {...}}` are routed to the
canonical `answer_question()` producer verbatim — the exact function M5's
frozen fixtures were generated from. **Zero engine edits; zero new
engineering logic.** The native decision-api.v1 path is untouched and
byte-identical to its frozen fixtures (regression-locked).

Tests: `tests/test_curve08c_parity_repair.py` (5 locks: frozen values,
hard ineligibility + rules, ranking exclusions, interpolation disclosure,
native byte-truth). Full repair-branch suite: **52/52 green**
(5 new + all 47 08B tests; `curve08b.assignment.json` updated to name the
live repair branch per the isolation lock).

## R6 — live verification detail

Runner: M5's `scripts/discovery01_live_parity.py` unmodified; environment
`M4_DECISION_URL=http://127.0.0.1:8793`, `M4_BEARER_TOKEN=<session probe>`;
transport real HTTP over the reverse SSH tunnel (authenticated, loopback on
both ends, no public exposure, no production routing change). Negative
matrix from M5's earlier run stands (401/403, bounded timeout,
release-mismatch structured + cohort preserved, unreachable => NOT_RUN).

## Preservation

- v1/v2/v3 bundles immutable (byte-identical, 08B locks green).
- No family admission (626 proposed memberships untouched, promote() has
  zero call sites), no gold promotion, no catalog writes, no production
  routing changes, no other-agent worktree modifications.
- Both source branches preserved: `curve/08b-decision-api` @ `18893df`
  (worktree intact), M5 `discovery/01-final-parity` @ `7c561441` (read-only).

## Deliverables

1. this report
2. updated decision service (dialect branch; worktree Harnessv1-curve08c)
3. v3 support verified (R1 fingerprints above)
4. regression tests (test_curve08c_parity_repair.py, 5 locks)
5. machine-readable contract addendum:
   `contracts/engineering-decision-api-v1/question-dialect-v1.json`
6. live transcripts: `results/discovery01-final-parity-08c/`
7. M5 handoff update: mailed (agentmail) with endpoint, fingerprints,
   verdict, and the correction
8. remaining parity gaps: **none known** — 21/21; the M5-side flip to
   EXPERIMENTAL-INTEGRATED is theirs to make.

## Deployment state

Experimental only: `ENGINEERING_DECISIONS_ENABLED=1` service bound to
`127.0.0.1:8793` on the M4, reachable from the M5 through the authenticated
reverse tunnel. Branch committed locally; **no push, no merge**.
