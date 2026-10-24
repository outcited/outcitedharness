# DIRECTOR-03-ARCHITECTURE — integration and ownership model

**base:** `director/02-continuous-intelligence` @ 47ff2343 · observe-only.

## What was added (all generic core; DesignWins stays config-only)

| module | role |
|---|---|
| `incidents.py` | incident correlation: deterministic symptom→incident grouping, suspected-vs-verified cause law, reopen history |
| `ownership.py` | task-ownership reconciler: evidence tiers, conflict/stale/unknown states, recommendations only |
| `sessions.py` | OpenCode session discovery (read-only sqlite + process health); titles never become identities |
| `continuity.py` + | class/page-aware backlog cost, starvation basis (measured / backlog-zero / unknown-telemetry / queues-unavailable) |
| `fleet.py` + | snapshot freshness → UNKNOWN-not-healthy, self-identity (no localhost dupes), alias map, migration states |
| `deps.yaml` + | `correlations:` groups + ownership/session rules (project data) |

## Integration model

```
source feeds (read-only) ──► adapters ──► classify (deps.yaml rules)
                                             │
              ┌──────────────────────────────┤
              ▼                              ▼
        incidents.observe()          proposals (mail/escalation drafts)
        (correlation groups)                 │
              ▼                              ▼
        director journal  ◄──────── transactional outbox (heals on restart)
              ▲
   ownership reconciler + session discovery + fleet registry + continuity
        (derived feeds: ownership, sessions, fleet_health, factory)
```

## Ownership model (the organization law)

Machine ≠ agent ≠ session ≠ workload ≠ accountable owner. Evidence tiers:

1. **claim** (commit-with-task-id / branch / session todo) — can establish
   ownership only when attributable to a *named* agent
2. **hint** (mailbox message) — never proof
3. two named claimants → `conflicting` (propose settlement message)
4. quiet beyond stale window → `stale` (propose status check, never reassign)
5. anything else → `unknown` (propose asking, never assigning)

An "unknown" agent never establishes ownership — ambiguity resolves to
UNKNOWN, exactly per §6.

## What remains config vs engine

Engine: ingestion, journal, incidents, ownership mechanics, sessions scan,
fleet derivation, continuity math, router governance, briefing.
Config (`projects/designwins/`): deps.yaml rules+correlations, policy.yaml
(authority + models), fleet.yaml (roles/migrations/thresholds),
machine-aliases.yaml, class-costs.json, feeds paths.
