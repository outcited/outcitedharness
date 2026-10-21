## DIRECTOR-07 §1.B RECEIPT — investigation persistence fixed on real DirectorState schema

**Commit:** `13aa618a` on `fix/sentinel-live-reconcile-20261019` (pushed; branch base `2ef8eaf3`)
**Addresses:** review [6277368358](https://github.com/outcited/outcitedharness/issues/12#issuecomment-6277368358) pt 3 + [6277037762](https://github.com/outcited/outcitedharness/issues/12#issuecomment-6277037762) — all four code-side bugs; bug 5 is ops-side (staged, see below).

### Fixes (assertions, each pinned by tests)

1. **Persistence is canonical.** `_persist` no longer creates an ad-hoc `investigations` side table on a live connection. Receipts go through `DirectorState.journal_investigation` → `kind='investigation'` inside the **hash-chained journal**; `verify_chain()` stays green after inserts; a test asserts the side table is never created. Persistence errors are recorded (`persisted`/`persist_error`), never swallowed.
2. **Typed source identity.** `run_code_intel` derives source revision as `sha256(assessment journal payload blob)[:32]` — stable while evidence is unchanged (dedupe holds across ticks), changes when evidence changes (re-opens). The old `kind='journal_run'` lookup never matched the writer's `kind='run'` and sliced untrusted JSON — dedupe was inert; that query is gone.
3. **Acknowledged dedupe.** The fingerprint enters the dedupe set only after the journal write **commits** and the status is terminal (`investigated`/`no_hits`/`insufficient_evidence`). Retryable statuses (`llm_unavailable`/`source_unavailable`) journal a receipt but are NOT acknowledged — they retry. A failed journal write never suppresses a retry (test simulates a failing journal, then recovery, and proves the redo happens).
4. **Starvation fixed.** Deduped heads no longer consume the one-per-cycle budget: the loop skips `dedupe_skip` results for free and spends the budget on the first non-deduped distinct incident.

### Evidence

- **17/17 tests pass on the real `DirectorState`** (fresh workspace per test, hash chain exercised): restart-safe dedupe, retry-after-persist-failure, starvation, source-identity stability across ticks, chain verification, terminal-vs-retryable acknowledgement.
- **Clean checkout:** fresh `git clone --branch` at `13aa618a` → 26/26 (investigate + sentinel suites).
- **Zero regressions:** full `tests/test_director*.py` suite on this branch has an **identical failure set** to the `2ef8eaf3` base — 8 pre-existing failures (01a backfill-suppression, 01b idempotency, 02 fleet/LLM-outage ×3, 03 outage, consumer ×2). Those pre-date this branch and are **not** claimed fixed here; they're another lane's debt, reported rather than hidden.

### Outstanding gates (not declaring bug 5 fixed)

- **Bug 5 — GCI token hardcoded in root `run-observer.sh` (seven):** verified present at line 5 this session (value redacted everywhere). Ops-side fix staged on seven, zero production impact until [SAM] executes: `~/seven-director/run-observer.sh.new` (credential-file/LoadCredential reader, fails closed) + `~/seven-director/RUNBOOK-token-rotation.md` (mint → install → swap → restart → verify → revoke, with rollback before the revoke step).
- Also tightened this session: user-observer `gci.conf` was mode **664** → now **600** (no restart needed; observer still `active`).
- **Receipt posting is credential-blocked** (dead classic PAT + fine-grained PAT lacks issues:write). Both §1.A and §1.B receipts are staged at `~/RECEIPT-DIRECTOR-07-1A.md` / `~/RECEIPT-DIRECTOR-07-1B.md` on M5 — post when the token is fixed (runbook step 7).

**Branch:** `fix/sentinel-live-reconcile-20261019` @ `13aa618a` (3 commits: sentinel reconciliation, unwritable-dir test, this fix).
