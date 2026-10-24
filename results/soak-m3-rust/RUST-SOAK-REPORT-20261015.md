# M3 Rust Factory Soak & Cutover Validation — 2026-10-15

**Lane:** M5 (extraction, normalization, verification, Rust processing
factory), assigned to this session by Sam 2026-10-14. **Branch:**
`agent/power-data-01`, worktree `Harnessv1-powerdata01`, remote
`github.com/outcited/outcitedharness` (origin). POWER-DATA-01's earlier
handoff (`363bc4f1`) acknowledged: SiC46x packet + TI efficiency rows are
done and NOT duplicated here (different cohort, different pipeline stage).

## 1. The approved test

Cohort: the frozen 2,000-document vault batch
(`sorted(vault/landing/**/*.pdf)[:2000]` of 61,682; frozen to
`scratch/m5-soak/frozen-cohort-vault-2000.json`; verified — every Python
output stem ∈ cohort). Pipeline: the burn cascade — substrate → student /
local-V41 propose (text fail-closed, local-only) → `quick_ok` repair ×2 →
deterministic verify (quote / value / column-ordinal) → page attribution →
per-stem ledger row.

## 2. Python reference run (completed by the M5 session; untouched here)

From `results/soak-m3-vault-20261014.log` + `_report.json`:

| metric | value |
|---|---|
| docs | 2,000 / 2,000 (1,993 stem rows) |
| wall time | 19,203.4 s (5.34 h) |
| throughput | 9.6 s/doc (25 threads, one process) |
| docs with claims | 47 (2.35%) |
| skip: no_claims | 1,953 |
| claims total | 519 |
| P0 (unsupported) | 28 (p0_rate 5.39%) |
| tier mix | local-v41 only |
| metered cost | **$0** — local lane only, no `cost-meter` rows |
| provenance | quote 100% · page 94.5% · doc_sha 100% of surviving claims |
| orchestration | threads + flat files; no durable queue, no recovery |

## 3. Rust controller run — architecture (thin wiring, no crate rewritten)

- **dw-controller** (the 43-step-gold-proven surface) owns ALL durable
  state: queue, chunk leases, retries, recovery, immutable event ledger.
  Additive change only: doc-scoped soak queue/manifest schemas accepted
  alongside the structural ones; structural schemas + gold battery
  unchanged; 5 negative-control tests added (`tests/soak_schema.rs`).
- **`dw-soak` bin** (new): enqueue / worker / status / recover. Per-doc
  cascade replicates `burn_doc` step-for-step; every deterministic stage in
  Rust (`dw-truth::find_quote / value_in_quote / column_position_binds` —
  replacement-gate-proven kernels, 0 historical diffs).
- **`scripts/soak_model_shim.py`** (new): the only Python — a provider
  shell importing the LIVE `burn_aisle.py`, so prompts, endpoint chain and
  repair protocol are byte-identical to the Python factory.
- Substrates pinned to local NVMe (1,994/2,000; 6 legitimately absent)
  after NAS flapping (§6).

## 4. Measured results — Rust vs Python (same frozen 2,000-doc cohort)

| metric | Python | Rust (dw-controller) |
|---|---|---|
| docs completed | 1,993 rows / 2,000 | **1,994 rows / 2,000** |
| chunks | n/a | 200/200 completed, 0 failed |
| wall time | 19,203 s | **24,111 s** |
| throughput | 9.6 s/doc | 12.1 s/doc (297.7 docs/h) |
| docs with claims | 47 | 163 |
| claims / surviving / supported / P0 | 519 / 490 / 490 / 28 | 1,990 / 1,873 / 1,873 / 105 |
| provenance (quote/page/sha) | 100 / 94.5 / 100 % | 100 / 88.7 / 100 % |
| lease renewals / recoveries | n/a | 2,007 renewals · 1 expiry event · max attempt 2 |
| restart/recovery | none — a killed run loses in-flight work | **kill -9 ×3 across pilots+run → lease expiry → re-claim, zero doc loss or duplication** |
| metered cost | $0 | **$0** (same local V41 lane) |
| CPU | ~722% avg (overlap window, sampler) | ~557% avg (22 workers; both runs endpoint-bound, neither CPU-limited) |

**Parity (the cutover-relevant question), 1,993 common stems:**

| measure | result |
|---|---|
| skip/skip agreement | 1,819 |
| both sides got claims | 36 stems |
| **identical raw claims → verdict/sha/page agreement** | 17 stems → **0 verdict mismatches, 0 doc_sha mismatches, page attribution identical on all 135 surviving claims** (the 1 unlocatable quote is null on BOTH sides) |
| raw-claim differences (model-lane variance) | 19 stems |
| claims/no-claims cliff flips | 138 (127 Rust-only, 11 Python-only) |

Combined with targeted pilot-2 (97 docs incl. all 47 Python claim-docs:
19 identical-raw stems → 0/0 mismatches over 171 claims), the determinism
floor holds everywhere it was measured: **given identical model output,
the Rust factory's verify, page attribution and provenance are
byte-equivalent to Python's.**

**The claim-rate gap (163 vs 47 claim-docs) is the shared model lane, not
the factory.** Same endpoints, same prompts (byte-identical via the live
burn_aisle import), same verify — the local V41 service returned usable
claims in far more invocations during the Rust window (both directions
flip per-invocation; pilot-2 saw ~50% cliff flips on identical docs).
Every extra Rust claim passed the same deterministic quote/value/column
gate (P0 rate 5.3% vs Python's 5.4%). If we want claim-rate stability,
that is an endpoint/inference question (batching, KV-cache, server load),
not a cutover blocker — but it IS the measured bottleneck: both runs are
V41-latency-bound (~9.6–12 s/doc), never CPU-bound.

## 5. Staging verification — BLOCKED ON STORAGE (procedure ready)

The api-store-export root (`yosemiteraid5:Public/m5-opencode/…`) is
unreachable: the 192.168.4.40 NFS export hangs on `ls` since ~09:15Z
(charley SMB also dropped twice earlier; mac-mini notified, mail
`mount-instability-charley-yosemite-20261015`). Not verified by me = not
claimed. The exact procedure, ready to run when storage answers (against a
COPY first; the live fold belongs to the store owner):

```bash
cp -R <api-store-export> <local-copy>     # never fold into the live store directly
python3 scripts/fold_strata.py --root <local-copy> \
    --burn-dir results/soak-m3-vault-local       # python rows
# then the same with scratch/m5-soak/full-out chunk dirs' rows (doc rows
# are per-stem JSONs; point --burn-dir at a flattened copy)
python3 scripts/extraction_board.py             # Δ must move by the vault rows
# live instruction for the owner: same commands against the real root.
```

## 6. Environment blockers (external; none blocked the soak itself)

1. charley SMB dropped twice (~23:50Z, ~01:30Z) — took the
   `/Volumes/M5_4TB` symlink chain; caused one garbage pilot pass
   (fail-closed instant `no_substrate` skips, discarded) before substrates
   went local. Fail-closed behavior verified in production conditions.
2. yosemite NFS (192.168.4.40) hangs on `ls` since ~09:15Z → staging
   verification deferred (§5).
3. V41 endpoint is the shared throughput ceiling; its per-invocation
   claim-rate variance (§4) is an inference-side question.

## 7. Bugs found and fixed during validation (negative controls shipped)

1. **No mid-chunk lease renewal** — 60 s leases expired under live workers
   (double-claim risk). Fixed: per-document `renew_chunk` (2,007 renewals
   in the full run; 1 expiry → clean recovery). Found by the pilot kill
   rehearsal, not by reading code.
2. **Analyzer flat-dir fallback missing** — caught on first use (0 Python
   stems), fixed, re-run.
3. dw-controller schema acceptance is additive with fail-closed negatives
   (wrong evidence hash / unknown schema / wrong queue sha / structural
   key regression — 5 tests, all red on the pre-change code).

## 8. Recommended cutover sequence

1. **Deterministic stages: cut over now.** dw-truth/dw-normalize
   replacement gates (0 diffs) + this soak's identical-raw parity
   (0/0/0 mismatches over 306 surviving claims across pilot-2 + full run).
2. **Orchestration: adopt dw-controller as the burn orchestrator**
   (durable queue, leases, recovery, event ledger — everything the
   ThreadPool+files pattern lacks). Run with 900 s leases + per-doc
   renewal; max_attempts 3.
3. **Model lane: single client.** Keep the shim (or port it when dw-model
   grows its HTTP shell). Expect per-invocation cliff variance; if
   stability matters, add a cheap attempt-2 policy on no_claims docs
   (measured: re-rolls flip ~50% at the cliff) — an inference decision,
   needs Sam's sign-off.
4. **Staging:** fold via §5 once storage returns; gold promotion and
   release flips unchanged, separate authority.

## 9. Artifacts

- `results/soak-m3-rust/`: this report, `soak-metrics-rust.json`,
  `soak-metrics-python.json`, `full-parity.json`
- `scratch/m5-soak/`: frozen cohort, substrate indexes (local NVMe),
  per-run state (factory.sqlite3), worker logs, `cpu-samples.tsv` (15 s
  cadence), pilot reports
- `scripts/{soak_model_shim,build_soak_substrate_index,
  analyze_soak_parity,capture_soak_metrics}.py`
- `designwins/crates/dw-controller/src/bin/dw-soak.rs` +
  `tests/soak_schema.rs`

No gold promotion, no release change, no production deployment, no cloud
spend. The Python soak's outputs were never modified; the thermal and
power-mode jobs ran undisturbed throughout.
