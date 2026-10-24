# DIRECTOR-06-FINAL-GATE — first live communication, GO/NO-GO

## What 05B closed

- legacy delivery bypass: GONE (store required; fail closed)
- operator trust: file-backed full-scope artifacts, uid-separated,
  symlink/traversal-proof, empty-file rejected, revocable
- delivery recovery: crash matrix proven (no loss, no duplicates),
  stable filenames, concurrent-safe, retry≠re-authorization
- ack origin: trusted-transport only (uid custody), forgeries stay
  unverified — consistent with the org's existing UIDTransport/orgmail
  uuid-custody model
- project closure: identity preserved through migration (audited),
  incident IDs project-scoped, per-project observed state, receipts-only
  verification, visibility declared

## Verified service boundary (not just units)

The actual CLI runs two projects through one workspace with registry,
reconciliation, packets, briefings; restart/kill/backup are clean
(05A service tests). 05B adds the adversarial authority + crash matrix
against the same MailboxDelivery/AuthorizationStore the service wires.

## Verdict: GO for the first live communication — one thread

Conditions (all software-side met; human steps in caps):

1. SAM writes one real approval artifact (uid-owned, 0640) binding
   project=designwins, task=SUPPLY-RECON-01, recipient=agent:m5-opencode,
   the exact message digest, 7-day expiry, spend_scope=none.
2. SAM (or registry) confirms the accountable owner — the agent may no
   longer be on M5; the message must go to the owner of record.
3. Flip delivery ONLY through that artifact; no config flag alone.

Success = exactly one delivered message, one transport-verified ack, one
evidence-verified recovery, zero duplicates across restarts, zero
unauthorized attempts in the audit trail. Anything else stays closed.
