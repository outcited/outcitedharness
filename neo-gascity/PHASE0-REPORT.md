# NEO ↔ Gas City Integration — Phase 0 Inspection Report

**Task:** DesignWins NEO↔Gas City closed loop (P1)
**Date:** 2026-10-27 · **Mode:** read-only inspection + upstream analysis
**Inspector:** m5-opencode · **Authorization:** inspect/prepare only, no dispatch

## 1. What "Gas City" actually is (upstream-verified)

- **Product:** `gastownhall/gascity` — `gc` CLI. Orchestration-builder SDK extracted
  from Gas Town (`gastownhall/gastown`, `gt` CLI). Cloned to
  `/var/folders/dj/pftbm4gd4yg6s0y9h_2brmh80000gn/T/opencode/{gascity,gastown-upstream}`.
- **Model:** six primitives (Agent / Bead / Formula / Rig / Pack / Event) over three
  machines: orchestrator (reconcile loop), bead store (bd + Dolt by default; file
  provider optional), event bus (`.gc/events.jsonl`, typed, seq cursors).
  Work survives agent/session crashes because work lives in beads.
- **Mayor is NOT built in.** Roles are pack-supplied configuration. The Gastown pack
  (`gastownhall/gascity-packs`) supplies mayor/witness/refinery/deacon/polecat agents.
  A deployment "has a Mayor" because its city config declares one
  (docs/reference/gastown-command-map.md:83).
- **Supervisor REST API** (docs/reference/api.md, OpenAPI 3.1, 137 paths, served by
  `gc supervisor`, default `127.0.0.1:8372`):
  - Beads CRUD: `POST /v0/city/{city}/beads`, `PATCH .../bead/{id}`, labels,
    dependencies; 422-with-nothing-written on invalid labels/metadata.
  - **`Idempotency-Key` header accepted on every POST mutation** (verified in
    docs/reference/schema/openapi.json) — native idempotent submission.
  - `X-GC-Request` anti-CSRF header required on mutations.
  - `X-GC-Index` response header = city event seq at read time → gap-free
    observation; SSE streams (`/events/stream`) resume via `Last-Event-ID`/`after_seq`.
  - Sessions: list/peek/transcript; **pending interactions**
    (`GET .../pending`, `POST .../respond`) = native "work awaiting approval".
  - Mail, convoys, orders, formulas, health/readiness endpoints.
  - Errors: RFC 9457 with stable `urn:gascity:error:<code>`.
- **Auth/trust** (docs/reference/trust-boundaries.md): operator config/packs =
  trusted code; bead titles/mail/API fields = untrusted data (never shell-interpolated).
  No per-client token auth on the API — it is a localhost control plane. Remote
  access must ride an SSH tunnel or operator-approved exposure.
- **Dedup/retry/replay/recovery inventory:**
  - `Idempotency-Key` request replay; `Ensure*Bead` show-then-create patterns
    (gastown internal/beads/beads_rig.go:97); MR dedup key = branch+SHA (beads_mr.go:23).
  - github-sheriff plugin pattern: cooldown gate + `digest = true` run-dedup
    (gastown plugins/github-sheriff/plugin.md).
  - Dolt transactional writes (BEGIN/DOLT_COMMIT/COMMIT); orchestrator **adopts**
    live sessions on restart instead of respawning; event log rebuilds pending-
    interaction announcements after restart ("announces nothing twice", api.md:410-417).
  - gh_workloop (our seven code) is itself idempotent keyed repo+issue.
- **Heartbeats:** gt has three stores with explicit rules (docs/concepts/heartbeats.md);
  gc exposes agent/session liveness via status/agents endpoints and controller-side
  pending detection (5s cadence).

## 2. Live infrastructure inspection

| System | Where | Status | Evidence |
|---|---|---|---|
| Gas City install | samsons-mac-mini (Tailscale 100.80.101.116, user samsonkim) | **UNREACHABLE — Tailscale node key expired** | `tailscale ping` → "peer's node key has expired"; ssh/nc 22 timeout |
| Engineering Director | seven (100.89.168.75, user samsonkim) | LIVE | `/etc/harness-director/{approvals,designwins,feeds.yaml}`, `/opt/harness-director/current/director_consumer/` |
| GitHub workloop | seven, director_consumer/gh_workloop.py | LIVE | pulls issues from allowlisted repos `outcited/outcitedharness`, `outcited/designwins-v2`; idempotent; untrusted-definition downgrade |
| TaskStore | seven, director_consumer/tasks.py | LIVE | states incl. `offered` (ingest proposes), `verified`; restart-safe |
| AgentMail bridge | NAS `/Volumes/Electronicdataassets/agent-inbox/<agent>/` | LIVE | mac-mini box active through 2026-10-27T00:15Z; messaging.py: propose→authorize→deliver→`record_ack`; acceptance = validated ACK envelope ("05C §5") |
| Authority policy | seven `/etc/harness-director/designwins/policy.yaml` | LIVE | human_owner: sam; human_only_actions; agent_authorities; metered-provider hard refusal |
| Approval artifacts | seven `/etc/harness-director/approvals/*.json` | LIVE | schema: project, action, recipient, task_ref, objective_ref, expires_at, issuer, max_actions, spend_scope, max_spend_usd, payload_digest |
| Fleet observability | M5 fleet_state.py + sentinel; feeds mirrored to seven | LIVE | feeds.yaml: fleet_changes, sentinel, factory_events, facets, budget |

**Mac-mini facts from records** (live confirmation pending re-auth):
PDF-factory/crawler host (T7 spool, `shared-docs-queue/outbound/mac-mini/`), lane
owner "pdf-factory" in gh_workloop LANE_REGISTRY; mailboxes `mac-mini` and
`mini-opencode`. No Gas City references anywhere in broadcast/mail yet — Gas City on
the mini is newly commissioned and not yet wired into the agent network.

## 3. Reuse-versus-new matrix

| Spec capability | REUSE (exists, verified) | NEW (must build) |
|---|---|---|
| Finding contract (Ph.1) | gh_workloop REQUIRED_FIELDS/TASK_ID_RE/allowlist; untrusted-definition downgrade law | `dw.finding.v1` JSON schema (severity, repro, pos/neg regression tests, receipts) + validator; issue-body convention |
| Authorization | Sam approval artifacts + authorization.py + policy.yaml | Link finding→approval→Gas City task in receipt; nothing else |
| Authenticated ingestion (Ph.3) | Supervisor API `POST .../beads` + `Idempotency-Key` + labels; `gc sling`; read-back via GET; `bead.created` event | Thin client adapter (resolve→dedup-check→submit→read-back→receipt). **No second orchestrator** |
| NEO read-only view (Ph.2) | GET status/agents/beads/events/SSE/pending/readiness; TaskStore on seven; mailboxes | Snapshot assembler (read-only merge → JSON+MD), no writes to either system |
| Continuity (Ph.4) | director feeds; gc controller pending detection; heartbeat convention; witness/patrol patterns | Cross-system rules: mayor idle w/ ready work, missing receipts, PR-CI-failed (gh read-only) |
| Receipts | `record_ack` validated-envelope law; event seq + X-GC-Index; Dolt history; payload_digest pattern | Acceptance-receipt format composing: idempotency key + bead id + read-back + event seq |
| Dedup | Idempotency-Key; label `neo-finding:<id>`; dead-twin guard precedent; repo+issue key | finding↔bead mapping table (tiny, in bridge state) |
| Replay/restart | Dolt durability; session adoption; event cursors; restart-safe ingest | End-to-end replay harness (Ph.5 demos) |
| PR/CI receipts | gh CLI; merge-queue/formulas in packs; github-sheriff CI-failure pattern | Receipt publication step in bridge |
| Coordination surface | GitHub (allowlisted repos) + approvals dir + mailboxes | Convention for `DW-NEO-*` task refs |

**Verdict:** no second orchestrator needed. The supervisor API already provides the
authenticated, idempotent, receipt-capable ingestion surface; the director already
owns authorization + GitHub ingest; the bridge is a thin, mostly-read-only adapter.

## 4. Blockers & decisions for Sam

1. **Mac mini Tailscale node key expired** — re-auth required (on the mini:
   `sudo tailscale up` login, or extend key in admin console). Blocks live Phase 0
   confirmation (city name, mayor agent, packs, port) and all Phase 5 demos.
2. **NEO→mini transport:** SSH tunnel `M5:8372 → mini:8372` (recommended; API has no
   token auth) vs tailscale-IP exposure. Decision needed before Phase 2/3 code runs live.
3. **Repo home for the bridge:** `outcited/outcitedharness` (already allowlisted by
   the director's workloop) recommended.
4. After mini re-auth: confirm mayor agent name, city name, installed packs, and
   whether a GitHub gate (gh CLI auth) is configured on the mini.
