# REMAINING-HOST-CHECKS + SOFTWARE GAPS (post closure)

## SOFTWARE gaps (must be built, cannot be host-tested away)

1. **OpenCode session-origin receipts** — `opencode.db` (schema inspected)
   has no signed session tokens; `OpenCodeSessionTransport` is the interim
   binding-file adapter. A true origin proof needs an OpenCode-side
   authenticated receipt. This is a software gap, NOT a host check.
2. **Production receiver** — reconciliation is proven against the
   simulated receiver's scoped-receipt ledger; the production NAS/orgmail
   receiver must emit the same scoped receipts (contract documented in
   CROSS-PATH-AUDIT).

## HOST NOT RUN (executable steps in RYZEN-COMMISSIONING.md)

1. Real uid separation + approvals directory permissions.
2. systemd restart under kill -9.
3. Live NAS mount identity (`require_existing_root`).
4. OpenCode installed-version binding against the interim adapter.
5. Model endpoint policy smoke against real registered endpoints.
6. Backup/restore + receiver reconciliation on a populated workspace.
7. M5-disconnection freshness (per-producer list in RYZEN-COMMISSIONING).
