# DESIRED-STATE-RECONCILIATION — the gap engine

## Inputs

**Desired:** approved objectives (+objective_ref linkage), ready tasks,
owners, critical-path targets, verification obligations.
**Observed:** sessions, ownership claims, funnel/continuity, incidents,
messages, fleet health, now.

## Deterministic discrepancy rules

| rule | detects | proposed action |
|---|---|---|
| objective-without-task | approved objective with no linked task | create an owned task |
| task-without-active-owner | unassigned/offered work | propose assignment |
| stalled-acknowledged | ack'd/active but no progress evidence >24h | status check |
| conflicting-claims | two attributable claimants | reconciliation request |
| starving-consumer | critical-path starvation/admission gap | investigate supply path |
| repair-unverified | repair reported, recovery unverified | request recovery evidence |
| owner-host-missing | owner's host offline (migration/loss) | migration check |
| message-unacked | sent >24h without ack | bounded re-ping or expiry |

## Stability (anti-flapping)

Finding identity = hash(rule, subject). Repeat polls inside the cooldown
(6h default) are recorded as **suppressed with a why-no-action note** —
never duplicate findings, never silent. Recurring beyond cooldown reopens
with hit counts. Restart-safe: findings persist in the workspace DB.

## Output contract

Each finding: severity, evidence, proposed_action, authority,
verification_plan, expected_outcome. LLM may attach hypotheses; the
engine never executes — proposals only (05 hard boundary).

## Live evidence

Shadow run: DesignWins real state → `starving-consumer` (admission gap);
Project B fixture → `task-without-active-owner`; same-local-id tasks
reconciled independently. Re-poll → 0 new findings, cooldown notes only.
