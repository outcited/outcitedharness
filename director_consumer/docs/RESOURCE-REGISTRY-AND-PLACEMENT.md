# RESOURCE-REGISTRY-AND-PLACEMENT — machines are resources

## Model

`ResourceRecord{resource_id, kind, capabilities, reservations,
availability, freshness, constraints, project_assignments, health}`.
Derived from the live FleetRegistry (Tailscale + snapshots + config) —
project roles map to assignments through config, never engine logic.

Availability states: available | reserved | draining | maintenance |
unavailable | unknown. Health (service verdicts) stays a separate layer —
an available machine can be service-degraded (02's online≠healthy law).

## Placement (recommendation only)

`recommend_placement(requirements)` ranks candidates by: project
assignment match, availability, draining penalty, config preference,
migration-target bonus. Every result is stamped `recommendation_only:
true` — the director NEVER moves workloads or alters reservations in 05.

## DesignWins deployment facts (config data, not assumptions)

ryzen-1 director · ryzen-2/3 extraction workers · m5 migrating services
and managing agents to Ryzen (future dev workstation) · m4 frontend ·
mac-mini/Tapes + N100 acquisition · mbp-intel slow extraction · DGX/ASUS
inference + 200Gb/s fabric. `m5` surfaces `status=migrating` with its
migration note; tests prove migration-aware placement and that nothing
is mutated.

## Missing by design (UNKNOWN)

Per-machine CPU/RAM/disk telemetry has no source yet — reported UNKNOWN
rather than guessed (same discipline as everywhere else).
