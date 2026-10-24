# RUST-CUTOVER-02 — Verification Cutover Readiness — 2026-10-17

**PRD:** revised RUST-CUTOVER-02 (replaces the earlier instructions).
**Owner:** power-data-01 (RC01 agent). **Baseline:** `5451ae71` → this
branch tip. **No production cutover, no gold promotion, no new
architecture.** Accepted RC01 evidence was NOT repeated; only unmet or
behavior-relevant gates ran.

## Deliverable 1 — Verification switch readiness (DONE)

Two seams, one env each, defaults Python, reversible by env flip. Python
model calls, prompts, provider routing, claim order, units and citations
untouched. No second implementation — the Rust engines are the SAME
dw-truth/dw-normalize code the replacement gates prove.

**Exact files changed (the whole integration surface):**

| file | change |
|---|---|
| `harness/pipeline/verify_provider.py` | NEW — verification seam (`verdicts`, `quick_ok`; python = the pre-seam loop verbatim; rust = dw-verify subprocess; fail-closed `VerifyProviderError`) |
| `harness/electronics/normalize_provider.py` | NEW — normalization seam (`parse_condition`; python = reference import; rust = dw-parse subprocess; fail-closed `NormalizeProviderError`) |
| `scripts/burn_aisle.py` | 3 hunks vs the CURRENT live factory file: seam import; `_quick_ok` routes through seam; verdict loop routes through seam. Plus one repo-relative `sys.path` line (a checkout carries its own harness). Default path byte-identical. |
| `harness/discovery/evaluator.py` | 1 hunk: `parse_condition` imported via the seam; `condition_covers` stays on the reference module |
| `designwins/crates/dw-truth/src/bin/dw-verify.rs` | NEW thin bin — burn_doc's verdict/quick_ok loops on the frozen-replay-proven kernels |
| `designwins/crates/dw-parity/src/bin/dw-parse.rs` | NEW thin bin — dw-normalize `parse_condition` in the reference wire shape (dw-parity is the bridge crate; dw-normalize stays dependency-free) |
| `tests/test_{verify,normalize}_provider_seam.py` | NEW — 12 tests |

**Activation:** `BURN_VERIFY_ENGINE=rust` / `BURN_NORMALIZE_ENGINE=rust`
(per run/process; no flag files, no migration).
**Rollback:** unset the env (or `=python`) — instant, byte-equal to
pre-seam (pinned by tests). binaries: `cargo build --release -p dw-truth
--bin dw-verify -p dw-parity --bin dw-parse`.

## Deliverable 2 — Final regression gate (ALL GREEN)

| gate | result |
|---|---|
| dw-parity replacement — condition (live reference) | 10,270 subjects, **0 discrepancies, cutover_permitted yes** |
| dw-parity replacement — quote (live reference) | 40 cases / 9,890 claims, **0 discrepancies, cutover_permitted yes** |
| workspace fmt / clippy `-D warnings` / tests / deny | ✓ / 0 errors / **277/277** / 0 |
| seam tests (fail-closed, dispositions, sha/page immunity, env-flip restore) | **12/12** |
| **fresh 3-way frozen replay, 200 stratified docs** (see below) | **200/200 byte-identical on all three paths** |

The one unmet gate from the earlier PRD (fresh 200-doc replay) was
executed because the verify path CHANGED since RC01 (seam + kernels) —
newly recorded cache (408 responses incl. 160 endpoint exceptions),
strata: prose-hyphen (25), repair/non-ASCII (25), null-page, claim-heavy
(30), python-claim (11), prior exception sample (20), double-skip (40),
random fill. Results, zero replay-cache misses anywhere:

- **Rust engine vs Python engine: 200/200 identical** — 1,674 claims in →
  1,535 surviving, both sides, byte-for-byte.
- **py+python-verify vs py+rust-verify (the seam itself): 200/200
  identical.**

Zero unexplained deterministic differences.

## Deliverable 3 — Agent 2 coordination (DELIVERED)

`results/soak-m3-rust/AGENT2-INTERFACE-HANDOFF.md` — the verified
interface contract (both seams + CLI shapes), runtime requirements
(build cmds, ~30 ms process overhead, the **preserve_order hard
constraint**, claims-raw ordering rule), replay fixtures and where they
live, failure/rollback behavior (tested), and canary constraints
(worker model, lease policy, model-lane boundary, manifest contract).
Mailed to the lane with ATTN Agent 2.

## Deliverable 4 — Recommendation

# READY FOR CONTROLLED ACTIVATION

of `BURN_VERIFY_ENGINE=rust` and `BURN_NORMALIZE_ENGINE=rust`, per the
staged plan, under these controls (all satisfied by the evidence above):

1. Activation is per-run env — flip, observe, unflip; no migration.
2. Gate for promoting a run to staging: zero `error:*` dispositions
   attributable to `*ProviderError`, claim/surviving counts within
   replay-parity expectations, replacement ledgers empty.
3. Rollback path tested (12/12) and documented in the handoff.
4. Unresolved parity defects: **none open.** (Two found in RC01 were
   fixed with negative controls; the fix is included in this build and
   re-proven by the 200-doc replay.)

Conditions that are NOT yet true and are explicitly out of scope here:
production cutover authorization, gold promotion, controller-level
adoption (Agent 2's canary), and any change to model-lane ownership.

## Evidence index

- 3-way replay: `scratch/m5-soak/{rc02-cache.jsonl, rc02-rust-out,
  rc02-pypy-out, rc02-pyru-out, rc02-record-out}` (record pass 200/200
  docs, 20/20 chunks)
- Replacement gate outputs: re-run 2026-10-17, both `cutover_permitted
  yes`
- RC01 accepted evidence unchanged and unrepeated (127/127; 2 bugs fixed;
  43-step gold; 2,000-doc soak; kill/recovery ×3; staging verified)
