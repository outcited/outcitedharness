# RYZEN-DEPLOYMENT-READINESS — permanent controller package (§12)

## Package (prepared, NOT executed)

Ubuntu Server + Tailscale + python3.11 + pyyaml; version-pinned app at
`/opt/harness-director` (commit SHA in VERSION); persistent workspace
`/var/lib/harness-director` (director.db + briefings + outbox + caches);
config `/etc/harness-director` (projects/*/ + feeds + aliases);
read-only feed mirrors (rsync timer) — **SQLite mirrors use
`sqlite3 source '.backup dst'` / `VACUUM INTO`, never raw cp of a WAL
DB**; systemd `harness-director.service` (Restart=always) + mirror/health
timers; hourly `cli verify`; daily workspace backup to NAS with local
fallback (NAS absence = cached state + stale markers, never failure).

## Restart / failure recovery (service-tested)

kill -9 → auto-restart → registry, cursors, tasks, ownership, incidents,
findings, drafts, approvals all resume; no duplicate actions; stale feeds
marked UNKNOWN. Backup/restore = workspace copy + chain verify.

## Measurements (real feeds, two projects, 1.5s full loop)

| metric | measured | 32GB target |
|---|---|---|
| loop wall | 1.5 s | far under 600s interval |
| RSS | 91 MB | fine |
| startup | < 1 s | fine |
| DB growth | ~1–5 MB/day (journal + findings) | years of headroom |

No RAM upgrade indicated by evidence. The controller stays usable with M5
asleep/disconnected: all feeds are mirrors; no interactive OpenCode tab is
required; model endpoints are network resources, not M5 resources.

## Operator authority migration

The approvals directory moves with the config bundle; the operator
re-establishes file ownership on the new host (chmod 0640, operator uid).
Until re-established, all authorization checks fail closed.
