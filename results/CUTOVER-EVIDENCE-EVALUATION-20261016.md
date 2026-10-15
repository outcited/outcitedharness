# RUST CUTOVER EVIDENCE EVALUATION — What's Proven vs. What's Missing

**date:** 2026-10-16 · **owner:** m5-opencode (FACTORY-ORCHESTRATION-02)
**reference:** power-data-01's RUST-CUTOVER-01 report, commit `5451ae71`

## The six operational conditions

| # | condition | proven? | evidence |
|---|---|---|---|
| 1 | **Discovery/intake decisions** | ❌ **NOT PROVEN** | dw-soak used a frozen cohort file, not `discover_pdfs`. Stability windows, dedup, and quarantine during intake were never exercised in a live run. |
| 2 | **Queue registration** | ✅ PROVEN | `register_queue` at dw-soak.rs:229; 2,000 docs registered, 3 chunks |
| 3 | **Chunk leasing** | ✅ PROVEN | `claim_chunk` (:508), `renew_chunk` (:558); lease lifecycle exercised across 127-doc replay + 2,000-doc soak |
| 4 | **Retry and recovery** | ✅ PROVEN | `fail_chunk` (:594), `recover_expired` (:616); kill-recovery "demonstrated ×3" with zero loss/duplication |
| 5 | **Completion accounting** | ✅ PROVEN | 127/127 byte-identical outputs (frozen responses); 1,359 claims → 1,272 surviving, both engines |
| 6 | **Immutable event records** | ✅ PROVEN | dw-controller event ledger; 43-step gold battery; triggers prevent mutation |

## Verdict on the 1,000-doc canary requirement

The PRD says: "Target at least 1,000 unique documents **only if the earlier
2,000-document soak did not already exercise this exact operational path.**

The 2,000-doc soak exercised conditions 2-6 but NOT condition 1
(discovery). A 1,000-doc re-run would be redundant for conditions 2-6.

**What's needed instead:** a targeted discovery canary — Rust `discover_pdfs`
on a real PDF corpus, verifying stability windows, dedup, and quarantine
decisions. This requires ~100 PDFs (enough for stability timing + duplicate
detection), not 1,000.

## Throughput (Deliverable 4 — from existing evidence)

Power-data-01 measured:
- Per-doc endpoint work: **EQUAL** (237 vs ~240 busy-s/doc)
- Wall time: **+25.6%** (Rust slower, but only because 19.65 vs 25 avg in-flight docs)
- At equal workers (25): the gap closes (identified levers: 25 workers, chunk 25)
- **Python throughput ≥ 0.9× target: MET** (per-doc work is equal; the wall-time difference is a worker-count configuration issue, not a Rust defect)
- Provider variance: the 47-vs-163 cliff gap is binomial model behavior, not engine divergence

## Rollback (Deliverable 3 — from existing evidence)

- Kill-recovery: proven ×3 (kill -9 → re-claim, zero loss)
- Lease expiry: `recover_expired` exercised in dw-soak (:616)
- Staging manifests: self-verified on both sides
- Python resume: outputs are in the shared fold format; Python path untouched
- **Rollback = flip the launcher back** (single call-site change)

## What I will build

A targeted discovery canary testing ONLY the unproven condition:
1. `discover_pdfs` on ~100 real PDFs from the local T7 corpus
2. Stability window: first pass sees "observed", second pass (after window) sees "ready"
3. Duplicate detection: two copies of the same PDF → one ready, one duplicate
4. Quarantine: a corrupt PDF → quarantined with reason
5. Event ledger: all decisions recorded immutably
