# PROJECT-REGISTRY-CONTRACT — registration, isolation, lifecycle

## Registration

`ProjectSpec`: stable `project_id`, name, description, approved
objectives+success criteria, repositories, policy version, config digest
(sha over the project's config files = versioned + evidence-linked),
human approval authority, lifecycle. `load_spec_from_config()`
reads `projects/<id>/project.yaml`.

## Lifecycle

`active | paused | archived` — every change is a registry event
(append-only). Pausing changes nothing about the engine; archiving keeps
all history readable. Invalid states raise.

## Durable identity (the §2 fix)

| store | durable key | migration |
|---|---|---|
| tasks | (project, task_id) | 04 rows preserved to `tasks_legacy` + copied |
| messages | (project, message_id) | ditto |
| incidents | (project, incident_id) | ditto |
| child history tables | project column added | ALTER, default 'default' |

`schema_meta.project_scope=1` guards a single transactional rebuild —
restart-safe, idempotent, zero data loss (verified by test with legacy
rows). Two projects can use `TASK-1` simultaneously with independent
owners, epochs, and histories (verified live in the shadow run).

## Cross-project isolation (audited)

- tasks/messages/incidents: composite keys + project-filtered queries
- decisions/context packets: project-rooted sources; cross-project leak
  test
- authorization: `check()` requires project match (§6)
- model policies: per-project policy.yaml; usage rows carry project
- evidence references: sources cited with project paths; kernel modules
  contain no project nouns (abstraction test)
