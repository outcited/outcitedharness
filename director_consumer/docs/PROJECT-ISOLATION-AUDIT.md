# PROJECT-ISOLATION-AUDIT — identity audit (§3)

## Audit results

| store | identity | status |
|---|---|---|
| project registry | (project_id) PK + events | ok |
| source cursors / seen_events | SHARED BY DESIGN | documented (physical feed events are fleet-global) |
| tasks / task_events / contributors | (project, task_id) + project cols | migrated, tested |
| messages / message_events | (project, message_id) | migrated, tested |
| incidents / recovery / events | (project, incident_id) | migrated, tested |
| proposals | (project, trigger_key) | fixed in 05A |
| reconciler findings | (project, finding_id) — finding_id folds project | fixed in 05A |
| authorizations / nonces | (project, auth_id), nonce rows project-tagged | fixed in 05A |
| model usage / journal | shared audit ledgers (project attribution column) | by design |
| context retrieval | project-rooted + registry/task/DB filters | leak-tested |
| ownership/recovery evidence | project columns on child tables | migrated |

## Migration safety

`scope.py` detects per-table by **PK shape** (04-era tables carry a project
column but single-PK — column-presence detection was wrong and is fixed).
Rebuilds are transactional; originals retained as `*_legacy`; idempotent;
restart-safe. Genuine older-state fixture test preserves every row.

## Shared vs private

Physical machines/fleet telemetry stay shared (one registry, not copied).
Project-private: tasks, messages, incidents, findings, authority,
packets. Critical-path state is declared per project — a shared funnel
observation never leaks into a project that did not declare a critical
path (shadow-caught leak, fixed, re-verified).
