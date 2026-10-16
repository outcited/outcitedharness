# OPENCODE-OWNERSHIP — sessions, task identity, conflicts, future contract

## Session discovery (read-only)

Sources: `~/.local/share/opencode/opencode.db` (session/workspace/todo —
the OpenCode CLI's own sqlite), process table via pgrep (health only).
Shadow run found 84 sessions on M5, including subagent sessions
(`parent_id` set → classified `managed`). Laws enforced by tests:

- a terminal tab is not an agent identity (title never becomes agent)
- a disconnected terminal is not proof of task completion
  (`session_disconnected` says exactly that)
- task ownership is UNKNOWN unless claim evidence is attributable

## Task identity and ownership reconciliation

Task IDs (`NAME-NN`-style, e.g. `FACTORY-ORCHESTRATION-03`,
`EVIDENCE-SUPPLY-04`) are discovered from commit subjects, branch names,
session titles/todos, and mailbox filenames. Shadow run reconciled **17
real task IDs**; all resolved to `unknown` ownership because the repo's
commit history carries task IDs without per-agent attribution — the
correct honest result (the reconciler refuses to guess from mail or
titles).

States produced: `confirmed-owner | delegation | conflicting | stale |
unknown`, each with a recommendation and (for conflict/stale) a proposed
message draft. No live leases. No reassignment.

## The EVIDENCE-SUPPLY-04 lesson, encoded

The incident was multiple agents treating a machine nickname / mailbox
message as ownership. The reconciler now encodes: mailbox = hint (weight 1,
never sufficient), claim = weight 2 and must carry an attributable agent,
two claimants = conflict resolved by asking, never by the director
reassigning.

## Future managed-agent execution contract (documented, NOT enabled)

```
task_id: str                     # stable, project-scoped
project_id: str
parent_objective: str            # approved objective reference
accountable_owner: str           # human or named agent (never "unknown")
execution:
  agent_id: str                  # spawned agent identity
  session_id: str                # OpenCode session
  host: str                      # target machine (fleet.yaml id)
  worktree: str                  # isolated worktree, never a shared branch
permissions:
  allowed_tools: [...]
  budget: {model_policy_ref, max_calls, max_spend_usd: 0}
  spend_authorization_ref: str   # human-signed; empty = metered refused
ownership:
  lease_id: str
  epoch: int                     # fencing token; stale epoch = revocable
  heartbeat_s: int
verification:
  acceptance_gates: [...]        # pytest / gold / parity refs
  required_evidence: [...]       # receipts before done
recovery:
  handoff_on_exit: mailbox_message_template
  orphan_policy: "park + escalate to accountable_owner"
```

Spawning remains disabled (DIRECTOR-03 hard boundary). This contract is
the interface future work must satisfy before any director-managed agent
exists.
