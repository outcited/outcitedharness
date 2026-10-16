# DIRECTOR-05-PLATFORM-ARCHITECTURE — the kernel

**base:** 04 @ c5d792a9 · **this:** 05 · observe-only; live authority stays
disabled. The platform knows what should happen, measures what does
happen, explains the gap, and proposes safe actions — for ANY project.

## Kernel modules (all project-agnostic)

| module | responsibility |
|---|---|
| `projects.py` | registry: stable project_id, objectives, repos, policy version + config digest (evidence-linked), lifecycle active/paused/archived |
| `scope.py` | project-scoped identity: (project, local_id) durable keys; non-destructive one-time migration of 01–04 data |
| `context.py` | Institutional Context Compiler: deterministic decision packets with 6 evidence classes |
| `reconciler.py` | desired-vs-observed gap engine: idempotent findings, cooldowns, no-action notes |
| `agent_runtime.py` | runtime-agnostic agent identity + lifecycle (OpenCode adapter, simulated managed agents) |
| `authorization.py` | scoped approval records enforced at the action boundary; default-deny |
| `resources.py` | universal resource registry + placement RECOMMENDATIONS only |
| `critical_path.py` | generic supplier/consumer coverage contract (factory continuity = first instantiation) |
| 01–04 preserved | journal hash-chain, transactional outbox, incident correlation + recovery verification, model governance |

## Identity law (enforced by schema, not convention)

task/message/incident durability = `(project, local_id)`. Events and
history carry project columns. Cross-project reads are impossible at the
query layer. Migration is transactional, idempotent, and keeps
`*_legacy` tables as verifiable provenance (nothing deleted).

## What is data vs engine

Engine: state machines, evidence laws, reconciliation math, routers.
Data: projects/*/ (project.yaml, policy.yaml, deps.yaml, fleet.yaml,
class-costs.json), machine-aliases, feeds. DesignWins is one directory —
`test_project_b_required_no_engine_change` bans project nouns from kernel
modules.

## Operating loop

register project → ingest evidence → compile context → reconcile gap →
propose actions (authority-checked) → track recovery → remember. Repeat.
No dashboards; the journal + briefing ARE the memory and the voice.
