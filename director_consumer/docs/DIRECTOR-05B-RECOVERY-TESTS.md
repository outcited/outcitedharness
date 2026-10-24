# DIRECTOR-05B-RECOVERY-TESTS — delivery crash contract (P0-3/P0-4)

## The delivery contract

1. Authorization reservation (nonce = message id) happens inside
   `check()` — one-time; ANY re-check is a replay refusal.
2. Durable action intent (`state='delivering'`) commits BEFORE the file
   write (single transaction with attempts).
3. Physical artifact filename is STABLE — derived from persisted
   `created_at` + message_id — independent of retry time.
4. `sent` recorded after the atomic write.

**Retry vs re-authorization:** a retry of an already-authorized action
(`state='delivering'`) completes the write WITHOUT re-entering `check()`.
A new action must authorize fresh. Concurrency: independent connections
racing on one message converge on one physical file (atomic replace);
tested with 6 threads on separate connections.

## Crash-boundary matrix (all tested)

| crash point | recovery | duplicates |
|---|---|---|
| after nonce, before intent | refused re-check (nonce burned); operator re-issues | 0 files |
| after intent, before write | retry completes the write | 1 file |
| after write, before sent | retry lands on the SAME stable filename | 1 file |
| concurrent N-way attempts | serialized intent; one artifact | 1 file |

## Ack origin (P0-4)

A `From:` header is never sender proof. `record_ack` requires a trusted
transport: the ack artifact's OS uid must match the agent's registered
uid (`UidTransport`, established unix mechanism). Forged origin →
`ack-unverified` (evidence to investigate). Verified origin → `acked`.
Untrusted agent text never becomes ownership or acceptance.
