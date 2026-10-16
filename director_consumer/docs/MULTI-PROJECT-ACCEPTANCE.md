# MULTI-PROJECT-ACCEPTANCE — §10 proof

## Project A: DesignWins (real)

Real read-only feeds (fleet CHANGES, sentinel, factory events, facet
ledgers, intake log, L0 index, OpenCode metadata, git, agentmail),
real policies (`projects/designwins/`), real history (553 events → 76
assessments → 5 proposals; 3 live incidents).

## Project B: minimal independent fixture

One repo, one objective (`OBJ-B-1`), one simulated agent (`agent-b`),
one task (`TASK-1`), one policy (proj-b policy.yaml with its own
authority + model permissions), zero factory/GPU specifics. Runs the
SAME kernel modules.

## Verified (tests + live shadow)

| requirement | evidence |
|---|---|
| both coexist | one workspace DB holds both registries/tasks/reconcilers |
| identical local IDs cannot collide | `TASK-1` in both projects: independent owners (agent-b vs unassigned), epochs, histories (scoped PK migration) |
| memory/authority isolated | proj-b mail invisible to designwins (and vice versa); context packets leak-free (tests) |
| shared hardware visible without leaks | ResourceRegistry shows machines to both; project-private data never in the other's packets/findings |
| independent incidents/tasks | same-db incident test: alpha's incident never appears in beta's `open_incidents()` |
| per-project model policies | load_project(proj-b) enforces proj-b's allowlist/rates; metered refusal holds per policy |
| reconciler is designwins-free | `test_project_b_required_no_engine_change` bans project nouns from kernel modules; proj-b reconciles without code changes |

## Migration safety

Legacy 01–04 rows survive the scope migration verbatim (legacy tables
retained as provenance); migration is transactional and idempotent;
restart-safe (tested).

## If Project B had required engine changes…

it didn't — the run surfaced exactly one abstraction leak (a hardcoded
project default in the compiler), which was removed and is now guarded
by the ban test. That is the test's job: if a future project forces a
kernel change, the abstraction is wrong and gets fixed at the kernel.
