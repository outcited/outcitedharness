# RUNTIME-REHEARSAL — one command, two projects, real code paths

## The command

```
python -m director_consumer.cli rehearse --workspace /tmp/rehearse-run
```

Isolated: synthetic feeds, isolated receiver with its own dedup/acceptance
ledger, isolated approval artifacts, independent verifier fixture. No
production inbox, credential, queue, or model is reachable.

## Result (this commit): 21/21 cases PASS

**SOFTWARE ACCEPTANCE: PASS** (see ACCEPTANCE-LEDGER for invariants).

The workflow, end to end (Project A):
observed problem (queue-empty + inventory-outside + `._` junk, measured
with metadata) → context packet persisted (`decisions/rehearsal-<digest>.json`,
exact task reference retrieval) → proposed investigation → operator
approval imported (uid-owned artifact, full scope) → atomic reservation +
delivery (single transaction) → isolated delivery (1 artifact) →
receiver dedup (retransmit suppressed) → session-verified ack (envelope +
binding + transport) → investigation report → independent verifier →
task closed. **The production incident stays open** — no repair claimed
(case `investigation_complete_incident_stays_open`).

Project B: same vocabulary, same local IDs (`SUPPLY-RECON-01`,
`OBJ-SUPPLY-1`), independent owners/state/authority.

## Adversarial coverage (A–T)

| case | result | mechanism |
|---|---|---|
| A operator/service separation | PASS | labeled test doubles; production pins process uid |
| B service forgery / observe mode | PASS | file-content authority; observe refuses effects |
| C revoked/expired/malformed | PASS | revocation + artifact re-read every check |
| D grant reuse | PASS | digest-bound single action; idempotent same-action retry only |
| E crash before reservation commit | PASS | REAL `os._exit` in child; nothing consumed; same approval resumes |
| F crash after commit, before write | PASS | REAL death; resumable intent; 1 artifact |
| G crash after write, before sent | PASS | REAL death; stable filename; retry reconciles |
| H lost ack | PARTIAL | quarantine + retry semantics cover it; full network-loss sim not built |
| I 6 concurrent processes | PASS | spawn, no test lock; CAS intent; one durable message |
| J wrong same-UID session | PASS | session binding rejects; owner session accepted |
| K wrong message/digest/epoch | PASS | envelope mismatch quarantined |
| L forged after valid | PASS | valid ack never downgraded |
| M missing mount / conflict | PASS | `require_existing_root` refuses impostor inboxes; digest conflict reported |
| N old evidence on recurrence | PASS | occurrence-bound evidence; refused |
| O stale source vs cached health | PASS | explicit stale marker |
| P name collisions | PASS | project-scoped identities |
| Q restart | PASS | task state + ledger + artifacts unchanged |
| R all models down | PASS | ingestion independent; metered refused by policy |
| S inventory outside queue | PASS | `insufficient-evidence`, not starvation |
| T AppleDouble literals | PASS | GLOB basename prefix; ordinary dotted names excluded |

## Identity honesty

Mocked-uid tests are labeled (`test_identities=True`). Real OS separation
is a HOST gate (NOT RUN until the Ryzen arrives) — the rehearsal proves
the software contracts; the host checks prove the deployment.

## Machine-readable output

`<workspace>/rehearsal-report.json` — per-case pass/fail + evidence,
summary verdicts (software / host / live, kept separate).
