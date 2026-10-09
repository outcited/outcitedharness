# DISCOVERY-01 — Final Live Contract Parity

Branch `discovery/01-final-parity` (worktree `/Users/samkim/Harnessv1-disc01final`),
base `discovery/01-facet-decision-integration@6d809e55`.
M4 side: `outcited/outcited.com` `curve/08b-decision-api@91e44f04`
(fetched read-only; service run locally from a dedicated worktree at that
exact commit). **No push. No merges. No production or gold changes.**

## 1. Location of the orchestration implementation and live-parity suite

Found, not reconstructed:

- Orchestration: `harness/search/orchestrator.py` + `m4_client.py` +
  `POST /v1/discovery/engineering-session` behind
  `SEARCH_ENABLE_ENGINEERING_SESSION` (branch `discovery/01-facet-decision-integration`,
  commits `2c552b27` orchestration, `6d809e55` live-parity harness).
- Live-parity suite: `scripts/discovery01_live_parity.py` +
  `tests/test_discovery01_live_parity.py` with anti-mock guards
  (unreachable ⇒ exit 2 `NOT_RUN`; set-but-down ⇒ loud FAIL; every journey
  must carry `m4_transport == http`; RELEASE-DIVERGENT ⇒ structural-only).
- Frozen M4 contract fixtures (fingerprints of record):
  `m4_frozen_journeys_v1.json` sha `095ebd9c…`, `m4_identity_crosswalk_v1.json`
  sha `d0d5c5d3…`, provenance pinned to M4 commit `23b17634` + bundle v3
  `0c047e51…`.
- M4's ten frozen-journey expectations: the 10 locks in
  `tests/python/test_engineering_decisions_08b.py` on `91e44f04`
  (contract/review-state, 4 fingerprint pins, canonical passthrough,
  J1/J2/J3 identity locks).
- M4's own audit states the originally-referenced CURVE-08B commit
  `18893df` never existed in any reachable store; their adapter is new,
  labeled work. M4's report also claims M5's handoff doc was unreachable —
  it exists in the discovery01 worktree; their search missed that
  worktree. Recorded, not litigated.

## 2. Field-by-field contract comparison (M5 expected vs M4 08b actual)

M5 expected envelope: `harness.electronics-decision-answer.v1`
(handoff §1). M4 actual: live response sha
`bf2c848a55deb9c562ac2f272a8e0189cde59a7e34d159e2eabf7364574b48ac`.

| element | M5 expected | M4 08b actual | disposition |
|---|---|---|---|
| schema id | top-level `schema` | `contract: fae-engineering-decisions-v1`, no `schema` | projected by M5 client; mismatch recorded |
| evidence release | top-level `evidence_release` | `evidence_releases.evidence_release` | projected; recorded |
| bundle sha | top-level `bundle_sha256` | `evidence_releases.evidence_bundle_sha256` | projected; recorded |
| hard eligibility | `hard_eligibility{eligible,ineligible,unknown}` with per-rule reasons | `eligibility{counts,eligible_parts,eliminated_count,unresolved_count}`; rules only inside `canonical_envelope.qualification` | projected (rules lifted from canonical); recorded |
| comparison | `comparison{condition_matched_entries, approximate_scenario_entries, comparison_level}` | `candidate_identity{ranked, grain_disclosure, …}`; **no approximate mode** | projected; approximate absence recorded |
| counts | seven-way candidate/evidence separation | three-way + denominator | recorded as unpublished; M5 counters kept local |
| interpolation disclosure | per entry (points, method, conditions, uncertainty, limitations) | absent from `ranked` | recorded; M5 does not invent it |
| review state | per-entry `adjudication_state` | top-level `review_state: experimental_pending_human_review` | semantics compatible, location differs |
| auth | "none today (localhost)" per handoff | FAE bearer, 401/403 verified live | **handoff stale**; M5 client gained env bearer (`M4_BEARER_TOKEN`), redacted everywhere |
| question envelope | `{question:{requirements,conditions,operating_point,phenomenon}}` | accepted verbatim (live 200) | MATCH |
| ineligible rule fields | rule/reason/required/rated/scope | present in canonical envelope | MATCH via canonical |

Nothing was silently adopted: the projection lives in
`harness/search/m4_client.py::project_m4_08b` and returns an explicit
mismatch list carried on every DecisionResult.

## 3. Live journeys through M5's orchestration endpoint (real HTTP)

M5 session route on `127.0.0.1:8795` (flag-gated), M4 adapter on
`127.0.0.1:8794` running commit `91e44f04`. (Port 8793 is M4's SSH-tunnel
mouth held by sshd with M4's own credentials — not usable by M5; the
local instance of M4's exact commit is the parity transport, disclosed.)

| journey | request sha | response sha | m4 transport | outcome |
|---|---|---|---|---|
| J-A 48 V→5 V @0.5 A | `8397f40786aa50ec` | `7ceff21b73cef098…b26` | http | decision projected; M4 ranked empty |
| J-B 48 V→5 V @3 A | `2abffbb41f0a080a` | `0d1740231787a04f…bbe` | http | decision projected; M4 ranked empty |
| J-C 12 V→3.3 V @0.1 A | `62ad7f2fa8eab30f` | `ccfdb900be3fd6df…68` | http | decision projected; M4 ranked empty |

M4 live eligibility (direct probe, same commit): 1777 eligible of 5040
catalog + 4 quote-backed out-of-catalog devices, **0 eliminated, 0
unresolved, ranked []** — their preference cohort excludes rating-backed
out-of-catalog devices and bundle v2 predates LM5161. M5's own engine
(v3) answers all three journeys fully (frozen tests).

## 4. Release and comparator fingerprints (step 7)

| | M5 pinned | M4 live |
|---|---|---|
| evidence release | `curve-evidence-bundle-v3-5875789dcf3f` | `curve-evidence-bundle-v2-3b5658b629cc` |
| bundle file sha | `0c047e516c10c489134efe226d12e2422651665c4e394379108e9ec10ea4b16f` | `35dbd73c48fd41b107fa13c7358469dc87f337212756dd6aa1a04a1bdaf5f712` |
| comparator/engine | M5 harness comparator @07e/08 (legend_ambiguity law, caption inference) | `vendored_comparator: …v2-3b5658b629cc`, `engine_revision: p0-fixes-1` |

**RELEASE-DIVERGENT.** Per handoff §2 the parity run therefore reports
**structural parity only; values are never mixed**; both ids recorded on
every session (`releases.m4_evidence_release`). Numerical parity is
reported separately: **not verifiable live** (M4 v2 lacks LM5161 and
excludes rating-backed devices from preference); M5-side regeneration
from v3 reproduces 93.94/92.96/92.95/77.95 at 48 V→5 V @0.5 A (frozen
tests in both repos).

## 5. Negative matrix (step 8)

| test | result |
|---|---|
| auth denial | live 401 missing bearer / 403 invalid bearer; M5 surfaces `auth_failure` structured |
| timeout | hanging server ⇒ bounded 6 s (3 s × 1 connection-level retry), structured `timeout` |
| release mismatch | `expected_releases=v3` vs live v2 ⇒ structured `release_mismatch`, `partial_result: true`, cohort preserved (1577) |
| unavailable service | parity runner exit 2, verdict `NOT_RUN` — never a pass |

## 6. Live-parity verdict

`results/discovery01-final-parity/live-parity-report.json`: **FAIL,
13/18 checks**. The five failures are all eligibility/unknown-retention
observations that M4's v2 engine cannot produce (empty qualification for
these questions). Guards behaved exactly as designed; nothing was
weakened to force a pass. **Status remains CONTRACT-READY; the flip to
EXPERIMENTAL-INTEGRATED is refused.**

## 7. Preservation (step 9)

- Both source branches untouched: `discovery/01-facet-decision-integration@6d809e55`,
  M4 `curve/08b-decision-api@91e44f04`; this work is a new branch.
- 626 proposed family memberships: hash-pinned owner packets
  (OptiMOS `7a65e880`, CoolMOS `071ec975`, CoolSiC `b2e4a60b`, StrongIRFET
  `701c45b6`), decision block awaiting owner signature; `promote()` has
  **zero call sites** (definition + docstring mentions only); catalog
  untouched.
- Production gates: localhost-only binds, feature flags unchanged,
  v1/v2/v3 bundles byte-identical, 0 human approvals anywhere.

## 8. What M4 must ship for value parity (next actions)

1. Serve bundle v3 (`…5875789dcf3f`) or ask M5 to re-freeze journeys
   from v2 (not recommended: v2 lacks LM5161 and the ambiguity law).
2. Include rating-backed out-of-catalog devices in the preference cohort
   (SiC46x curves exist in their own bundle but never rank).
3. Publish interpolation disclosure and an approximate/scenario mode in
   the 08B projection, or state their permanent absence.
4. Update the handoff's auth section (bearer is live) and agree key
   distribution for cross-machine use.

**Verdict: structural contract parity achieved through an explicit,
loss-reporting projection; numerical parity blocked by release and cohort
divergence; live-parity gate FAIL (13/18) by design; no integration flip.**
