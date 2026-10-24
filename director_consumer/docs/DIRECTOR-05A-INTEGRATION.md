# DIRECTOR-05A-INTEGRATION — the runtime wiring

## What changed (P0: the real service now runs the platform)

`cli run` is no longer a single-project observer. One normal invocation:
ingests shared + project feeds → fleet/continuity/ownership derived feeds →
**for every active project in the registry**: builds desired state from the
registry + real TaskStore, observed state from real stores (sessions,
ownership, funnel, incidents, messages, fleet), runs the reconciler,
compiles decision packets for consequential findings, journals everything,
and writes per-project + consolidated briefings.

```
cli run (one process)
  ├─ source feeds (read-only adapters, cursors, dedup)
  ├─ derived feeds: fleet_health / factory / ownership / sessions
  ├─ ProjectRegistry (all active projects)
  │    per project: DesiredState(registry+tasks) × ObservedState(stores)
  │        → Reconciler → findings (project-scoped ids)
  │        → ContextCompiler (P0/P1) → decision packets
  │        → journal + briefings/<project>
  └─ consolidated briefing (all projects, all open findings)
```

No second scheduler, no new event bus — the existing consumer loop and
persisted state carry everything. `register-project` enrolls projects from
`projects/<id>/project.yaml` (objectives, policy version, config digest).

## Restart contract (proved by service tests)

Registration, tasks, findings, journal, and briefings live in one
workspace DB. `kill -9` mid-loop → restart resumes with identical state,
no duplicate findings/proposals; `cli verify` proves chain integrity.
Backup = workspace copy; restore continues cleanly.

## Measured (real feeds, two projects)

run: 553 events → 76 assessments (49 historical) → 5 proposals; reconcile:
designwins {objective-without-task ×2, starving-consumer}, proj-b
{objective-without-task}; 1.5s wall, 91MB RSS. Restart: 0 new anything.
