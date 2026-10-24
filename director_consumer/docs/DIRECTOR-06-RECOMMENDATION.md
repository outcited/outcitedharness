# DIRECTOR-06 recommendation — first live, bounded coordination

## The experiment (one thread, one week)

Take the live DesignWins `factory-supply-exhaustion` incident and let the
director run ONE real coordination loop end-to-end:

1. Issue a **scoped, human-signed authorization** (sam): action=deliver,
   project=designwins, task=SUPPLY-RECON-01, recipient=agent:m5-opencode,
   spend_scope=none, expires in 7 days, single nonce per send.
2. Deliver the investigation request through the real NAS inbox
   (message grammar already in production use).
3. Accept the attributable ack; track the repair via incident lifecycle.
4. Close only on independent `root-cause-fixed` evidence (measured queue
   depth after admission).

## Minimum permission set

- delivery enabled for exactly one recipient/thread (AuthorizationStore
  record above — not a config flag alone)
- read everything else as today
- NO spawning, NO reassignment authority, NO spend, NO queue writes,
  NO release/promotion, NO second thread

## Success gate (then, and only then, widen)

one delivered + acked + verified-recovered thread, zero duplicate
messages across restarts, zero unauthorized attempts (audit shows all
refusals), Sam touches only the initial signature. Next widening step:
P1 investigation requests to lane owners, still per-send scoped
authorization.

## Why this is the right first step

It exercises every kernel law once under real conditions — scoped
authority, delivery/ack separation, ownership, incident recovery
verification, journal durability — with blast radius = one message.
