# CURVE-08B Release Report — Engineering Decision API

Branch `curve/08b-decision-api` (worktree
`/Users/samsonkim/Dev/Harnessv1-curve08b`), base
`curve/08-decision-engine@23b17634`. Committed locally; **not pushed,
not merged** (PRD §15 stop condition — awaiting separate authorization).

## What shipped

A feature-flagged, localhost-only, deterministic HTTP service that wraps
the CURVE-08 decision engine — plus the M5 handoff contract package.

| Piece | Where |
| --- | --- |
| Service + front door | `harness/electronics/decision_api.py` (stdlib only; `python -m harness.electronics.decision_api`) |
| Machine-readable contract | `contracts/engineering-decision-api-v1/openapi.yaml` |
| Frozen fixtures (byte-truth) | `contracts/engineering-decision-api-v1/examples/` (4 journeys + 5 error scenarios) |
| M5 integration handoff | `contracts/engineering-decision-api-v1/M5_INTEGRATION.md` |
| Ablation artifact | `contracts/engineering-decision-api-v1/ablation/ABLATION_REPORT.json` |
| Fixture regenerator | `contracts/engineering-decision-api-v1/generate_fixtures.py` |
| Tests | `tests/test_curve08b.py` (47) |
| Architecture record | `CURVE_08B_ARCHITECTURE.md` |

## Requirement outcomes

- **R1 route:** `POST /v1/engineering/decisions` exactly as specified
  (first `/v1` path in the harness; deviation from `/designs`-style
  documented). Flag `ENGINEERING_DECISIONS_ENABLED`; off ⇒
  `404 feature_disabled`.
- **R2 response contract:** release identity, partition-honest counts,
  grain-explicit candidate records, full-disclosure comparison records.
  Overlapping vs mutually exclusive partitions stated in-band
  (`counts.partitions`).
- **R3 hard eligibility:** verified through the API at 3 A — LM5161
  (rated 1 A) and SiC464 (rated 2 A) ineligible with rule+required+
  rated+evidence; catalog-sourced ratings are enriched with catalog
  provenance so no exclusion is source-less; unknown never counts as
  eligible (family-node test).
- **R4 condition-matched curves:** 48 V→5 V @0.5 A returns SiC461
  93.94 %, SiC463 92.96 %, SiC464 92.95 %, LM5161 77.95 % — computed
  by the existing comparator over immutable evidence (values appear in
  NO production code path; the frozen fixture holds them as data).
  Switching frequency/circuit conditions, trace identity, source
  locators, uncertainty and limitations ride every record;
  approximate scenarios stay a separate labeled collection.
- **R5 evidence resolution:** `GET /v1/engineering/evidence/{id}`
  resolves every reference; locators never manufactured; no filesystem
  paths on the wire (tested by substring scan).
- **R6 M5 handoff:** OpenAPI + examples + error definitions +
  comparator fingerprint + release ids + validation tests (fixture
  freeze) + integration doc. M5 consumes; it never re-implements
  interpolation or condition law.
- **R7 failure/unknown states:** all differentiated with stable codes
  (see architecture doc); zero-candidate/zero-comparison is a 200
  (Journey C fixture); broken/tampered evidence fails CLOSED as 500 —
  tested by corrupting a throwaway bundle copy.
- **R8 journeys:** A (light load), B (3 A — exclusions + recompute +
  unknowns preserved, no 0.5 A value reuse), C (24 V→3.3 V coverage
  gap — 11 parametric-eligible, 0 comparable, 13 investigation
  suggestions). All three run as automated tests through REAL HTTP
  (ThreadingHTTPServer, ephemeral port).
- **R9 ablation:** verdicts identical between arms (curves never touch
  eligibility); arm B adds 4 comparable candidates (recall 0.267 of the
  eligible+unknown cohort), 20 surfaced refusals, latency 0.0004 s →
  0.0057 s in-process; expert review honestly `pending` (0/123).
- **R10 verification:** every "no" invariant has a named test
  (invented candidates, count confusion, family/child double count,
  unsupported eliminations, unknown-as-eligible, typical-as-guaranteed,
  extrapolation, missing-source-as-verified, machine-to-gold,
  approximate bleeding, broken-source-as-empty, cross-worktree
  mutation, unexplained regressions). Historical bundles v1/v2/v3
  byte-identical (hashed before/after the journey suite).

## Test counts

- `tests/test_curve08b.py`: **47/47 green**.
- Curve lineage (curve04/05a/05b/07e/08/08b): **109 passed, 1 skipped**.
- Full harness suite on this M4 box: **1455 passed, 11 failed, 8
  errors** — every failure explained, none caused by this lane (zero
  tracked files modified by the branch):
  1. **M5-volume paths** (9): `/Volumes/M5_4TB/...` PDFs + designs.db
     are not mounted on the M4 (crop_verify ×4, region_detect ×4,
     discovery_api ×1).
  2. **macOS/APFS platform defect, pre-existing** (11): the hash-bound
     bundle writers chmod a staging directory to 0o555 then
     `os.rename` it (`harness/electronics/claims.py:231` et al.);
     macOS refuses rename of a read-only directory (reproduced with a
     3-line script), Linux does not. admission/claims/embeddings/
     frontier_batch ×6/training_dataset_builder. Fix belongs to the
     bundle-writer owner, not this lane; recorded here as the blocker
     reference.

## API example (frozen: `examples/response-journey-a.json`)

```json
POST /v1/engineering/decisions
{"category":"power","subcategory":"buck-converters",
 "requirements":{"vin_v":48,"vout_v":5,"iout_a":0.5},
 "comparison":{"metric":"efficiency","mode":"condition_matched"},
 "evidence_policy":"machine_verified_advisory"}
→ 200 {"schema":"harness.electronics-decision-api.v1",
       "counts":{"total_candidates":21,"hard_eligible":14,
                 "hard_ineligible":6,"hard_unknown":1,
                 "with_comparable_evidence":4,...},
       "comparisons":{"condition_matched":[...SiC461 93.94, SiC463 92.96,
                                           SiC464 92.95, LM5161 77.95],
                      "comparison_level":{"achieved_level":
                                          "cross_manufacturer"}}}
```

## Candidate counts + evidence coverage (unchanged from CURVE-08 by
design — the API wraps, never re-derives)

21 candidates (16 catalog OPNs + 4 SiC46x devices + 1 family node) ·
123 evidence rows attached · 0/123 human-approved (advisory everywhere)
· bundle `curve-evidence-bundle-v3-5875789dcf3f`.

## M5 handoff status

Contract package complete and self-contained (OpenAPI + frozen byte-
stable fixtures + error table + integration doc). M5 integrates against
`http://127.0.0.1:8793` with the flag on; no reverse-engineering of
implementation code required; nothing for M5 to recompute.

## Blockers / remaining work

1. **Not pushed / not merged** — per PRD §15, awaiting authorization.
2. Metrics beyond `efficiency` and categories beyond `power/buck` are
   `422 unsupported_*` — the honest boundary, not silent gaps.
3. The 9 M5-volume test failures are mount-dependent; the 11 APFS
   rename failures need a one-line fix from the bundle-writer owner
   (chmod after rename, or copy-then-rename) — flagged, not touched
   from this lane.
4. Release recommendation: **qualified yes** — merge behind the feature
   flag after review; the flag keeps prod behavior unchanged (the
   service is a new standalone entry, not wired into any gateway).
