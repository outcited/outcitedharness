# RYZEN-COMMISSIONING — prepared, NOT deployed (host gates NOT RUN)

## Release candidate (observation-only)

- App: this commit, pinned in `VERSION`; `python3.11` + `pyyaml` only.
- Identity: dedicated non-root service user `harness-director`; operator
  identity configured separately in `/etc/harness-director/policy.yaml`
  (`delivery.operator_uid`); approvals at
  `/etc/harness-director/approvals/` (operator-owned, 0640 files; the
  service account cannot write — enforce at the host gate).
- State: `/var/lib/harness-director/` (local disk — NEVER a live sqlite
  DB on the NAS).
- Outbound: **disabled by default** (`delivery.enabled: false`;
  `require_existing_root: true` for the NAS transport).
- Supervision: systemd `harness-director.service` (Restart=always,
  RestartSec=30) + health timer (`cli verify` + briefing freshness).
- Backup: `sqlite3 director.db '.backup ...'` snapshots + workspace tar;
  **restore test = integrity + referential + pending actions + project
  attribution + receiver-receipt reconciliation** — not just chain
  verify. Restored outbox stays delivery-disabled until reconciliation
  shows no resend of previously accepted work (receiver receipt history
  is newer than the sender snapshot by design).
- Freshness: mirrors carry ORIGINAL observation time; a fresh copy never
  refreshes an old observation. M5 disconnection leaves cached state +
  explicit stale markers; any feed that still depends on M5 is listed
  below as an unresolved runtime dependency.

## Host checks — prepared, NOT RUN

| check | method | status |
|---|---|---|
| real UID separation + permission enforcement | run service/operator as distinct uids; attempt artifact writes as service | NOT RUN |
| systemd restart behavior | kill -9 under systemd; state recovery | NOT RUN |
| NAS mount/transport identity | real mount, require_existing_root honored | NOT RUN |
| OpenCode version + session binding | inspect installed schema; bind ack envelopes | NOT RUN |
| model endpoint policy | config-driven allowlist smoke | NOT RUN |
| backup/restore | populated-workspace restore + receiver reconciliation | NOT RUN |
| source freshness during M5 disconnection | stop mirror; confirm stale markers | NOT RUN |

**Unresolved M5 runtime dependencies** (for now): fleet-state + sentinel
feeds, OpenCode metadata mirror, shared-docs-queue git. A cached mirror is
NOT proof the producer is alive.

## Rollback

Stop service → restore previous workspace snapshot → reinstall previous
VERSION dir. All migrations are additive/audited; `*_legacy` tables keep
history. One active controller identity; handover = stop old, verify
chain + pending actions, start new.
