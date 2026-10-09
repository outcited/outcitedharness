# DISCOVERY-01 Promotion Verification Record — REFUSED

Verifier branch/commit: `discovery/01-final-parity` (this commit),
worktree `/Users/samkim/Harnessv1-disc01final`.
Owner approval reference: `DISCOVERY01-OWNER-APPROVAL-2026-10-09-42644ea`.
Verification timestamp: 2026-10-09T17:04:23Z.
State transition performed: **NONE**. Resulting state: **CONTRACT-READY**
(unchanged). No parity guard, frozen lock, fixture, or runner semantics
were modified by this verification.

## Gate 0 — commit identity (precondition for gates 1–10)

| probe | command | result |
|---|---|---|
| M4 remote branches | `git ls-remote origin` (outcited.com), 156 heads | `curve/08b-decision-api@91e44f04` present; **no `curve/08c-parity-repair`, no `42644ea`** |
| harness remote | `git ls-remote github` (outcitedharness) | no 08c / 42644ea refs |
| 13 local git stores | `git cat-file -t 42644ea` in every worktree (Harnessv1*, outcited*, repos/outcited.com{,-shadow}) | **absent in all** |
| M4 worktrees on disk | `/Users/samkim/outcited-*`, `/Volumes/M5_4TB/repos/outcited*` | only `outcited-08b-run@91e44f0` (detached), `outcited-curve06`, main clone |

The remediation commit is **local-only and unreachable from the
verifier**. Verification items 1–10 of the owner instruction require
evidence "from the exact M4 remediation commit"; none can be produced.
Per the instruction's own PUSH/HISTORY rule ("The integration decision
must reference the pushed immutable M4 commit, not merely a local
worktree state"), a durable promotion record is impossible today even if
the code were perfect.

**Gate 0 result: FAIL — PROMOTION REFUSED at the precondition.** All
downstream gates are recorded UNVERIFIABLE, not passed.

## Gate-by-gate status (as far as observable without 42644ea)

| # | requirement | status | evidence |
|---|---|---|---|
| 1 | commit identity / ancestry / no lock weakening | UNVERIFIABLE | commit unreachable |
| 2 | SAME_RELEASE v3 both sides | UNVERIFIABLE | live tunnel probe below; no authenticated access to 08C |
| 3 | ranked non-empty, values live, nothing borrowed | UNVERIFIABLE | same |
| 4 | three A exclusions over real HTTP with cited rules | UNVERIFIABLE | same |
| 5 | unknown retention | UNVERIFIABLE | same |
| 6 | interpolation disclosure machine-readable | UNVERIFIABLE | same |
| 7 | 26 labeled approximate entries, never ranked | UNVERIFIABLE | same |
| 8 | auth 401/403/200, env-only secrets | PARTIAL (08b only) | 401/403 verified live against `91e44f04`; 08C auth repair unverifiable |
| 9 | frozen locks 10/10 @91e44f04, M5 locks unchanged | M5 side PASS | M5 fixtures/runner byte-identical on this branch; suite 1479+1skip |
| 10 | M5 runner unmodified, exit 0, 18/18 original gates | **FAIL (last executable run)** | against the only reachable M4 code (`91e44f04`): verdict FAIL 13/18, release DIVERGENT (v2 served), ranked empty |

## Live-path probes (current tunnel state)

- `GET 127.0.0.1:8793/healthz` → `{"error": {"code": "unknown_route"}}`
  (contract shape changed vs the 08b adapter; service behind M4
  credentials).
- `POST 127.0.0.1:8793/v1/engineering/decisions` without bearer → **401**.
- M5 holds no M4 bearer key; the claimed 08C live behavior (SAME_RELEASE,
  ranked values, 26 approximate entries) therefore cannot be observed
  independently from this environment. Assertion is not verification.

## Premise corrections — evidentiary status

1. "`18893df` is not phantom; exists in beargallbladder/outcitedharness":
   `gh api repos/beargallbladder/outcitedharness/commits/18893df` →
   **404 Not Found**. Absent from all 13 local stores and from M4's prior
   exhaustive audit. The `outcited` org API returned 403 for this token
   (org policy on token lifetime), so one store remains unqueryable —
   but a correction of the historical record requires positive evidence,
   which does not exist. **Record unchanged: unresolved in every
   reachable store.** Action for M4: push the commit; then it becomes
   verifiable and the record can be corrected with evidence.
2. "13/18, v2, ranked [] describes the retired adapter": plausible and
   consistent with M4's narrative, but the *current* state is exactly
   what Gate 0 blocks me from inspecting. Not accepted, not denied —
   unverifiable.
3. Wrapper stdout-truncation caveat: acknowledged and honored — the
   refusal does **not** rest on the wrapper. It rests on commit
   unreachability and on the last executable runner verdict (13/18 FAIL
   against the only reachable M4 commit).

## Promotion-gate evaluation

`live_original_gates == 18/18` → **false** (13/18 last executable; 08C
not runnable). `release_relation == SAME_RELEASE` → **unverified**.
`ranked_observable`, `exclusions_observable`, `unknown_retention_observable`,
`interpolation_contract_present`, `approximate_contract_present`,
`auth_contract_current` → **unverified**. `frozen_locks_unchanged`,
`guards_unchanged` → true on the M5 side only.

Any false/unverified condition ⇒ **FAIL — PROMOTION REFUSED**.

## What unblocks re-verification (single action for M4)

Push `curve/08c-parity-repair` containing `42644ea` (and, separately,
`18893df` if the historical correction is to stand) to
`outcited/outcited.com`. M5 will then: resolve the commit, diff frozen
locks, stand up the exact commit, run the unmodified runner (expect
exit 0, 18/18 original + extended checks in `--out`), re-probe auth and
journey hashes, and only then execute the existing guarded transition
with a durable record referencing the pushed SHA.

## Suite state at verification

`1479 passed, 1 skipped` (the skip is the guarded live-parity wrapper,
which is PENDING by design without `M4_DECISION_URL`; with the URL set
against `91e44f04` it fails loud, as designed).

**DISCOVERY-01 NOT INTEGRATED — PROMOTION REFUSED**
