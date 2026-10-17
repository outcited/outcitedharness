# CROSS-PATH-AUDIT — operation → entry points → shared validator → evidence

## The three invariants and their validators

| invariant | shared validator | enforced by |
|---|---|---|
| A. only the exact approved action executes | `AuthorizationStore.reserve()` (grant→action binding) + `deliver()` envelope persistence + `still_eligible()` pre-effect gate | every send path |
| B. only the authenticated agent/session accepts | `acceptance.validate_acceptance()` | every ack path |
| C. only successful, source-supported verification closes | `IncidentStore.validate_receipt()` (+ `establish_cause()` for causality) | every resolve/close path |

## Operation audit

| operation | entry points | shared validator | regression |
|---|---|---|---|
| authorize+send | `MailboxDelivery.deliver()` (normal, retry/resume) | `AuthorizationStore.reserve` + `still_eligible`; external write from PERSISTED envelope (body+destination) | `test_one_grant_cannot_fund_second_explicit_action_id`, `test_modified_message_cannot_change_reserved_output`, `test_changed_destination_config_cannot_redirect_reserved_work`, `test_revoked_expired_unsent_actions_stop_visibly`, `test_resume_after_crash_never_consumes_another_approval` |
| acknowledge | `record_ack()` | `validate_acceptance` | `test_wrong_task_epoch_session_digest_project_refused`, `test_invalid_receipt_after_valid_ack_leaves_state_unchanged` |
| mailbox recovery | `reconcile_mailbox()` | `record_ack` → `validate_acceptance` (scoped envelope required) | 05b crash/recover tests |
| receiver reconciliation | `reconcile_with_receiver()` | `validate_acceptance` (scoped receipts only; bare ids rejected) | `test_bare_receiver_id_cannot_acknowledge`, `test_genuine_scoped_acceptance_recovers_lost_ack` |
| incident resolve | `IncidentStore.resolve()` | `validate_receipt` (failure results rejected; sources must resolve) | `test_wellformed_failure_result_cannot_close`, `test_unresolvable_source_cannot_verify` |
| cause conclusion | `IncidentStore.establish_cause()` | `validate_receipt` + explicit causal receipt (kind=root-cause) | `test_generic_observation_never_becomes_cause` |
| recovery_verified | `lifecycle_transition()` | current-occurrence validated receipts (not kind strings) | `test_recurrence_rejects_old...`, 04 receipt tests |
| task verified/closed | `TaskStore.transition()` | evidence-mandatory + legal transitions; investigation≠production (incident untouched) | `test_investigation_still_broken_does_not_close_production` |
| grant mint | `AuthorizationStore.issue()` | operator file artifact (uid/mode/content), director cannot self-issue | 05B authority tests |
| model inference | `ModelPolicy.check` | allowlist + metered refusal + usage ledger | 05/05c tests |

No public path bypasses its validator; convenience methods route through
the same contracts (resolve() has no evidence-free branch; record_ack is
the only ack writer and it validates; deliver() is the only external
effect and it reserves atomically).

## Adapter honesty (§6)

- **Production adapter:** `OpenCodeSessionTransport` — resolves origin from
  an operator-protected sessions binding + local session existence.
  SOFTWARE GAP (named, not hidden): the installed `opencode.db` schema
  carries no signed session tokens, so cryptographic origin binding needs
  an OpenCode-side receipt; until then the binding file is the strongest
  available proof and is labeled as such.
- **Test adapter:** `SessionTokenTransport` (path→token map) — explicitly a
  test double, never presented as live origin proof.
- **Host adapter:** `UidTransport` (uid custody only) — insufficient alone
  for session identity; kept for host-level checks.
- **Service wiring:** the CLI constructs the same stores/validators the
  acceptance tests use; operator identity comes from protected
  `policy.yaml` (`delivery.operator_uid`); missing config fails closed;
  service identity is the real process uid.
