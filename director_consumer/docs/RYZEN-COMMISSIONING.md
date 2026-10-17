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

## Pinned observation-only candidate (closure update)

- **Commit:** `director/05c-runtime-acceptance` @ (see final SHA) — version
  pinned in `VERSION`; deps: Python 3.11.15, pyyaml 6.0.3 (no others).
- **Service account:** dedicated non-root `harness-director`.
- **Operator authority:** `delivery.operator_uid` in
  `/etc/harness-director/policy.yaml` (protected); approvals at
  `/etc/harness-director/approvals/` (operator-owned, 0640; service
  account cannot write). Missing operator config = fail closed.
- **State:** `/var/lib/harness-director/` (local disk only).
- **Outbound:** disabled by default; `require_existing_root: true` for any
  real transport; delivery gate = AuthorizationStore (no flag/string path).
- **systemd:** `harness-director.service` (Restart=always) + health timer
  (`cli verify` chain + briefing freshness).
- **Backup/restore:** sqlite `.backup` snapshots + workspace tar; restore
  verified against receiver receipts (no resends of accepted work);
  delivery stays disabled until reconciliation passes.
- **Handover:** one active controller; stop old → verify chain + pending
  actions + receiver ledger → start new.

## M5-dependent producers (explicit; cached mirrors ≠ survival)

| producer | feed | if M5 sleeps |
|---|---|---|
| fleet_state (launchd on M5) | fleet-state/CHANGES.md + snapshots | stale markers, fleet UNKNOWN |
| sentinel (launchd on M5) | ALERTS.md + LKG | watcher-stale incident (rule live) |
| extract_dispatch (M5) | pipeline-live.db mirror | substrate queue view freezes |
| facet lanes (M5 orchestrator; MBP worker) | done-*.txt ledgers | consumption rate goes UNKNOWN |
| OpenCode metadata (M5) | sessions mirror | ownership/session views stale |
| shared-docs-queue (M5 repo) | agentmail git | mail history frozen |

Cached evidence does NOT prove these keep running — each goes explicitly
UNKNOWN/stale, never silently healthy.

## Executable host checks (run ON the Ryzen at commissioning)

```
# 1 uid separation + approvals perms
id harness-director; sudo -u harness-director touch \
  /etc/harness-director/approvals/probe.json   # must FAIL
# 2 systemd restart
sudo systemctl kill -s KILL harness-director; systemctl status harness-director
# 3 NAS identity
mount | grep <nas>; test -d /mnt/evidence && echo mounted
# 4 OpenCode binding
python3 -c "import sqlite3;sqlite3.connect('/mirror/opencode.db')"
# 5 model policy
curl -sS <allowed-endpoint>/v1/models >/dev/null && echo ok
# 6 restore + receiver reconciliation
./scripts/restore-verify.sh <snapshot> <receiver-ledger>
# 7 M5 disconnection freshness
systemctl stop harness-director-mirror.timer; ./cli verify --workspace ...
```
