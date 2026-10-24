# RA-03 CLOSEOUT — final receipt — 2026-10-18

**Verdict: PASS** · commit: this commit (`agent/power-data-01`) ·
command: `python3 scripts/ra03_reconcile.py --out-dir results/soak-m3-rust`
· machine output: `ra03-final-reconciliation.json` +
`ra03-per-document.jsonl` (one row per document identity)

## 9,201-document identity reconciliation — exactly once

| item | count |
|---|---|
| input documents (POWER aisle, frozen cohort) | **9,201** |
| unique identities (stem + pdf + doc sha) | **9,201** |
| duplicate stems in cohort | 0 |
| double-processed stems (silent overwrites) | **0 — none** |
| unresolved / missing | **0** |
| failure evidence retained (superseded rows kept) | **yes** |

**Authoritative dispositions (each stem exactly one):**

| disposition | n |
|---|---|
| ra03-out retained (valid pre-incident era) | 944 (924 no_claims + 20 claim-docs) |
| fixup-out (catch-up redo, post-fix shim) | 8,257 (4,826 no_claims + 3,431 claim-docs) |

Malformed/unreadable files: **0** (loader reports any by path; none found).

## Chunk / lease / attempt accounting

| run | completed | failed (recorded error) | stale lease | seal verify |
|---|---|---|---|---|
| ra03 (original canary) | 346 | **22** (550 items, `lease expired`, attempt 3) | 1 (25 docs, abandoned; recovered via fixup) | 346 ok / 0 bad |
| fixup (catch-up) | **331 / 331** | 0 | 0 | 331 ok / 0 bad |

Every failed-chunk document was in the redo cohort and is present in
fixup-out (pending_redo = 0). The 22 failures are the measured
lease-expiry class (docs >900 s under endpoint latency; fixed at 3600 s
+ per-doc renewal for the redo) — recorded, recovered, not hidden.

## Baseline differences (per document, Python vs Rust authoritative)

| class | n | explanation |
|---|---|---|
| skip_match (both empty, same class) | 4,510 | agreement at the cliff floor |
| rust-claims / python-empty | 1,474 | cliff: one-invocation model variance (proven 1/5–5/5 flips) |
| python-claims / rust-empty | 1,234 | same cliff, other direction |
| raw_diff_both_claims | 1,925 | model-lane run variance (different proposals; both gate-checked) |
| identical raw survivors, proposed-count diff | 5 | verify outcome identical; upstream proposal counts differ |
| identical raw + identical verdicts | 42 | full agreement |
| python `error:timed out` rows | 11 | baseline-side errors (their own recorded dispositions) |

The cliff classes are **empty-vs-claims**, never wrong-value contamination:
every stored claim is gate-checked (quote verbatim in source + value in
quote + column binding).

## Claims & evidence

| measure | value |
|---|---|
| surviving claims | **123,136** across 3,451 docs |
| quote-in-source coverage | **100%** |
| doc_sha coverage | **100%** |
| page coverage | 81.5% (remainder = documented unlocatable class, named absence) |
| source-to-claim re-verification sample | 36/36 quote+value true; 29/36 page-span verified |

## Spend (preserved; no further paid calls)

**$30.0123** cumulative across 5,724 paid calls (cap $30 — 1.2¢ bounded
overshoot from in-flight at trip; authorization: Sam's $30 approval).
Reconciliation itself made zero calls.

## Verdict dimensions

| dimension | result |
|---|---|
| execution_complete | ✅ all chunks reconciled-terminal (completed+sealed, or failed-with-recorded-error + recovered) |
| claims_valid | ✅ zero claims missing quotes |
| verified_evidence | ✅ quote 100% + sha 100% chains complete |
| document_complete | ✅ 9,201 identities exactly once |

**VERDICT: PASS.**

## Remaining P0 blockers

**None in this lane.** Coordinated-outstanding (not blocking RA-03):
- cr-core: SiC46x admission answers + 7,181-OPN intake packet review
  (packet delivered 2026-10-15; questions overdue 2026-10-16)
- M4: bundle v4 ingestion of TI efficiency rows (delivered `363bc4f1`)
- Agent 2: interface handoff ack (delivered 2026-10-17)

## Incidents recorded (none hidden)

1. shim recursion defect (mine, 23:28–23:30 window): 7,890 false no_claims
   — classified failures, preserved in ra03-out, recovered via fixup.
2. 2048 max_tokens truncation (mine): billed fallthroughs ≈ $0.018,
   cost recorded; raw outputs lost to an overwrite (disclosed 2026-10-17).
3. lease-expiry class: 22 chunks failed in the original run — recorded,
   recovered; bound measured (>900 s docs), fix deployed (3600 s + renewal).
