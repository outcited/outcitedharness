# DISCOVERY01 ARCHITECTURE — progressive engineering discovery (D1)

Branch `discovery/01-facet-decision-integration` (worktree
`../Harnessv1-discovery01`, off `facet/03-family-provenance` @ 961ef951 —
FACET-02/03 code reused as the branch base, no cross-agent merges).

## Service boundary (the load-bearing rule)

- **M5 owns**: taxonomy, cohort narrowing, facets, identity, evidence
  resolution, orchestration, caching, presentation contracts.
- **M4 owns**: hard engineering eligibility, curve mathematics,
  condition-matched comparison, comparison-level classification, review
  state. M5 NEVER recomputes any of these — it forwards canonical-SI
  requirements and renders M4's answers.
- Boundary enforcement is structural: `orchestrator.py` contains no
  eligibility or interpolation code; the only decision source is
  `m4_client.decide()`.

## M4 availability and the frozen-contract decision

The PRD-reported M4 API (`POST /v1/engineering/decisions`, :8793,
CURVE-08B OpenAPI, commit `18893df`) is **not reachable from this
machine** (port closed; commit absent from this repo). What IS available:
CURVE-08 engine `curve/08-decision-engine` @ `23b17634` in worktree
`../Harnessv1-curve08` with frozen bundle
`curve_evidence_bundle_v3.jsonl` (sha `0c047e51…`, release
`curve-evidence-bundle-v3-5875789dcf3f`).

Per PRD §3.1: **no merge performed**. Two immutable artifacts were
regenerated read-only from the M4 worktree and pinned as fixtures on this
branch:

1. `tests/fixtures/discovery01/m4_frozen_journeys_v1.json` — journeys A/B/C
   answers (fixture sha `73cbb73f…`), regenerated from the evidence bundle,
   never hardcoded (the 93.94/92.96/92.95/77.95 values are fixture data).
2. `tests/fixtures/discovery01/m4_identity_crosswalk_v1.json` — 21 M4
   candidate identities (16 opn / 4 device / 1 family) with grains,
   authorities, sha `6eb5a73d…`.

Both carry full provenance (m4 commit, bundle sha, generation command).
**Live integration remains pending** — `HttpTransport` is implemented and
failure-tested but has never spoken to a live :8793; status is
CONTRACT-READY, not EXPERIMENTAL-INTEGRATED.

## Components (new on this branch)

```
harness/search/m4_client.py     typed decision client: frozen + http
                                transports, question-contract validation,
                                structured failures (never false-empty)
harness/search/session.py       serializable session state (R2), step
                                records with eliminated/unplaced separation
harness/search/orchestrator.py  engineering_session(): cohort (FACET-02/03
                                reuse) -> crosswalk -> M4 decision ->
                                evidence resolution -> guidance;
                                SessionCache (release-keyed LRU, errors
                                never cached); subcategory crosswalk
                                (buck->switching-regulators, closed table)
harness/search/api.py           POST /v1/discovery/engineering-session
                                behind SEARCH_ENABLE_ENGINEERING_SESSION=1
                                (off => 404, existing behavior untouched)
scripts/discovery01_freeze_m4_journeys.py   fixture regeneration
```

## Identity crosswalk (R1 §4.2)

Classes: `exact` (normalized OPN/device string equality — the only join
that exists), `ambiguous` (one M5 OPN claimed by >1 M4 candidate),
`unmapped` (M4 family-grain nodes — never interchangeable with OPNs),
`m4_only` (M4 devices with no M5 catalog identity, e.g. SiC46x). No
fuzzy/similar-name joins exist in the code. Live journey A: 16 exact-or-
m4_only device mappings, 1 family node unmapped-by-design, 0 ambiguous.

## Grain discipline (R1 §4.3)

Every response declares grain per count: M5 cohort counts OPNs; M4 counts
its own candidate set (opn/device/family declared per candidate); the
envelope reports them side by side, never summed. A family and its children
are never double-counted (regression-tested).

## Release identities carried per response

m5_evidence_release (content+policy hashed), m5_identity_snapshot
(content-hashed, deterministic), crosswalk fixture sha, m4 decision
contract schema, m4 evidence release, comparison fingerprint (sha of the
comparison payload). Expected-release mismatch → structured
`release_mismatch` failure + `partial_result`, cohort preserved.

## Unknown taxonomy (R2 §5.2) — never collapsed

hard-failed (M4 rule + rated + required + scope) / passed / unknown
(missing ratings — retained, "never eliminated") / evidence-missing
(counts.candidates_missing_curve_evidence) / evidence-incompatible
(noncomparable) / identity-unresolved (crosswalk unmapped/ambiguous) /
service-failure (structured M4ServiceError). Each surfaces in its own
envelope field.

## Caching (R8)

Key = {category, subcategory, grain, canonical constraints, identity
snapshot id, evidence release, policy, transport class}. Bounded LRU
(default 64), deterministic eviction, errors never cached, key computed
from cheap meta reads BEFORE the expensive cohort build. Measured:
hit p50 88ms / p95 94ms (n=24); miss 742ms (cohort build 646ms dominates —
the FACET-02 known blocker; release-keyed cohort cache is the next lever).

## Security (R9)

Localhost-only preserved (non-localhost bind refuses without
SEARCH_ALLOW_REMOTE=1). Engineering-session route exists only under its
feature flag. No credentials in responses (frozen transport has none; http
transport adds none). No filesystem paths leak (regression-tested).
M4 http target will need auth policy before any cross-machine use —
documented in the handoff.
