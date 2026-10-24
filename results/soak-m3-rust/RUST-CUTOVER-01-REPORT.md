# RUST-CUTOVER-01 — Cutover Proof Closed — 2026-10-16

**Assignment** (Sam, 2026-10-15): close cutover proof. POWER-DATA-01 owns
the soak-specific proof; M5 owns factory integration and approval
coordination. **No production cutover.** Branch `agent/power-data-01`,
worktree `Harnessv1-powerdata01`, remote `github.com/outcited/outcitedharness`.

## Deliverable 1 — Deterministic parity on identical frozen model responses

**Method.** The provider shim gained live/record/replay/fill modes. The
cache key is the exact HTTP request tuple (endpoint, model, messages —
content normalized for JSON-escape dialects), so *request identity itself
is under test*: a cache miss means an engine built a different prompt.
One record pass (127-doc stratified sample: all 36 both-claim stems, 40
rust-only + all 11 python-only cliff stems, 40 double-skip stems — 256
frozen responses including endpoint exceptions), then BOTH engines replay
strictly from the same cache and every output byte is diffed
(`scripts/compare_frozen_replay.py`).

**Result: 127/127 stems byte-identical** — counters, tiers, and full
`claim_data` arrays: **1,359 claims in → 1,272 surviving, both engines,
zero differences.**

**Two real divergences were found and fixed en route (that is the point
of the exercise):**

1. **`dw-truth::value_in_quote` number-splitting bug** (the documented
   §5.1 latent divergence, now with a reproducer): the reference's
   `re.findall(r"-?\d+(?:\.\d+)?")` extracts `16` from `"16-V VIN, …"`;
   the port's fragment split produced `"16-"`, failed to parse, and
   wrongly P0-dropped prose-hyphenated claims (CSD96416 class: python
   surviving 25 vs rust 19 on identical claims). Fixed by adopting the
   existing reference-faithful `extract_numbers` scanner (the same one
   `column_position_binds` already uses). Negative-control test shipped;
   dw-truth 16/16; **live replacement parity re-proven** (dw-parity quote
   mode, 40 cases, `cutover_permitted yes`).
2. **Repair-payload key order** (LM5100B): a claims round trip through
   serde_json's sorted map reordered keys and diverged the repair prompt
   bytes. Fixed by round-tripping the shim's raw claims text (the worker
   never re-serializes through a sorted map). Enabling crate-wide
   `preserve_order` was rejected: it would break dw-controller's
   Python-verbatim canonical hashing under workspace feature unification
   — pinned instead by fixing the bin's `canonical_core` to use
   dw-extract's canonical writer (found because workspace test runs
   enable dw-learn's `preserve_order`; three tests failed only under
   `--workspace`, which is itself now a guard).

Also fixed: substrate `page` fields carry JSON numbers — Python
`str(8)`→`"8"`, the port yielded `""` (87 stems affected before the fix).

## Deliverable 2 — The 47-vs-163 claim-doc gap, controlled replay evidence

**Experiment** (`scripts/cliff_replay_experiment.py`): 20 stems × 5
independent live propose invocations each — 10 rust-only cliff, 5
python-only cliff, 5 both-claim controls. Same endpoint, same chain, same
prompts as the factory.

| group | per-stem hit rates (claims-returning invocations) |
|---|---|
| cliff (rust-only) | 2/5, 3/5, 1/5, 4/5, 4/5, and 5/5 ×5 |
| cliff (python-only) | 5/5 ×5 (incl. BQ24074 — the stem the soaks split on) |
| controls (both-claimed) | 5/5 ×4, **1/5** (CTL_UC2842L) |

Per-invocation claim-return is stochastic at the cliff AND for nominally
stable docs; 163 of 256 frozen record-pass responses were endpoint
exceptions. With one invocation per doc per window, claim-doc counts are
binomial draws — a 47-vs-163 ratio between windows is lane variance, not
engine behavior. (Rust's every extra claim passed the same deterministic
gate; P0 rates match at 5.3–5.4%.)

## Deliverable 3 — Fold/staging verification on a local copy

The live export root was unreachable (yosemite NFS outage); verification
ran against FRESH local roots through the real `fold_strata.py` — the
live served store was never touched:

| side | rows folded | claims | quote% | page% | sha% | manifest sha verifies |
|---|---|---|---|---|---|---|
| python soak | 1,993 | 490 | 100 | 94.5 | 100 | ✓ |
| rust soak | 1,993 | 1,873 | 100 | 88.7 | 100 | ✓ |

Both engines' rows fold into the staging form (`parts_burn.jsonl` +
`fold_manifest.json` self-verifying). **The live fold remains the store
owner's call**; exact commands in the M3 report §5.

## Deliverable 4 — The 25.6% longer Rust runtime, decomposed

Measured, not modeled:

- Rust worker-busy: **473,776 s over 24,111 s wall → 19.65 docs in
  flight** (89.3% of 22 workers). Python: 25 threads at a machine-stable
  25-docs/240 s cadence → ~25 in flight. **25 / 19.65 = +27% ≈ the
  observed +25.6%** — the gap is average in-flight docs, NOT per-doc
  slowness: per-doc endpoint work is equal (237 vs ~240 busy-s/doc).
- Shim spawn overhead measured at **31 ms** (replay-cache probe, no
  endpoint): ~0.6% of the gap — noise.
- Repair-load difference (163 vs 47 claim-docs × ≤2 repair rounds) is
  inside the busy time and did not lengthen the wall beyond the in-flight
  effect (endpoint saturated in both windows).
- Levers if parity matters: 25 workers, chunk-size 25 (fewer boundary
  gaps ≈ half the 10.7% idle), persistent shim pool. Optional — the
  endpoint is the shared ceiling either way.

## Deliverable 5 — Six missing substrates + page-attribution delta

- **There are no missing substrates.** The frozen 2,000-doc cohort
  contains **six byte-identical duplicate PDFs** (LM27951, LM5109B,
  TLC59213A, TPS51116, UCC27282, UCC27323 — sha-verified twins across
  `cr-swarm-20260918/gold-fill-p0` and `gold-fill-w1`). Unique documents:
  1,994; both engines covered 100% of them. Intake note for the corpus
  owner: sha-dedupe landing waves (the registry-gated corpus builder
  already does this; the landing tree is not registry-gated).
- **Page attribution 88.7% vs 94.5%** is claim-mix, not kernel: null-page
  claims are the same classes on both sides (longer quotes, 37–40% with
  newlines, same top symbols: TA/fPWM/thresholds); Rust simply carries
  3.8× more surviving claims from its window, weighted toward the
  harder-to-locate tail. On identical-raw stems, page attribution matched
  byte-for-byte (deliverable 1). Post `value_in_quote` fix, marginal
  claims survive uniformly on both engines.

## Deliverable 6 — Staged cutover plan (no production cutover performed)

**Stage 0 — evidence base (DONE, this assignment):** frozen-response
parity 127/127; replacement gates green post-fix; kill-recovery proven at
volume; staging form verified.

**Stage 1 — deterministic verify cutover** (dw-truth/dw-normalize for
verify+normalize; Python model lane unchanged):
- Gate: frozen-replay parity = **0 diverging stems** on a fresh 200-doc
  stratified sample (re-record; ≥10 min run); replacement gates
  condition+quote `cutover_permitted yes`; workspace 277/277.
- Rollback: revert to `harness.pipeline.quote_verify` at the call site
  (single import); no data migration (outputs byte-equal).

**Stage 2 — orchestration adoption** (dw-controller owns queue/lease/
recovery for burns; burn_aisle remains the model client):
- Gate: one production-shaped burn of ≥1,000 docs via `dw-soak` with 0
  failed chunks after retries, recovery drill passed (kill -9 → re-claim,
  zero loss/duplication — already demonstrated ×3), throughput ≥ 0.9×
  Python at equal worker count.
- Rollback: the Python ThreadPool path still exists untouched; flip the
  launcher back. The shared output format makes both paths' rows
  interchangeable.

**Stage 3 — model-lane single client + cliff policy** (needs Sam's
sign-off): keep ONE provider client (the shim, or its dw-model port when
an HTTP shell lands); optional attempt-2 rerun on no_claims docs
(measured flip rate supports ~50% recovery of cliff claims at ~2×
endpoint cost per affected doc).

**Stage 4 — staging + release**: live `fold_strata` by the store owner
(procedure delivered), then the normal release path. Approval chain:
M5 (this lane) recommends → Sam approves → CR executes release-side.
Gold promotion and production deploys remain out of scope here.

**Acceptance gates, measurable:** (a) replay parity = 0 diffs; (b)
replacement ledgers empty; (c) failed chunks = 0 post-retry; (d)
throughput ≥ 0.9× at equal workers; (e) board Δ moves by the folded row
count; (f) every gate ships a negative control (maintained: 6 this
assignment alone).

## Environment notes

NAS instability continued (yosemite NFS hung through the staging window —
local-copy verification used instead; charley SMB up at time of writing).
Reported to mac-mini with timestamps. The soak ran on local substrate
copies and was unaffected.

## Artifacts

- Replay: `scratch/m5-soak/{replay-cache.jsonl, replay-*, final-*,
  cliff-results.json, final-comparison.json}`
- Scripts: `soak_model_shim.py` (live/record/replay/fill + trace),
  `replay_python_burn.py`, `compare_frozen_replay.py`,
  `cliff_replay_experiment.py`
- Fixes: `dw-truth` value_in_quote (+negative control), `dw-soak`
  raw-claims round trip + page stringify + canonical-core writer,
  `soak_schema.rs` (canonical-hash guard, 5/5 under `--workspace`)
- Gates at close: fmt ✓ · clippy `-D warnings` 0 · **workspace 277/277** ·
  deny 0 · dw-parity replacement (quote) re-proven · frozen-replay 127/127
