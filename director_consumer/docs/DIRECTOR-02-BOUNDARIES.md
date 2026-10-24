# DIRECTOR-02-BOUNDARIES — generic core vs project configuration

**status:** proposed boundaries only (01B mandate: no refactor). The director
is a permanent, project-agnostic orchestration system; DesignWins is its
first project. The M5 is a development workstation, not a server dependency;
the permanent controller is the 32 GB Ryzen Ubuntu box.

## What is already project-independent (keep as core)

| module | why it is generic |
|---|---|
| `schema.py` | events/assessments/proposals/evidence carry no project nouns |
| `state.py` | journal hash-chain, transactional outbox, cursors, dedup — mechanics only |
| `consumer.py` | observe loop, backfill law, effectively-once law, watcher synthesis |
| `adapters.py` (mechanisms) | file-tailer (byte-exact), sqlite-ro tailer, ledger line-counter, upsert-differ, freshness/fail-closed — five reusable poller shapes |
| `impact.py` (engine) | graph closure, structured matching, authority hooks, incident windowing |
| `mailgen.py` (grammar) | filename/type grammar of the shared fleet protocol; proposal-only law |
| `llm.py` (router) | token bucket, overflow, advisory-only law, degrade-to-None |

## What is already per-project (externalized config)

- `deps.yaml` — dependency map, rules, severities, authority per rule
- `machine-aliases.yaml` — this fleet's identity reconciliation
- `docs/feeds.real.yaml` — this deployment's feed locations (M5 today,
  Ryzen mirrors later; paths are config, never code)

## Residual DesignWins coupling in core — the smallest backward-compatible path

1. **`impact.py: SAM_ONLY_CATEGORIES`** — "Sam" is institutional, not
   engine. DIRECTOR-02: move to a `policy:` block in the project's deps.yaml
   (`authority_categories: {action: human-only-reason}`); engine resolves
   authority through policy, keeps a fail-closed default (unknown action →
   escalate).
2. **`llm.py: DEFAULT_ENDPOINTS`** — hardcoded asus2/dgx1 endpoints.
   DIRECTOR-02: endpoints from the project/deployment config (mirrors
   `models.yaml`); code keeps only the routing law (primary→overflow→None).
3. **`mailgen.py: ACTION_TO_MAIL` + `FROM_IDENTITY`** — action→type mapping
   belongs with the project's rules; identity per deployment. Config keys
   with current values as defaults (zero behavior change).
4. **Feed role heuristics** — `consumer.py` treats `sentinel` as the watcher
   feed by name. DIRECTOR-02: `role: watcher` annotation in feeds config;
   the engine reacts to roles, not names. Adapter class NAMES
   (`factory_events`, `facets`) stay as mechanism names; projects may
   instantiate them under their own feed names.
5. **Per-project institutional memory** — recon docs, audit history,
   DECISIONS references live per project; the journal workspace is already
   per-deployment (one workspace per project isolates history by
   construction).

## Proposed layout (DIRECTOR-02 build target)

```
director_consumer/          # engine only (no project nouns after items 1-4)
projects/
  designwins/
    deps.yaml               # map + rules + policy block
    machine-aliases.yaml
    feeds.yaml              # per-deployment (m5-dev / ryzen-prod)
    memory/                 # recon, audits, institutional docs index
  <future-project>/         # same shape; engine shared
```

Resolution order: explicit path → project dir → packaged defaults (today's
flat files remain valid defaults — backward compatible, no flag day).

## Shared, dynamically discovered infrastructure (DIRECTOR-02 scope)

Fleet awareness (machines, services, agents) is DERIVED per poll from
existing sources (fleet_state snapshots, AGENT_NETWORK.md, Tailscale status)
— never a second truth store. Project configs reference infrastructure by
role/service tokens; identity reconciliation stays in aliases+registry.

## Remaining risks / readiness

- **Ready for DIRECTOR-02** after this gate: engine laws (effectively-once,
  chain, outbox, fail-closed, advisory-only, human-only) are tested and
  failure-injected; cursor work is byte-exact.
- Fleet prefix-hash check is O(consumed bytes) per poll — fine at
  CHANGES.md scale; revisit only if a feed grows to MBs per poll.
- Production-side debts OUTSIDE the director (documented, not masked):
  sentinel dir permissions + plists missing `PIPELINE_DB` (watchers exit 1
  since Oct 9); `._` junk queue flood awaiting the factory fix.
- One pre-existing repo test failure (`test_discovery_api::
  test_design_lifecycle`) — fails at the pre-director base commit too
  (evidence in 01B verification); unrelated to the director.
- Ryzen migration: no code change required for feed paths (config-only);
  journal state migrates by copying the workspace (append-only + chain
  verify proves integrity after copy).
