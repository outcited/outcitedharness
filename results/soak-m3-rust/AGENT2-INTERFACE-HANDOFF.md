# Agent 2 handoff — verified deterministic-engine interface (RUST-CUTOVER-02)

**From:** power-data-01 (deterministic extraction/verification parity owner)
**To:** Agent 2 (controller-level shadow orchestration owner)
**Baseline:** `5451ae71` (RC01) → this branch tip (RC02)

You own the Rust-controlled factory canary. This is everything your
controller needs to adopt the verified engines — nothing here is new
architecture; it is the SAME code the parity evidence covers.

## 1. The two interfaces (both behind one env each)

### Verification — `BURN_VERIFY_ENGINE={python,rust}` (default python)

- Seam: `harness/pipeline/verify_provider.py` — `verdicts(claims, text)`
  and `quick_ok(claim, text)`. burn_aisle routes through it (3-hunk diff
  against the live factory file; default path byte-identical).
- Rust engine: `designwins/target/release/dw-verify` (dw-truth kernels).
  CLI: stdin `{"mode": "verdicts"|"quick_ok", "claims": [...], "text": s}`
  → stdout verdict JSON in the reference dict shape; exit 2 on malformed
  input; any subprocess failure raises `VerifyProviderError` (fail
  closed → the doc takes the error disposition — never silent
  fallthrough, never unverified claims).
- Evidence: 127/127 frozen-response docs byte-identical (RC01); value_in_quote
  number-splitting bug found+fixed there; dw-parity quote replacement gate
  re-proven `cutover_permitted yes` post-fix; 7 seam tests
  (`tests/test_verify_provider_seam.py`).

### Normalization — `BURN_NORMALIZE_ENGINE={python,rust}` (default python)

- Seam: `harness/electronics/normalize_provider.py` — `parse_condition(text)`
  returning the reference dict shape (`{"keys": …}` / `{"reject": …}`).
  `harness/discovery/evaluator.py` imports it (one-hunk diff;
  `condition_covers` stays on the reference module).
- Rust engine: `designwins/target/release/dw-parse` (dw-normalize via
  dw-parity, the bridge crate — dw-normalize itself stays dependency-free).
- Evidence: dw-parity condition replacement gate 10,270 conditions, 0 diffs
  (`cutover_permitted yes`, re-run on the final build); seam tests pin
  rust==python over 200 live corpus conditions
  (`tests/test_normalize_provider_seam.py`).

## 2. Runtime requirements

- Build both binaries once per deploy:
  `cargo build --release -p dw-truth --bin dw-verify -p dw-parity --bin dw-parse`
  (workspace must pass `cargo fmt --check && cargo clippy --workspace
  --all-targets -- -D warnings && cargo test --workspace` — currently green,
  277/277).
- Process model: one subprocess per verify/parse call (~30 ms spawn,
  measured). Per-doc verification cost is negligible vs the model lane
  (~70 s/doc); no I/O dependencies; no network.
- **HARD CONSTRAINT — do not enable serde_json `preserve_order` anywhere
  in the engine graph**: it changes `Value::to_string` key order globally
  and breaks the Python-verbatim canonical hashing (queue evidence,
  dw-controller contract). Use `dw_extract::canonical::canonical_json`
  whenever hashing JSON. The workspace test run guards this (three tests
  fail under `--workspace` if the bin forgets).
- Claim key order is load-bearing for repair prompts: round-trip raw
  claims text (never re-serialize through a sorted map) — dw-soak's
  `claims_raw` pattern is the reference implementation of that rule.
- Page attribution (`_page_for`) and provenance stamping stay Python-side
  in the seam design; the Rust engine output schema (`i/verdict/reason/
  severity`) cannot touch `doc_sha256`/`page` (pinned by test).

## 3. Replay fixtures (reuse, don't rebuild)

- Frozen response cache + comparator: `scratch/m5-soak/{rc02-cache.jsonl,
  replay-cache.jsonl}` + `scripts/{soak_model_shim,replay_python_burn,
  compare_frozen_replay}.py`. Shim modes: live/record/replay/fill; cache
  key = (endpoint, model, normalized messages) — request identity is
  itself under test.
- Stratified samples: `scratch/m5-soak/{replay-cohort-127,
  rc02-cohort-200}.json` (prose-hyphen, repair-path, numeric-page,
  null-page, exception, cliff, and control strata).
- Orchestration state to mirror: `scratch/m5-soak/{full-state,
  rc02-state}/factory.sqlite3` (200/200 and in-flight) — schema-verbatim
  with the Python factory; your controller can point at these shapes.

## 4. Failure and rollback behavior (tested, not asserted)

- Rust engine failure (binary missing, exit != 0, malformed output,
  timeout) → provider raises → burn_doc/run_doc wraps into the
  `{"stem", "skip": "error:…"}` disposition. No claim vanishes without a
  verdict; no SHA/page mutation (schema-pinned).
- Rollback: unset the env (or set `python`) — instant, no data migration,
  byte-equal to pre-seam behavior (tests pin the restore).
- For the controller: treat `VerifyProviderError`/`NormalizeProviderError`
  like any per-doc pipeline exception — your retry/fail-chunk machinery
  already handles that class (lease-expiry recovery proven ×3 at volume).

## 5. Verified constraints for your canary

1. Worker model: dw-soak measured 19.65 avg in-flight docs at 22 workers
   (89.3% utilization); Python ≈ 25 threads. Match worker counts when you
   compare; the endpoint saturates either way (~9.6–12 s/doc).
2. Lease policy: 900 s + per-document renewal (the 60 s lease class
   double-claims under endpoint latency; measured, fixed, guarded).
3. The model lane stays Python (burn_aisle call path) — do not route
   model calls from the controller; single client, per the cutover plan.
4. Chunk seals require manifest schema + queue-sha key per
   dw-controller's `verify_chunk_output` (soak schemas accepted
   additively; structural gold battery unchanged, 43/43).

Questions → mail power-data-01. My recommendation for activation gating
is in the RC02 report.
