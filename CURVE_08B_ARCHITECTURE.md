# CURVE-08B Architecture — Engineering Decision API and M4 Service Qualification

Branch `curve/08b-decision-api`, worktree
`/Users/samsonkim/Dev/Harnessv1-curve08b`, base
`curve/08-decision-engine@23b17634` (verified: 16 frozen CURVE-08 tests
green at base under this worktree's uv-managed Python 3.12 venv).
Worktree-local assignment: `curve08b.assignment.json` (branch +
base-commit; the isolation test asserts assignment == checked-out
branch without hardcoding a name).

## R0 reconnaissance findings (verified, not assumed)

- **Base:** `23b17634` is the tip of `origin/curve/08-decision-engine`;
  the local clone at `~/Documents/Default Project/outcitedharness` was
  stale (main @ b0c847e) and was fetched first.
- **CURVE-08 engine:** `harness/electronics/{candidates,eligibility,
  decision_engine}.py` answer the full question in-process
  (`answer_question`); 16 frozen tests regenerate the four PRD
  verification targets from immutable evidence.
- **API framework:** two front doors already exist —
  `harness/discovery/api.py` (:8790) and `harness/pipeline/api.py` —
  both stdlib `http.server`, no dependencies, `main()` entry, JSON
  bodies, 400/404/422 statuses. A heavier Starlette gateway exists but
  is for the LLM routing lane. 08B follows the stdlib convention.
- **CURVE-07E contracts:** bundle v3
  `curve-evidence-bundle-v3-5875789dcf3f` (123 rows, SHA-verified
  against the manifest at every service start), comparator functions
  `query_operating_point` / `condition_compatibility` /
  `curve_from_bundle_row`, comparator fingerprint + 4 admission
  packets + 20-cell coverage matrix in the manifest.
- **FACET-02:** foreign workstream; its `harness/search` package does
  not exist on this branch and nothing in the 08B modules imports it
  (asserted by test). `to_facet_candidate()` remains the reconciliation
  seam, untouched.
- **Route deviation note (PRD R1):** no compatible endpoint exists; the
  harness has no `/v1` routes today. The PRD-mandated
  `POST /v1/engineering/decisions` is implemented verbatim — the first
  versioned path in the harness, documented here as the deliberate
  deviation from the `/designs`-style resource routes.
- **Port choice:** 8793 — discovery owns 8790 (and a mail from
  2026-10-09 records :8790 colliding with recon-ruvector on one box),
  m5's extraction API owns 8819.

## Service design

```
POST /v1/engineering/decisions      the decision door (feature-flagged)
GET  /v1/engineering/health         flag + release identity (always on)
GET  /v1/engineering/evidence/{id}  frozen-row resolver (R5)
```

- **Wrap, never rebuild:** `harness/electronics/decision_api.py` calls
  `answer_question` / `evaluate_cohort` / `build_power_candidates`
  directly. Zero edits to CURVE-08 modules; zero new dependencies.
- **Feature flag:** decisions answer only when
  `ENGINEERING_DECISIONS_ENABLED` is truthy; otherwise
  `404 feature_disabled`. Health always answers (reports the flag and
  evidence state) so an integrator can probe without enabling.
- **Auth (optional):** `ENGINEERING_DECISIONS_TOKEN` set ⇒ bearer
  required (`401 unauthorized`). Default unset = localhost open,
  matching the other harness front doors.
- **Bind:** `127.0.0.1:8793` default; `ENGINEERING_DECISIONS_HOST/PORT`
  override. No production routing touched anywhere.
- **Determinism:** responses carry no wall-clock and no random ids;
  `request_id = sha256(canonical request + release identity)[:16]`.
  Frozen fixtures are therefore byte-stable, and a test fails if the
  service output drifts from them.

### Request normalization (physical → rule vocabulary)

`vin_v` is a span demand (rated max ≥ V AND rated min ≤ V — the same
law the outcited store applies), `vout_v` → output-capability rule,
`iout_a` → `iout_min`, `temp_max_c` → `temp_max`; native rule keys pass
through; anything else is `422 unsupported_condition`. Curve-match
conditions derive from the physical requirements; explicit
`comparison.conditions` extend them. Omitting `comparison` entirely =
parametric-only mode (R9 arm A). `comparison.mode` containing
"approximate" additionally surfaces mismatched evidence in a separate
labeled collection (engine law: condition-matched output is
byte-identical either way).

### Response contract (R2)

Release identity (evidence release, frozen catalog-slice release,
comparator fingerprint, bundle sha, contract version) → counts →
candidate records → ineligible/unknowns → comparisons → investigation →
laws. Partition honesty is explicit in-band (`counts.partitions`): the
verdict triple is mutually exclusive; evidence-status counters overlap
verdicts and each other. Family candidates disclose `membership`;
every record states its grain (`opn|device|family`); the TPS563206
family node is the single candidate for its part rows (no family+child
double count — asserted by test).

### Source citation (R3 + R5)

Rules whose rated value came from an admission packet or device-rating
quote carry that quote inline (engine behavior). Rules sourced from the
frozen catalog slice have no quote — the API enriches them with the
catalog provenance (record id + datasheet url + authority) so **no hard
exclusion is ever source-less on the wire**. Evidence references
(`harness.electronics-evidence-ref.v1`) carry sha, revision, page,
figure/series locator, series name, supporting points, verbatim
conditions, class, review state. Locators exist only where the frozen
row has them; `citation().source_path` is deliberately NOT projected —
no filesystem path crosses the wire (tested).

### Failure model (R7)

Stable codes: `invalid_json`, `invalid_request`,
`operating_point_required` (400); `unauthorized` (401);
`feature_disabled`, `unknown_route`, `evidence_not_found` (404);
`unsupported_category`, `unsupported_metric`, `unsupported_condition`,
`unsupported_comparison_mode`, `unsupported_evidence_policy`,
`unknown_candidate` (422); `evidence_source_unavailable`,
`evidence_bundle_tampered`, `internal_error` (500). In-band states that
are NOT errors: unknown verdicts, missing ratings, missing/non-comparable
availability, zero-comparison 200s (Journey C fixture is the proof).
The bundle is SHA-verified at state load: tamper or absence is a 500
that fails closed — tested by corrupting a throwaway copy.

## R9 ablation design

Arm A = same request minus `comparison` (parametric-only); arm B =
full. The test asserts verdict-identity between arms (curves never
participate in eligibility), counts the recall gain (4/15 comparable),
records latency, and writes
`contracts/engineering-decision-api-v1/ablation/ABLATION_REPORT.json`.
Expert review is reported as `pending` (0/123 approved) — not
fabricated.

## Ownership boundaries honored

- CURVE-08 modules: unmodified.
- FACET-02 / search lineage: absent, unimported, tested.
- Catalog admission: none (packets were already prepared-not-applied).
- Human adjudication: none simulated.
- Production routing: untouched (new standalone entry only).
- Historical bundles v1/v2/v3: byte-identical before and after the full
  journey suite (hashed in-test).

## Test inventory

`tests/test_curve08b.py` (47): isolation + FACET-02 absence; journeys
A/B/C through real HTTP (ThreadingHTTPServer on an ephemeral port);
hard-eligibility contract; all error codes incl. fail-closed tamper;
R10 invariants (the PRD's "no" list); fixture freeze; ablation;
historical bundle immutability.
