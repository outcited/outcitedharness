# ACCEPTANCE-LEDGER-05C — corrective pass on 322cdd95

Review discipline honored: every negative test in
`tests/test_director_05c_fixes.py` was written FIRST and shown failing on
322cdd95 (12 failed / 3 already-correct passed), then the production path
was fixed and the same tests pass. Legacy tests that encoded weak
behavior were STRENGTHENED (never weakened) to the corrected contract.

| finding | baseline failure (322cdd95) | corrected behavior | evidence |
|---|---|---|---|
| rehearsal deletes any workspace recursively | test refused targets got rmtree'd | `prepare_workspace` creates a fresh `rehearsal-<ts>` child; refuses repos, director workspaces, homes, symlinks, mixed dirs; refusal leaves targets byte-identical | `test_rehearsal_refuses_non_test_targets...` |
| approval bound only body digest | same body+new subject/type/task/epoch/destination reused old grants (message ids even collided across types) | identity = project+recipient+type+subject+body+task+epoch; check() validates the full envelope (subject/type/transport/epoch/id/sender); one grant = one immutable action; same-action retry is idempotent, never a second action | `test_identical_body_cannot_become_second_action` |
| "body here" fixture exception in integrity check | conflicting artifacts could be overwritten by test-shaped content | any content difference = integrity conflict, returned and logged; conflicting evidence NEVER overwritten; identical content reconciles | `test_conflicting_artifact_never_overwritten` |
| missing session_bindings bypassed ack checks; caller-declared session text counted as origin | spoofed session + same UID could ack | bindings REQUIRED (fail closed); origin ESTABLISHED by transport (`verify_origin`) from the artifact; claimed text must equal established origin; invalid receipts quarantined without touching valid ack/task state | `test_missing_bindings_and_spoofed_session_cannot_ack` |
| resolve() silently hid incidents; receipts = 5 nonempty strings | incidents vanished without evidence; kind strings counted as proof | resolve() REQUIRES a validated receipt (scope must reference the incident, observed_at parses + fresh, substantive method/result); recovery_verified validates receipts of the CURRENT occurrence; investigation completion ≠ production recovery | `test_resolve_without_evidence...`, `test_receipt_validation...`, `test_verifier_rejects_false_report_claims`, `test_investigation_completion...` |
| after-write/before-sent failpoint missing; recurrence test didn't prove cause; lost-ack untested; concurrency unproven | SQL-state fudging only; "PASS" without coverage | real `os._exit` failpoint via `DIRECTOR_FAILPOINT` (exit code + checkpoint asserted); recurrence: legal recovery-verified first, old-occurrence rejection AND current-occurrence success both proven; lost-ack settled by `reconcile_with_receiver` (case H now real); concurrency released by `mp.Barrier`, every caller's outcome asserted | `test_after_write_before_sent_failpoint_real`, `test_recurrence_rejects_old...`, `test_lost_ack_receiver_reconciliation`, `test_concurrent_callers_every_outcome_accounted` |
| chunks+jobs summed as documents; starvation divided mismatched populations; "workers will idle" fired on unknowns | ready_backlog lied; rate division meaningless; idle prophecy on insufficient evidence | per-lane counts (`lane_counts`), document total UNKNOWN across lanes, divisions only with matching populations OR explicit operator `population_aliases`; `queue_empty` fires only on proven `backlog-zero`; insufficient-evidence emits no prophecy | `test_chunks_and_jobs_are_not_summed...`, `test_starvation_never_divides...`, `test_insufficient_evidence_emits_no_idle_prophecy` |

## Bugs found and fixed in production code (not just tests)

1. **message identity omitted msg_type/correlation/epoch** — colliding ids
   let a different-typed action ride another's state (deliver returned
   "sent" for a never-authorized action).
2. **`os` missing in messaging.py** — the failpoint path crashed.
3. **population labels computed after the division** — the mismatch check
   always saw None==None and divided anyway.
4. **`reserve()` vs `check()` conflation** — strict one-time semantics
   belong to `check()`; the action boundary `reserve()` must treat a
   same-digest nonce as idempotent resume (concurrency + crash retries).
5. **crash_child/rehearsal transport mismatch** — test transports must
   match the approved destination or delivery correctly refuses.

## Test count

- negative suite: 15 (12 failed on baseline as intended; 3 regressions)
- full director suite: **173 passed / 0 failed / 0 skipped**
- repository: 1,636 passed / 1 failed (known `test_discovery_api::
  test_design_lifecycle`, pre-existing at d09d7d6e)

## PASS-condition audit of the rehearsal (review item)

Removed label-only checks and constants: K/L now asserts the specific
message row; D asserts idempotent-sent + refused-second-action; I asserts
all six outcomes + one artifact + sender state; H is a real case (was
missing); report statuses are PASS/FAIL/PARTIAL/NOT RUN (summary counts
each). Legacy weak expectations in 01–05c suites were upgraded to the
strong contract (receipts, origin transports, epoch-aware fixtures,
fresh timestamps).
