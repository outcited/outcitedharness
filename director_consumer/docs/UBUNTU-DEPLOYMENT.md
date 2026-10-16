# Ubuntu deployment plan — Engineering Director on Ryzen #1 (32 GB)

**goal:** the director runs permanently on the Ryzen controller with NO M5
runtime dependency. The M5 is a development workstation (see
projects/designwins/fleet.yaml migration note). Read-only mirrors only; no
competing truth stores.

## Runtime

- Ubuntu, python3.11+, `pip install pyyaml` (stdlib + pyyaml — nothing else)
- App dir: `/opt/harness-director` (rsync from the reviewed branch; pinned
  commit SHA in `VERSION`)
- Config: `/etc/harness-director/` — `feeds.yaml` (mirror paths),
  `policy.yaml`, `fleet.yaml`, `machine-aliases.yaml`, `deps.yaml`
  (per DIRECTOR-02-BOUNDARIES resolution order: project dir → defaults)
- State: `/var/lib/harness-director/` (workspace: director.db, outbox/,
  escalations/, briefings/, fleet-state.json, continuity-*.json) — this is
  the only writable location
- Feed mirrors: rsync pull every 5 min from the NAS/production sources
  (fleet-state/, sentinel/, factory.sqlite3 copy, pipeline-live.db copy,
  facet ledgers, intake log, AGENT_NETWORK.md). Mirrors are READ sources;
  staleness is measured, never masked.

## systemd units

```
harness-director.service   (Type=simple, Restart=always, RestartSec=30)
  ExecStart=/opt/harness-director/venv/bin/python -m director_consumer.cli \
    run --workspace /var/lib/harness-director \
    --deps /etc/harness-director/deps.yaml \
    --feeds /etc/harness-director/feeds.yaml \
    --project /etc/harness-director \
    --fleet-yaml /etc/harness-director/fleet.yaml \
    --agent-network /var/lib/harness-director/mirrors/AGENT_NETWORK.md \
    --snap-dir /var/lib/harness-director/mirrors/fleet-state/snapshots \
    --factory-db /var/lib/harness-director/mirrors/factory.sqlite3 \
    --pipeline-db /var/lib/harness-director/mirrors/pipeline-live.db \
    --facet-root /var/lib/harness-director/mirrors/facet-ledgers \
    --intake-log /var/lib/harness-director/mirrors/pdf-intake.log \
    --interval 600
harness-director-mirror.timer  (OnCalendar=*:0/5)
harness-director-health.timer  (hourly: cli verify + briefing freshness)
```

## Health checks

1. `cli verify` (journal hash chain) exits 0 — hourly timer + post-run.
2. Workspace heartbeat: `journal` run entries fresh < 2× interval.
3. systemd `Restart=always` + `WatchdogSec` optional (the loop is
   crash-safe by design: atomic ingest, outbox heal on startup).

## Backup / restore

- Backup: `sqlite3 director.db ".backup backup.db"` + tar outbox/ +
  escalations/ + briefings/ → daily, copied to NAS (append-only journal
  makes any consistent copy verifiable: `cli verify` on restore).
- Restore: untar workspace, run `cli verify` — chain proves integrity.

## Restart recovery

No recovery procedure needed beyond restart: cursors/dedup/journal are
transactional (crash mid-batch rolls back; outbox heals unwritten
proposals on the next run). Proven by failure injection (01A/01B tests).

## Rollback

Stop unit → restore previous workspace backup → reinstall previous `VERSION`
directory. No schema migrations beyond additive column adds; old binaries
read new columns only if present (`_migrate` is forward-additive).

## No-M5-dependency checklist

- All feed paths live in config (mirrors), never code
- No M5 hostnames in code; fleet.yaml roles are declarative data
- Model endpoints come from policy.yaml (Tailscale IPs — same on Ryzen)
- Tailscale binary path configurable; registry degrades to UNKNOWN when
  the probe fails (never fabricates state)
- Journal migration from M5 dev workspace = file copy + `cli verify`
