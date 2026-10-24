# FACTORY-CONTINUITY — real measurements, source mapping, unknowns

**run:** 2026-10-16 shadow, workspace /tmp/d03-shadow · read-only · every
number below is read from a named source; nothing is estimated except the
explicitly-marked class-cost table.

## Source mapping

| measurement | authoritative source | state |
|---|---|---|
| Rust lane queue/leases | `ra03-state/factory.sqlite3` factory_chunks | live |
| Substrate queue | `pipeline-live.db` jobs | live |
| Consumption rate | facet `done-*.txt` line growth (needs ≥2 runs) | run 3+ |
| Acquisition rate | `results/pdf-intake-20261015.log` checkpoints | live |
| Doc class / pages | `results/l0-census-20261006/l0-records.jsonl` | partial join |
| Prioritized backlog order | `factory_targets.json` (NAS Public) | **UNAVAILABLE** — named gap |
| Manufacturer health | mac-mini factory telemetry | **UNAVAILABLE** — adapter spec + handoff proposed |
| Worker-hour costs | `projects/designwins/class-costs.json` | bench-derived, marked |

## Current measurements

- ready backlog (queues): **0 docs** (rust queued + pipeline pending)
- rescan backlog (facets): 121,495 docs pending 8-facet passes
- consumption rate: **UNKNOWN** — 1st observation recorded this run;
  missing measurement: a second observation ≥ minutes apart (self-heals)
- acquisition: **+666 docs/day** (intake log span 2026-10-15→16)
- junk contamination: **176,709** dead `._` jobs (threshold 1,000 → incident)
- starvation: **0.0h, basis=backlog-zero** — real starvation signal because
  the queue is literally empty; NOT a telemetry artifact. The distinct
  `unknown-telemetry` and `queues-unavailable` bases are implemented and
  tested so future gaps never masquerade as starvation.

## Class-aware cost (§2: PDFs are not equal)

L0 join over the rescan backlog: 1,060 docs classified
(datasheet 410 · reference_manual 266 · unknown 200 · user_manual 68 ·
errata 65 · application_note 25 · pcn 14 · reference_design 8 · bom 4);
pages tracked per class (reference_manual carries the heavy tail).
Worker-hours: **9.64h PARTIAL** — only class-mapped docs counted; class
costs are bench-derived planning values (class-costs.json provenance).

**Named gap:** L0 join covers only 1,060/121,495 backlog docs — the census
predates the vault rescan root. Missing measurement: L0 re-census over
`/vault` paths (or a path-map). Until then class coverage is 0.9% and the
worker-hour figure is a floor, not a total.

## Prioritized supply assessment

Acquisition rate is healthy (factory ahead 1.4×) BUT ready-to-extract is
empty and the rescan backlog's priority order cannot be verified
(factory_targets.json unreachable). Coverage verdict: **critical-path risk
NOW from queue starvation; priority composition UNKNOWN**. Action for
owner: mac-mini factory target manifest exposure (see
INCIDENT-CORRELATION + the manufacturer handoff draft).
