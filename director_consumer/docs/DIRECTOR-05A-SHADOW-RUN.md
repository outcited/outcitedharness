# DIRECTOR-05A-SHADOW-RUN — one process, two projects, real feeds

## Setup

Workspace `/tmp/d05a-shadow`; projects registered via the real
`register-project` entrypoint: `designwins` (projects/designwins with
objectives SUPPLY-CONTINUITY + EXTRACTION-COVERAGE) and `proj-b`
(independent fixture: one repo, one objective, own policy). Real
read-only feeds (fleet CHANGES, sentinel, ra03 factory events, facet
ledgers, intake log, L0 index, sessions, git, agentmail). `--no-llm`.

## Results (actual CLI output)

RUN1: 553 events → 76 assessments (49 historical suppressed) → 5
proposals. Reconciliation:
- designwins: `objective-without-task` ×2 (approved objectives with no
  linked tasks — nobody reported this; the reconciler found it) +
  `starving-consumer` (real admission gap) → 1 decision packet
- proj-b: `objective-without-task` ×1 — and **no starvation leak** after
  the critical-path declaration fix (the leak was caught in the first
  shadow pass and corrected before submission)

RUN2 (restart): 0 new findings (3 + 1 suppressed with reasons), 0 new
proposals, chain verifies. Consolidated briefing lists both projects and
all open findings.

## Measured

1.5 s full loop · 91 MB RSS · registrations survive restart with config
digests · identical local `TASK-1` independence (service test) · kill -9
interrupt/resume (service test) · backup/restore (service test).

## What the director understood without being asked

1. Two approved objectives have no execution plan.
2. Executable queues are empty while eligible inventory exists
   (admission gap ≠ supply shortage).
3. Project B's quiet objective still needs an owned task.
4. Which facts are receipts vs assertions vs hypotheses vs superseded.
