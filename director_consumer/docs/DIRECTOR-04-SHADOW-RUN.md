# DIRECTOR-04 — shadow-run report

**workspace:** /tmp/d04-shadow · live read-only run + coordination
simulation in an isolated mailbox. No production messages; production
delivery gate stayed closed.

## Live run (real feeds)

553 events → 76 assessments (49 historical suppressed) → 5 proposals.
Three incidents open with lifecycle `detected`:
`factory-supply-exhaustion` P1 · `intake-contamination` P2 ·
`watcher-failure` P1. Replay idempotent; chain verifies.

## Definition-of-done proof (against the live supply incident)

The real `factory-supply-exhaustion` incident was driven through the full
coordination workflow with a simulated agent in an isolated mailbox:

1. **Assigned:** task `SUPPLY-RECON-01` created with incident as
   evidence, offered to `agent:m5-opencode` under director authority.
2. **Communicated:** the mission's exact request ("Factory supply is at
   risk… confirm ownership, identify the cause, return evidence.")
   delivered to the isolated mailbox (simulated authorization).
3. **Acknowledged:** ack artifact recorded — separate from delivery.
4. **Tracked:** lifecycle `investigating → mitigation_proposed →
   repair_in_progress` (admission-step repair).
5. **Verified:** `recovery_observed` + `root-cause-fixed` evidence
   (measured queue depth > 0 after admission; inventory admitted, not
   deleted) → `recovery_verified` → `closed`.
6. **Task closed** with independent verification evidence; message `acked`.

Result: incident `closed`, task `closed`, message `acked`, recovery
evidence `['root-cause-fixed']`. The whole flow survives restarts
(replay tests), duplicate events (fingerprint/ID idempotency), model
outages (deterministic path), and machine migration (identity/fencing).

## Failure-injection summary (§10)

24/24 `tests/test_director_04.py`: two-agent conflict, ack-then-disappear
(stale + fence), owner migration, duplicate delivery, delivery-without-ack
expiry, crash-commit-vs-delivery reconciliation, failed verification →
active, symptom-clear rejected for verified recovery, recurrence reopen,
manufacturer feed absence (no fabrication), admission-gap diagnosis,
queues-unavailable distinction, project isolation, restart no-dup, and
metered refusal on child paths. Plus the 72 inherited director tests.

## Unresolved risks

- Production delivery remains disabled (by design) — the mailbox step is
  simulation-proven only until approval.
- The 17 historical tasks stay unowned pending authority answers
  (reconciliation requests ready, not sent).
- Manufacturer telemetry needs the mac-mini producer to adopt the contract.
- Per-doc processing-time telemetry missing (worker-hours stay PARTIAL).
- The admission-gap repair itself is a production change owned by the
  lane owner — the director proposes, never edits queues.
