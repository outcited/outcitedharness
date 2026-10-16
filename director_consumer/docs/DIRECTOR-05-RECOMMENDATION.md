# DIRECTOR-05 recommendation — minimum permissions for the first live
# coordination experiment

## The one experiment worth authorizing first

**Live coordination of a single recovery loop, read-mostly:** take the
real `factory-supply-exhaustion` incident and let the director send ONE
message through the real NAS inbox to `agent:m5-opencode` (the
investigation request already simulation-proven), accept the ack, and
track the repair to `recovery_verified` from measured queue depth.
Nothing else.

## Minimum permissions to grant

1. **Delivery:** `delivery.enabled: true` + a task-scoped
   `authorization_ref` naming this single experiment (expires in 7 days).
   No blanket messaging.
2. **One recipient:** `agent:m5-opencode` mailbox only; all other
   recipients stay refused by config.
3. **No new write powers:** tasks/incidents/messages stay in director.db;
   the director still cannot touch production queues, crawler configs,
   releases, or spend.
4. **Model policy unchanged** — metered still refused; the experiment is
   deterministic-path first.

## What stays disabled

Spawning, reassignment authority, production queue writes, destructive
cleanup, spend, release state, and any delivery beyond the single
authorized request-thread.

## Success criteria for the gate

- one real message delivered, one real ack, one repair tracked to
  `recovery_verified` with independent evidence
- zero duplicate messages across restarts
- zero messages to unauthorized recipients
- Sam intervenes only for the initial authorization

If the loop holds for a week, DIRECTOR-05 can propose widening to
"investigation requests to lane owners for P1 incidents" — still
approval-gated per send, still never reassigning work autonomously.
