# DIRECTOR-05B-AUTHORITY-REVIEW — the trust boundary (P0-1/P0-2)

## P0-1: legacy bypass removed

`MailboxDelivery.deliver()` now REQUIRES the AuthorizationStore. A
`DeliveryPolicy` flag plus a nonempty string is never approval — without
the store, delivery raises (fail closed) and the refusal is journaled.
The running director (`DirectorConsumer`) constructs the same
AuthorizationStore the security suite tests. Isolated mailbox simulation
remains for tests only.

## P0-2: operator trust boundary

The approval artifact is an operator-owned FILE whose content defines the
complete approved scope (project, action, recipient/task/objective,
expiry, spend scope, issuer, immutable payload digest). Enforcement:

- `operator_uid != service_uid` — identical identities = no trust
  boundary = refuse (the director cannot run both sides)
- artifact owner must be the operator; approvals dir and file must not
  be group/world writable
- auth_id is a strict token; open with `O_NOFOLLOW`; realpath
  containment — symlinks and path traversal refused (tested)
- empty `{}` or partial files are NOT human approval (tested)
- `issuer: sam` in the file is only meaningful because the OPERATOR owns
  the file — the DB row is a validated cache; the file is authority and
  is re-read on every check
- revocation = file deletion or the revoked flag (tested)

No invented cryptography: unix ownership/permissions are the established
mechanism for a single-owner self-hosted host. Grant/revoke lifecycle is
file operations by the operator; the service account never writes there.

## Scope validation

Missing required scope fields fail closed per action class (deliver =
project+recipient+task_ref). Identity mismatches report their specific
reason. One-message approvals are digest-bound: reusing an approval for a
different body is refused (tested).
