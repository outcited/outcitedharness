# M5 Integration — Engineering Decision API v1 (CURVE-08B)

**You do not recompute engineering decisions. You render them.**

The fundamental invariant (PRD-CURVE-08B): M4 provides defensible
engineering decisions; M5 never needs to invent, reconstruct, or
reinterpret them. Everything below is about consuming, displaying, and
resolving provenance — never re-deriving values.

## 1. Endpoint

```
POST http://127.0.0.1:8793/v1/engineering/decisions
GET  http://127.0.0.1:8793/v1/engineering/evidence/{evidence_id}
GET  http://127.0.0.1:8793/v1/engineering/health
```

- Feature-flagged: the server answers decisions only when
  `ENGINEERING_DECISIONS_ENABLED=1` (otherwise `404 feature_disabled`).
- Default bind `127.0.0.1` (localhost only; no production routing).
- Optional bearer: set `ENGINEERING_DECISIONS_TOKEN` and send
  `Authorization: Bearer <token>`.
- Start: `.venv/bin/python -m harness.electronics.decision_api`
  (stdlib only; no extra dependencies).

Full machine-readable contract: `openapi.yaml` next to this file.
Frozen request/response fixtures: `examples/*.json` — byte-stable
(the service is deterministic; `request_id` hashes request + release).

## 2. The request (what M5 sends)

```json
{
  "category": "power",
  "subcategory": "buck-converters",
  "requirements": {"vin_v": 48, "vout_v": 5, "iout_a": 0.5},
  "comparison": {"metric": "efficiency", "mode": "condition_matched"},
  "evidence_policy": "machine_verified_advisory",
  "include_unknowns": true
}
```

- `vin_v` is a span demand: rated max >= V AND rated min <= V.
- Omit `comparison` entirely for parametric-only screens (arm A of the
  ablation: identity + hard eligibility, no curves attempted).
- `comparison.mode` `approximate` or `condition_matched+approximate`
  additionally surfaces mismatched evidence in a SEPARATE labeled
  collection; it never alters condition-matched results.
- `cohort: ["SiC461", ...]` restricts the universe; unknown names are a
  `422 unknown_candidate` listing them.

## 3. The response (what M5 renders)

| Block | Meaning | Render guidance |
| --- | --- | --- |
| `release` | evidence release id, bundle sha, comparator fingerprint, catalog release | show as the "as-of" stamp on every screen |
| `counts` | verdict triple is mutually exclusive; evidence-status counters overlap | never sum them into one number |
| `candidates[]` | one record per selection unit at its honest grain (`opn/device/family`) | grain badge mandatory; family cards link member devices via `membership` |
| `candidates[].hard_eligibility.verdict` | `eligible` / `ineligible` / `unknown` | **unknown ≠ eligible** — visually distinct (dashed border / "investigate" chip), never green |
| `comparisons.condition_matched[]` | measured values at matched conditions | order by `result.value` if you rank; each card shows value + unit + "typical, not guaranteed" |
| `comparisons.approximate_scenarios[]` | mismatched evidence, labeled | separate section, never blended into rankings |
| `investigation[]` | why comparable evidence is absent + what would unlock it | the "what next" panel |
| `laws[]` | the invariants the answer obeys | render as the trust footer |

Every comparison carries:
- `evidence` — sha, page, figure, series, caption, artifact name → deep link
- `interpolation` — method, supporting points, uncertainty, limitations
- `human_review.advisory: true` — 0/123 rows human-approved today

## 4. Evidence resolution (deep links)

Every `evidence_id` resolves: `GET /v1/engineering/evidence/{id}` returns
the full frozen row (locator, series points, verbatim conditions,
supported region, binding method). Locators are only present where the
frozen row carries them — the API never manufactures one. No filesystem
paths cross the wire; `source_artifact` is a filename for display.

## 5. Error handling (stable codes)

| http | code | M5 behavior |
| --- | --- | --- |
| 400 | `invalid_json` / `invalid_request` / `operating_point_required` | fix request |
| 401 | `unauthorized` | token |
| 404 | `feature_disabled` / `unknown_route` / `evidence_not_found` | flag off / typo |
| 422 | `unsupported_category` / `unsupported_metric` / `unsupported_condition` / `unsupported_comparison_mode` / `unsupported_evidence_policy` / `unknown_candidate` | fix request |
| 500 | `evidence_source_unavailable` / `evidence_bundle_tampered` / `internal_error` | **fail closed — show error state, never an empty result list** |

In-band states that are NOT errors: `unknown` verdicts, missing ratings,
`missing`/`non_comparable` evidence availability, zero-candidate or
zero-comparison 200s (see `examples/response-journey-c.json`).

## 6. Frozen example: 48 V→5 V @ 0.5 A

`examples/response-journey-a.json`: 21 candidates → 14 eligible,
6 ineligible (each with rule+required+rated+evidence), 1 unknown;
condition-matched: SiC461 93.94 %, SiC463 92.96 %, SiC464 92.95 %,
LM5161 77.95 % at `cross_manufacturer` level. At 3 A
(`response-journey-b.json`): LM5161 (1 A) and SiC464 (2 A) are
hard-ineligible before any curve is consulted; the ranking recomputes.

## 7. What M5 must NOT do

- No curve interpolation, condition matching, or eligibility logic on
  your side — the API is the only source of values.
- No promoting `unknown` to eligible because a value exists.
- No presenting typical values as guarantees (every record says
  `guarantee: false`; keep the label).
- No blending approximate scenarios into condition-matched rankings.
- No caching across evidence releases — pin responses to
  `release.evidence_release_id` and re-ask when it changes.
