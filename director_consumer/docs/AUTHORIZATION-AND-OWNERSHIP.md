# AUTHORIZATION-AND-OWNERSHIP — scoped approvals, default-deny

## The hardening over 04

04's gate was `enabled AND nonempty authorization_ref`. §6: **a nonempty
string is never sufficient**. `AuthorizationStore` now holds durable
approval records and validates the ACTUAL request at the action boundary:

record: {auth_id, project, objective_ref, task_ref, action, recipient,
spend_scope, max_spend_usd, expires_at, issuer}

`check(auth_id, request)` rejects: unknown id (default deny), expired,
project mismatch, action mismatch, recipient mismatch, task_ref mismatch,
spend above scope, metered provider under `spend_scope=none`, and **nonce
replays** (used nonces are recorded).

## Issuing authority

`issue()` refuses director-issued records (`AuthorizationError` — the
director cannot authorize itself or future child agents). Only human
authorities appear as `issuer`.

## Ownership conjunction

Task ownership (04) + authorization (05) are separate gates: even an
acknowledged owner cannot act beyond their approval scope. Ack
attributions require sender identity — mailbox filenames and session
titles never establish either ownership or authority (encoded in tests).

## Enforcement points

- messaging `deliver()` (delivery gate + record validation)
- model router `ModelPolicy.check` (metered refusal, now also on
  child-execution paths via the agent contract's model_policy_ref)
- future dispatch/spawn paths must carry the same check

## Evidence

Tests: default-deny, all mismatch classes, expiry, replay, self-issue
refusal, metered block. Every rejection returns its reason for the audit
trail. Default posture remains deny-anything-unapproved.
