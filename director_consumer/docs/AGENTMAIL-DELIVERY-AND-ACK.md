# AGENTMAIL-DELIVERY-AND-ACK — controlled delivery, receipts, reconciliation

## Authorization gate (hard boundary)

Delivery requires BOTH `delivery.enabled: true` AND a non-empty
`delivery.authorization_ref` in project policy — default is disabled, so
every production path ships as proposed-only until separately approved.
Without the gate, `deliver()` raises `DeliveryDisabled` and the attempt is
audited. A director can never grant itself the reference.

## Message lifecycle

`proposed → sent → acked`, with `expired` (TTL passed, no ack → escalation
draft) and `escalated` (attempts ≥ 5). Duplicate sends are idempotent by
`message_id` (same file, same row, attempts counter honest). Filenames use
the existing grammar
`<ts>_<from>_<to>_<type>_<subject>.md` in the recipient's inbox directory.

## The three laws (tested as separate states)

1. **Delivery ≠ acknowledgment** — `sent` rows have `acked_at NULL` until
   an explicit ack artifact arrives (`ack_of:` header / `record_ack`).
2. **Acknowledgment ≠ completion** — task completion lives in `tasks`
   (`submitted`/`verified`), driven by evidence, not by mail.
3. **A finished session ≠ verified work** — verification is a task
   transition with evidence; sessions end silently all the time.

## Reconciliation and resilience

- crash between commit and delivery: `reconcile_mailbox()` compares the
  mailbox against the table and heals state (idempotent, tested)
- out-of-band acks discovered by scanning for `ack_of:` headers
- bounded retries (5) then `escalated` — escalation drafts are produced by
  the caller, never sent automatically
- full audit trail in `message_events` (proposed/sent/acked/escalated/
  expired/recovered/delivery_refused)

## The coordination request shape (from the mission)

> "Factory supply is at risk. Investigate why the executable extraction
> queue is empty despite pending rescan inventory. Confirm ownership,
> identify the cause, and return evidence."

This exact message was delivered in the shadow simulation (isolated
mailbox, test authorization) and tracked to ack + completion without any
human relaying.

## Test discipline

All delivery tests use isolated mailboxes (`tmp_path/boxes`); no
production mailbox is ever a test fixture. Production delivery remains
disabled in `projects/designwins/policy.yaml`.
