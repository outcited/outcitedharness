# QUEUE RECOVERY — P0 pipeline queue integrity (directive 2026-10-08)

Branch `queue/claim-cap-fifo` (isolated worktree `../Harnessv1-queuefix`,
off main @ 68ab9037). All live-DB work was read-only; nothing was mutated,
retried, deleted, or dead-lettered. Live dispatcher processes
(`extract_dispatch.py`, `extract_worker.py`) were never touched.

## 1. Before / after inventory

Consistent snapshots of pipeline.db (earlier single-statement reads raced
the live dispatcher's transactions and misreported "all claimed / results
empty" — corrected here and in the search runbook appendix).

| Measure | Before fix (live, 2026-10-08) | After fix (projected, dry-run) |
|---|---|---|
| states | done 81,473 · dead 3,759 · claimed 3 · pending 0 | same, with the 3 claimed → dead (sweep) |
| results rows | 81,473 (NOT empty) | unchanged |
| attempts > 3 | 12 jobs (max 123) | 12 (historical values are provenance; no NEW over-cap claims possible) |
| zombies (expired lease at/over cap) | 3 (ids 86, 96, 189; ~hourly reclaim since Oct 3) | 0 — swept to dead + 1 ledger row |
| claimable under fixed semantics | — | 0 jobs (nothing legitimately pending) |

max_attempts is uniformly 3 across all 85,235 jobs. The three zombies run
attempts=123 ≈ 123 hourly visibility cycles ≈ 5.1 days — landing exactly on
the Oct 3 storage death, confirming the unbounded-reclaim defect's
mechanics.

## 2. Root-cause analysis

**Defect 1 — unbounded reclaim (CONFIRMED, actively harming).**
`claim()` enforced no `attempts < max_attempts` in either the candidate
SELECT or the claim UPDATE; `fail()` enforces the cap but only when called.
A worker that dies without calling fail() leaves a job whose expired lease
is reclaimed forever. Production evidence: 3 jobs at 123 attempts, still
churning hourly today (last 20:57:25), all pointing at
`/Volumes/macbookM4-4TB/...` (unmounted pre-migration volume).

**Defect 2 — DESC ordering on corruptible timestamps (CONFIRMED, latent).**
`ORDER BY created_at DESC` put future-stamped rows permanently ahead of
legitimate work. The corruption is real: ids 1–3,950 (the original
macbookM4 MCU cohort) all carry created_at = 2029-12-03 11:32:56–57 — a
single bulk write (2-second window) by an unknown writer; no trail in
dispatch.log; .scan-watermarks.json shows the macbookM4 root was genuinely
scanned ~2026-09-23. Today the defect is latent (pending=0; all 2029-stamped
rows are done/dead and never enter the candidate set), but any new enqueue
wave mixed with re-claimable legacy rows would be mis-ordered. The fix
orders by insertion id (rowid) — clock-independent FIFO; created_at is left
verbatim as provenance.

**Non-defects (recorded to close them out):**
- The 49,111 JOB_FAILED ledger events: 49,105 sit on Oct 3–4 (storage
  death); continuing rate ≈ 1/day from the zombie churn the fix removes.
- dead 3,759 = 3,751 vault/landing + 4 cas + 4 macbookM4 — the landing mass
  is the known CAS-junk quarantine (handoff §2), correctly dead.
- `results` is NOT empty (81,473 rows — the authoritative substrate output;
  M4 joins by sha per the handoff contract). The earlier "empty" reading
  was a racing query, not a fact.

## 3. Legacy corpus generation (tasks 3–5)

Of the 4,070 jobs referencing `/Volumes/macbookM4-4TB/...`:

| Class | Count | Disposition |
|---|---|---|
| already processed (done) | 4,063 | none |
| zombie-claimed, content missing from M5 | 3 (ids 86, 96, 189: dspic33ck64mc105, dspic33ch512mp508, attiny804) | swept to dead by the fix; NOT recoverable from vault |
| dead, content missing from M5 | 3 (avr32-16du, kl25p80m48sf0, SPRUHS1C) | unresolved — re-acquisition is the mac-mini lane's call |
| dead, content PRESENT in vault | 1 (rh850u2c-group-users-manual-hardware-rev100.pdf) | safely remappable |

The remappable one exists at
`/Volumes/M5_4TB/vault/landing/cr-swarm-20260918/rh850u2c-group-users-manual-hardware-rev100.pdf`,
sha256
`3636b2bd5456237e2627cebed130979cf7cbc559d00032055da558d94df6277e` —
recommended as a NEW job (`corpus_key=sha256:3636…`, source_path = the
vault path) rather than a retry of job 3675. No blind retry, no deletes,
no bulk dead-letter performed.

## 4. Regression tests (task 2)

`tests/test_pipeline_store.py` — 8 existing + 6 new, all green:

1. `test_expired_lease_reclaim_stops_at_attempt_cap` — crash-without-fail
   cycles stop at max_attempts; job retires dead with exactly one ledger row.
2. `test_zombie_sweep_never_touches_active_lease` — sweep only fires on
   expired leases.
3. `test_claim_order_is_fifo_by_id_not_created_at` — future-stamped older
   job still wins by insertion order.
4. `test_concurrent_claim_no_double_delivery` — active lease is never stolen.
5. `test_crash_recovery_is_bounded` — attempts can never exceed the cap.
6. (existing roundtrip/expiry/fail tests unchanged — behavior compatible)

## 5. Proposed safe migration (needs approval; nothing executed)

1. Approve stopping `extract_dispatch.py` / `extract_worker.py` (they run
   the old code; the fix loads only on restart). One operator does the
   stop-pull-restart — not this agent.
2. On restart with this branch, the first `claim()` call sweeps the 3
   zombies to dead (+1 ledger row) and claims nothing else. That is the
   entire live-queue effect of this change.
3. Optionally enqueue the rh850u2c remap as a new sha-keyed job.
4. Leave created_at untouched (provenance). Scheduling no longer reads it.
5. mac-mini lane decides on re-acquiring the 6 missing MCU PDFs.

## 6. Answers to the directive's questions

1. Active dispatcher? YES — `extract_dispatch.py:209` calls
   `store.claim()`; both processes live (ps verified). max_attempts = 3
   everywhere; the 12 high-retry jobs range 48–123 attempts.
2. Bounded claim/reclaim + terminal handling: implemented + tested (§4).
3–5. Legacy audit + SHA join: §3 (1 remappable, 6 missing, 4,063 done).
6. 2029 timestamps: single bulk write over ids 1–3,950, unknown writer, no
   log trail; effect was potential starvation under DESC ordering; the fix
   is clock-independent (id FIFO) and preserves created_at verbatim.
7. `results` holds 81,473 substrate outputs — the authoritative current
   extraction-output path (with burn waves + catalog.db downstream); the
   "empty results" report was a query artifact and is retracted.
