# DIRECTOR-01 — reused vs newly added

## Reused (zero modification, consumed read-only)

| existing system | how consumed |
|---|---|
| `scripts/fleet_state.py` → `fleet-state/CHANGES.md` | FleetChangesAdapter (sections = events) |
| `scripts/sentinel.py` → `sentinel/ALERTS.md`, `last-known-good.json` | SentinelAdapter (+ freshness) |
| Rust `factory_events` (immutable ledger, ra03-state) | FactoryEventsAdapter (sqlite ro URI, rowid cursor) |
| facet-wave ledgers `done-<facet>.txt`/`parked.txt`/`missing-docs.log` | FacetLedgersAdapter (line-count deltas) |
| `pipeline-live.db: budget_spend` | BudgetSpendAdapter (ceiling anomaly) |
| `AGENT_NETWORK.md` identity law (by box, never tool) | mailgen addressing (`engineering-director`) |
| agentmail grammar `<ts>_<from>_<to>_<type>_<subject>.md`, types {status,question,answer,directive,handoff,work-brief} | mailgen filenames/bodies (proposed-only) |
| `HANDOFF-m5 "Sam's calls, only"` + DECISIONS.md authority | impact.authority / SAM_ONLY_CATEGORIES |
| `config/models.yaml` endpoints (asus2_ops_brain, dgx1_overflow) | llm.py DEFAULT_ENDPOINTS + overflow order |
| `MODEL_SETTINGS.md` settings law (enable_thinking:false) | llm.py request body |
| promotion-packet evidence pattern | evidence lines in every proposal |

## Newly added (this branch)

- `director_consumer/` — schema, 5 read-only adapters, atomic state store
  (cursors/dedup/proposals/hash-chained journal), deterministic impact engine,
  versioned `deps.yaml` (schema 1), ops-brain router (token bucket, advisory
  only), mail/escalation generators, observe-mode consumer, CLI.
- `tests/test_director_consumer.py` — 11 acceptance tests (all 8 required
  criteria + freshness fail-closed + tamper detection + advisory-only model).
- `director_consumer/docs/` — feeds.real.yaml, this doc, JOURNAL-AND-EVIDENCE.

## Explicitly NOT built (per directive)

No orchestration framework, no messaging transport (nothing is delivered),
no authoritative database (journal is advisory evidence, sources stay
authoritative), no production writes of any kind.
