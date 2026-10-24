# AGENT-RUNTIME-CONTRACT — identity, adapters, lifecycle

## Identity model (runtime-agnostic)

`AgentIdentity{agent_id, runtime, host, mode, session_id, project_id,
task_id, capabilities, ownership_epoch, heartbeat_ts, progress_evidence,
allowed_tools, model_policy_ref, budget, worktree, repo, recovery_state}`

Laws: a terminal tab is not an agent identity; a session title is not
authority; a disconnected terminal is not completion; machine migration
does not change agent identity.

## Adapters

- **OpenCodeAdapter** (first runtime, read-only): sessions from
  `opencode.db` + declared registry. Mode classification: `managed` for
  subagents (parent_id) and declared workers; `interactive` for
  human-interactive declarations — root unknown sessions stay `unknown`
  and are never touched. Interactive sessions on M4/M5/mac-mini are
  preserved by construction (read-only adapter, heartbeat raises).
- **SimulatedAgent** (conformance model for future managed execution):
  acknowledge → progress → submit → verify_or_fail → disconnect with full
  event history. This is the lifecycle a future spawned agent must
  satisfy; spawning is DISABLED platform-wide in 05.

## Execution contract (future spawn path)

The 03 contract (task/project ids, owner, session, host, worktree,
permissions + spend authorization ref, lease/epoch fencing, acceptance
gates, handoff/recovery) is realized structurally: TaskStore carries the
assignment/epoch columns, AuthorizationStore gates the actions, and
SimulatedAgent proves the lifecycle. Enabling spawning later is a policy
switch + runner, not a redesign.

## Recovery semantics

`recovery_state`: verified | failed-verification | disconnected |
submitted | unknown. Only `verified` with evidence counts as completion —
the task store's `verified` transition is the authoritative gate.
