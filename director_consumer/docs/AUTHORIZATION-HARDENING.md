# AUTHORIZATION-HARDENING — verified approvals (§4)

## Trust model (no invented cryptography)

Operator approval = an `approvals/<auth_id>.json` file that is
**operator-uid-owned and not group/world-writable**, checked on every
issue/check (unix permissions = the established mechanism for a
single-owner self-hosted box). A declared `issuer` string is never proof.
Grant: write the file (chmod 0640). Revoke: delete the file or flip the
`revoked` flag. Survives Ryzen migration: the approvals directory is part
of the config bundle; ownership is re-established by the operator on the
new host (documented in RYZEN-DEPLOYMENT-READINESS.md).

## One action boundary

`deliver()` validates through `AuthorizationStore`: project, action,
recipient, task scope (message correlation_id), objective scope, expiry,
spend scope, **payload digest binding** (one-message approvals cannot be
reused for other bodies), and one-shot nonces. Missing required scope
fields fail closed; identity mismatches report their specific reason.

## Crash safety

Durable action intent (`state='delivering'`) commits BEFORE the file
write; crashes resume (`delivering` is retryable, file write idempotent);
nonce = message id (one action id, never two consumptions); duplicate
sends return the terminal state. Reconcile heals commit-vs-write gaps.

## Acknowledgment attribution

`record_ack(verified_sender=...)`: only a sender matching the intended
recipient counts as acceptance. Unattributed mailbox text is stored as
`ack-unverified` — evidence to investigate, never acceptance. Recovery
scans attribute acks only when the file names a matching sender.

## Adversarial evidence

tests: digest-binding refusal, permission enforcement, revocation,
project/action/recipient/task mismatch, expiry, replay, self-issue
refusal, metered scope block, forged acks.
