# TASK-OWNERSHIP-CONTRACT — states, evidence, fencing

## Identity model

`task_id` (stable, project-scoped) · `correlation_id` (incident link) ·
`epoch` (assignment fencing token). Machine ≠ agent ≠ session ≠ workload
≠ accountable owner — all five are separate columns and survive each
other's migration (tested: owner moves m5→ryzen-2, identity holds).

## States and legal transitions

```
unassigned → offered → acknowledged → active → submitted → verified → closed
                 ↑          │            │         │  (failed verif → active)
                 │          ▼            ▼         ▼
                 └──── blocked / stale / conflicted / cancelled
verified/closed → reopened; closed → detected (recurrence)
```

Transitions carry mandatory `evidence` + `actor`; `verified` stamps
`last_verified_progress`. Illegal moves raise `TransitionError`; stale-epoch
moves raise `FencingError` (re-offer bumps the epoch — a late holder cannot
reclaim).

## Assignment authority and acknowledgment

- `offered` requires an authority reference (director:incident,
  project-authority, human). Owner is set at offer time (epoch +1).
- `acknowledged` requires an ack artifact (message id / receipt). Delivery
  of an offer is NOT acknowledgment.
- UNKNOWN ownership is never resolved by guessing: `reconcile_request()`
  produces a PROPOSED request to the project authority. The 17 historical
  DIRECTOR-03 tasks remain unowned — no retroactive invention.

## Contributors vs owner

`task_contributors` records multiple helpers; exactly one
`accountable_owner` at a time. Conflict (two claim evidence) → state
`conflicted` + reconciliation request, never an automatic reassignment.

## Future managed-agent conformance

The execution contract from 03 (task/project ids, owner, session, host,
worktree, permissions+spend ref, lease/epoch, acceptance gates, recovery)
maps 1:1 onto these tables; spawning remains disabled. A child agent's
model calls must pass `ModelPolicy.check` — metered refusal applies to
execution paths, not just the director's own calls (tested).
