# DIRECTOR-06-GATE — first live communication (this document grants nothing)

## Verdicts (kept separate)

- **SOFTWARE ACCEPTANCE: PASS** — rehearsal 21/21 (RUNTIME-REHEARSAL.md);
  director suite 158/158; crash/concurrency/authority/verification
  invariants proven on production code paths.
- **HOST COMMISSIONING: NOT RUN** — Ryzen host checks pending
  (RYZEN-COMMISSIONING.md). The M5 is not a substitute.
- **LIVE COMMUNICATION: DISABLED** — remains disabled until software
  review, host/transport verification, recipient confirmation, and Sam's
  exact approval are all complete.

## Candidate first message (exact content, not sent)

- project: designwins · task: SUPPLY-RECON-01 · incident:
  factory-supply-exhaustion · epoch: current at send time
- recipient: accountable owner of record (RESOLVE FIRST — may no longer
  be agent:m5-opencode on M5)
- body: "Factory supply is at risk. Investigate why the executable
  extraction queue is empty despite pending rescan inventory. Confirm
  ownership, identify the cause, and return evidence."
- envelope binds: body digest, subject, msg_type=question, transport
  (real NAS inbox), expiry 7 days, max_actions=1, spend_scope=none

## Required approval scope (Sam)

One operator artifact (uid-owned, 0640) carrying the complete envelope
above. Nothing less authenticates this send.

## Remaining gates

1. Independent review of this commit.
2. Host commissioning on the actual Ryzen (uid separation, systemd, NAS
   identity, OpenCode binding).
3. Owner re-resolution + recipient confirmation.
4. Sam's artifact + explicit delivery enablement for that one action.

## Product direction (not in scope here)

The one-message limit is commissioning policy. Future: approved
project-level delegations, coordination of existing agents, bounded
spawning — the envelopes/leases/receipts built here are designed to be
that substrate. Not implemented in 05C.
