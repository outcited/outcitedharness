# NEO ↔ Gas City — Integration Architecture

**Status:** candidate implementation, tested offline (20/20) + one isolated live
demo against `designwins-staging` (2026-10-27). NOT deployed; no production writes.
**Law:** no second Mayor, no competing queue. Gas City is the sole execution
authority; the director on seven is retired-in-place (inactive since 2026-10-20).

## Components

```
GitHub issue (dw.finding.v1 body)          Sam approval artifact
        │                                   (action: gascity-ingest,
        │                                    task_ref, digest, expiry)
        ▼                                          │
 ┌─────────────────────────┐    ┌──────────────────────────────────┐
 │ M5: bridge/ (this repo) │───▶│ Gas City supervisor REST API v0   │
 │  finding.py  (Ph.1)     │    │ 127.0.0.1:8372 on samsons-mac-mini│
 │  handoff.py  (Ph.3)     │    │ (reached via SSH tunnel M5:8373)  │
 │  neo_status.py (Ph.2/4) │◀───│ read-only: status/agents/beads/   │
 │  gascity_client.py      │    │ sessions/pending/events+SSE       │
 └─────────────────────────┘    └──────────────────────────────────┘
        │ receipts (JSON, append-only per finding)
        ▼
 demo/out/ → receipts/           NEO consumes snapshots + alerts
```

## Data contracts

1. **Finding** — `schemas/dw.finding.v1.json`. A GitHub issue body is an
   UNTRUSTED work definition (director gh_workloop law): validation gaps are
   named, never silently accepted. `authorization_state: proposal` findings are
   never dispatched.
2. **Authorization** — Sam-authored artifact (schema inherited from seven's
   `/etc/harness-director/approvals/`), `action: "gascity-ingest"`, bound to
   ONE task_ref, ONE city (recipient), payload_digest of the exact finding
   body, expiry, max_actions budget. Demo artifacts cite the authorizing brief
   in `authorization_ref`; production artifacts must live in the approvals
   directory Sam controls.
3. **Bead mapping** (`finding.to_bead_payload`): title `TASK-ID — summary`;
   labels `neo-finding:<finding_id>`, `gh:<repo>`, `severity:<sev>`, `neo`;
   metadata `neo-finding-id`, `neo-task-id`, `neo-authorization-state`,
   `neo-digest`. Bead is created open+unassigned: queueing only — dispatch
   (`gc sling`) remains a separate, human-visible step.
4. **Acceptance receipt** — `dw.gascity-acceptance.v1`: finding id,
   authorization ref, deterministic Idempotency-Key
   (`neo-sha256(finding|digest|auth)[:40]`), bead id, INDEPENDENT read-back
   (status/title/labels), event-log seq, attempts. States: `refused |
   duplicate | accepted | api-error`. The POST response alone is never a
   receipt (Phase 3 law).

## Guarantees and their mechanisms

| Requirement | Mechanism (verified live) |
|---|---|
| Ingested exactly once | label pre-check + Idempotency-Key; resubmit returned `duplicate` with same bead `ds-gb1` |
| Unauthorized cannot dispatch | handoff refuses pre-API (6 refusal reasons unit-tested); CSRF `X-GC-Request` 403-verified on the live API |
| Durable acceptance | Dolt-backed bead store + read-back + event seq 37493 |
| Replay safe | deterministic key; supervisor Idempotency-Key replay; event stream `after_seq` resume |
| Failed delivery ≠ silent execution | api-error receipts record status/code; bounded retries (3) on 5xx/429 only |
| NEO observes without private state | read-only snapshots (JSON+MD) from public API endpoints only |

## Continuity rules (Phase 4, `neo_status.evaluate`)

orchestrator-idle-with-ready-work · in-progress-without-live-session ·
work-awaiting-human (pending interactions) · city-status-partial (already
firing on both live cities: bead-cache/store-health timeouts) ·
duplicate-findings (already firing on production: `hw-*`/`do-*` task mirrors) ·
finding-without-receipt. Bounded retries live in handoff; NEO never restarts,
reassigns, merges, or deploys.

## Known gaps (next)

- ~~gh token on the mini invalid~~ **FIXED by Sam 2026-10-27** (device-flow login,
  beargallbladder, ADMIN on outcitedharness) — PR/CI receipt leg unblocked.
- Production dispatch path (`gc sling`) intentionally NOT automated — recommend
  it stay human-initiated until Sam approves otherwise.
- Supervisor POST does not return `X-GC-Index` (receipt relies on event-log seq).
- `dw-finding.v1` issue-body convention needs one live GitHub issue on
  `outcited/outcitedharness` to be ratified (PR pending Sam's go).
- E2E L3 in staging requires adding a rig+agent to the staging city (config
  change, needs approval) OR manual sling in production (dispatch artifact).
