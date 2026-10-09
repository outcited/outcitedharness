# DISCOVERY01 LIVE-PARITY HANDOFF

Purpose: the exact requirements for flipping DISCOVERY-01 from
**CONTRACT-READY** to **EXPERIMENTAL-INTEGRATED**. Frozen-fixture tests
already pass (16/16); this parity run is the only gate, and it can only be
satisfied by a real service-to-service conversation.

## 1. M4 service requirements

| Requirement | Value |
|---|---|
| Endpoint | `POST /v1/engineering/decisions` |
| Expected address | `http://<m4-host>:8793` (experimental port per CURVE-08B); M5 consumes via `M4_DECISION_URL` env — no hardcoded hosts |
| Request envelope | `{"question": {"requirements": {"vin_min": float, "vin_max": float, "vout": float, "iout_min": float}, "conditions": {"vin_v": float, "vout_v": float}, "operating_point": {"x": float, "unit": "A"}, "phenomenon": "efficiency_vs_load"}}` — canonical SI floats (validated client-side before send) |
| Response schema | `harness.electronics-decision-answer.v1` (top-level or under `answer`); MUST carry `schema`, `evidence_release`, `bundle_sha256`, `hard_eligibility{eligible,ineligible,unknown}`, `comparison{condition_matched_entries,approximate_scenario_entries,comparison_level}`, `counts` |
| Comparison entries | each MUST carry `part`, `value`, `unit`, `evidence_id` |
| Ineligible entries | each rule carries `rule`, `reason`, `required`, `rated`, `scope` (M5 renders verbatim; never recomputes) |
| Contract version | if CURVE-08B (`18893df`) changed the envelope vs `23b17634`, publish the OpenAPI; M5 adapts `harness/search/m4_client.py` only |
| Authentication | none today (localhost experimental). Before any cross-machine use: agree transport auth; M5 keeps credentials out of responses, logs, and fixtures (redaction is a merge condition) |
| Timeout policy | M5 client: 10 s bounded, 1 retry on connection-level failure only; 4xx never retried |

## 2. Release identities the live service must expose

- `evidence_release` — M5 records it per session and fails structured on
  `expected_releases` mismatch (never silently uses stale decisions).
- `bundle_sha256` — pinned per answer.
- Frozen baseline for value-parity: bundle
  `curve-evidence-bundle-v3-5875789dcf3f`, sha256
  `0c047e516c10c489134efe226d12e2422651665c4e394379108e9ec10ea4b16f`,
  M4 commit `23b176343abf836d208680f66295cb30ed4825c6`.
- If the live service reports a DIFFERENT evidence release, the parity run
  marks **RELEASE-DIVERGENT**: structural parity only, values never mixed,
  both ids recorded. Value parity resumes when M4 serves the pinned bundle
  or M5 re-freezes fixtures from the new one
  (`scripts/discovery01_freeze_m4_journeys.py`).

## 3. Frozen M4 contract fixtures (item 4 — fingerprints of record)

| Fixture | SHA-256 |
|---|---|
| `tests/fixtures/discovery01/m4_frozen_journeys_v1.json` | `095ebd9c40e5f43271c53529aac18c505cce4b2f50dfee4856296250fd0481b6` |
| `tests/fixtures/discovery01/m4_identity_crosswalk_v1.json` | `d0d5c5d36eab026ee50a7141f06b54d1c69b78ddaefab59addb6a2f7a87dbaa8` |

Both regenerate deterministically from the M4 worktree (provenance block
inside each file: m4 commit, bundle sha, generation time). These fixtures
are the CONTRACT-READY evidence baseline; they are never used to fake a
live result (guards below).

## 4. Running the parity suite

```
M4_DECISION_URL=http://<m4-host>:8793 \
  scripts/discovery01_live_parity.py --out live-parity-report.json
# or via pytest:
M4_DECISION_URL=http://<m4-host>:8793 \
  pytest tests/test_discovery01_live_parity.py -q
```

Anti-mock guards (release decision item 6, tested):
- service unreachable → exit 2, verdict `NOT_RUN` — never a pass, never a
  fixture fallback;
- `M4_DECISION_URL` set but unreachable → pytest FAILS loudly
  (misconfiguration ≠ pending);
- every journey result must carry `m4_transport == "http"`;
- verdict `PASS` requires all checks green AND at least one live journey.

## 5. Expected results (from the pinned frozen baseline)

| Journey | Expected live behavior |
|---|---|
| A: 48V→5V @0.5A | 14 eligible; condition-matched SiC461 93.94 / SiC463 92.96 / SiC464 92.95 / LM5161 77.95 (±0.01, release-matched only); level cross_manufacturer; every entry evidence_id-bearing |
| B: 48V→5V @3A | eligible 4; LM5161 + SiC464 hard-ineligible with cited rules (rated 1.0/2.0 vs required 3.0); ranking fresh (no 0.5A reuse); unknown retained |
| C: 24V→3.3V @1A | SUCCESS (not error) with zero condition-matched curves; 11 eligible retained; suggestions derived from counts (11+1+1=13) |
| Crosswalk | 21 candidates: 16 opn / 4 device (SiC46x, m4_only — stay unresolved in M5 until catalog admission, item 8) / 1 family (never OPN-mapped) |
| Release mismatch probe | wrong `expected_releases` → structured `release_mismatch` + `partial_result`, cohort preserved |
| Unknowns | ≥1 unknown candidate retained per journey ("electrically unconfirmed; retained, never eliminated") |

## 6. Pass criteria → status change

All green ⇒ M5 reports **EXPERIMENTAL-INTEGRATED** (still feature-flagged,
localhost-only). Any red ⇒ remain CONTRACT-READY with the failing checks
published. PRODUCTION-READY additionally requires the R11 list (admission
decisions, provenance backfill completion, cohort caching, auth policy).

## 7. Standing constraints honored here (items 3, 7, 8)

- Localhost-only serving and `SEARCH_ENABLE_ENGINEERING_SESSION` flag
  unchanged; the parity runner is a client, it exposes nothing.
- All 626 family memberships remain `proposed`; `promote()` is not called
  anywhere in this branch (grep-verified); owner sign-off pending.
- The four SiC46x identities remain `m4_only` in the crosswalk — visible,
  unmapped, awaiting proper catalog admission; no fuzzy joins exist.
