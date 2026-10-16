# FLEET-RECONCILIATION — identities, freshness rules, migration handling

## Fixed from DIRECTOR-02 gaps

1. **Self-identity:** Tailscale `Self` resolves to the canonical machine id
   (via machine-aliases.yaml: `m5max-ai → m5`); `localhost` peers are
   skipped entirely — no duplicate identities (tested).
2. **Freshness law:** `latest_snapshot()` age-checks the snapshot timestamp
   (threshold 26h, configurable). A stale snapshot marks services
   **UNKNOWN** — never healthy, never degraded-by-guess. The briefing shows
   the snapshot age. Live shadow run: fleet_state snapshots are from
   Oct 12 → all service telemetry reads UNKNOWN (correct; CHANGES.md
   remains the live feed).
3. **Alias reconciliation:** snapshot box names, tailscale hostnames, and
   config ids canonicalize through one alias map (`e10b→dgx1`,
   `spark-e10b→dgx1`, `m5max-ai→m5`, …).
4. **Resource states:** `planned | migrating | active | draining |
   unavailable | degraded` come from fleet.yaml (declarative, with
   provenance) and surface in `machine_health`; connectivity stays a
   separate layer (online ≠ healthy).

## Current topology as the registry sees it (operational facts)

| machine | roles (config) | status |
|---|---|---|
| ryzen-1 | engineering-director-controller | planned |
| ryzen-2 / ryzen-3 | extraction/normalization workers | planned |
| m5 | control-plane-interim → dev workstation | **migrating** (both extraction services and managing agents moving to Ryzen) |
| m4 | frontend-dev, interactive OpenCode | active |
| mac-mini / whitepine | PDF factory / crawlers | active |
| mbp-intel | slow extraction worker | active |
| dgx1–3, asus1–4 | inference (TP4, ops brain, adjudicator, embedder) | active |

Tracked per machine: identity/aliases, tailscale IPs, connectivity,
containers, services (or UNKNOWN), roles, status, capability (declared),
last_observed + per-field provenance.

## Not yet measured (UNKNOWN by design)

CPU/RAM/disk per machine over the network (no telemetry source yet —
probes would be new infra; gap noted in GAPS-NEXT-PHASE 03).
Queue capacity and model availability derive from continuity/model policy
feeds rather than per-machine metrics.
