# CUTOVER PLAN — Python Factory → Rust dw-controller

**date:** 2026-10-15 · **owner:** m5-opencode · **status:** draft for Sam's review

## 0. What cutover means

The Python factory (`burn_aisle.py`, `extract_dispatch.py`, the various
`extract_*.py` lanes) hands orchestration to the Rust `dw-controller`
crate. The Rust side owns: document discovery, stability windows, chunk
leasing, retry logic, and the immutable event ledger. Python keeps only
the model-call lane (S3 extract) and the verifier (S4).

```
BEFORE (today):                    AFTER (cutover):
Python discovers PDFs              Rust dw-controller discovers
Python manages retries             Rust leases + retries + events
Python extracts (model calls)  →   Python extracts (unchanged)
Python verifies (quote/page)       dw-truth verifies (parity-proven)
Python writes results             Rust writes results + events
No audit trail                     Immutable factory_events ledger
```

## 1. Why cutover is safe (the evidence)

| gate | evidence |
|---|---|
| dw-controller = Python behavior | 43-step gold battery vs live `factory_control.py`, byte-exact |
| dw-truth = Python verifier | 40/40 live parity cases, 0 discrepancies (§2 of handoff) |
| dw-normalize = Python parser | 10,270/10,270 live parity (replacement gate green) |
| Schema shared | SQLite schema verbatim — both engines operate one `factory.sqlite3` |
| Substrate lookup | Now local (instant), no NAS dependency |

## 2. The phased plan

### Phase 0 — Parallel run (1 week, zero risk)
Both engines run side by side on the same document stream. Python
continues as primary; Rust observes and records events but does not
route work. Compare outputs daily.

- Python: `burn_aisle.py` → results/ (unchanged)
- Rust: `dw-controller` discovers same PDFs, records events, does NOT
  claim chunks (read-only mode)
- Compare: daily diff of event ledger vs Python's implicit outcomes
- Exit: 7 days of matching discovery + stability + dedupe decisions

### Phase 1 — Rust owns discovery (1 week)
Rust takes over S1 (intake) and S2 (stability/dedupe). Python receives
its work queue FROM Rust instead of discovering on its own.

- Rust: `discover_pdfs` → `register_queue` → writes work chunks
- Python: reads chunks, extracts (S3), verifies (S4), writes results
- The lease lifecycle is Rust-owned but single-attempt (no retry logic
  in Python — Rust handles retry via lease expiry)
- Exit: 7 days, zero lost documents, retry accounting correct

### Phase 2 — Rust owns the full loop (ongoing)
Rust manages the entire lifecycle. Python becomes a pure model-call
library (S3 only). The verifier (S4) moves to dw-truth.

- Rust: discovery → chunking → leasing → monitoring → completion
- Python: model calls only (called from Rust via subprocess or HTTP)
- dw-truth: verification (already parity-proven)
- Exit: Python's `factory_control.py` and `burn_aisle.py` retired

### Phase 3 — Optimization (post-cutover)
- Async model calls (HTTP client pool in Rust)
- Multi-lane extraction (Rust orchestrates, multiple Python workers)
- The 25k/week target becomes a config parameter, not an aspiration

## 3. What doesn't change

| thing | why it stays |
|---|---|
| The model providers | TP4 local, OpenRouter cloud, the routing law |
| The extraction prompts | dw-model already owns them (byte-identical) |
| The gold fixtures | They gate both engines |
| The substrates | Local disk, indexed |
| The served store | api-store-export format unchanged |

## 4. Risks and mitigations

| risk | mitigation |
|---|---|
| Rust bug loses documents | Phase 0 parallel run catches it; events ledger is immutable evidence |
| Model-call latency differs | Python subprocess is the same code; only the orchestrator changes |
| Concurrent access to one db | SQLite WAL + busy_timeout (already configured); one writer at a time |
| Other agents' lanes break | Each lane uses its own db + output dir; cutover is per-lane |
| Rollback needed | Phase 0/1 are reversible (Python still runs); Phase 2 keeps Python as a fallback for 30 days |

## 5. What I need from Sam

1. Approve Phase 0 start date
2. Decide: cutover per-lane (power first, then mcu, then vault) or all-at-once
3. Confirm the 25k/week target is the Phase 3 acceptance bar
