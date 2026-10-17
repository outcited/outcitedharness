# RESUME HERE — Engineering Director, pre-Ryzen checkpoint

> Read this before resuming the Director project. Preserve the candidate; pause broad platform development. The next operational milestone is a restricted observer on the dedicated Ryzen. This document is a handoff, NOT permission to deploy, send mail, repair production, or start DIRECTOR-06.

## 1. Exact stopping point

| Item | Checkpoint |
|---|---|
| Repository | `outcited/outcitedharness` |
| Implementation branch | `director/05c-runtime-acceptance` |
| Pinned application candidate | `2a5a12cf495b76978e7f4e3d98e4bbf9a61b9031` |
| Previous reviewed implementation | `d33106207b63e1d6ffe5b6280eeee4f9975749ff` |
| This handoff branch | `director/05c-pause-checkpoint-2a5a12cf` |
| Intended first runtime | Dedicated 32 GB Ryzen, Ubuntu, observation-only |
| Implementation agent | Existing M5 OpenCode CLI Director session; reported stopped after push |
| Host arrival/installation | Awaiting confirmation from Sam; not verified by this checkpoint |
| Deployment / live mail / spawning | None authorized or performed by this documentation change |
| DIRECTOR-06 | Not begun; live communication disabled |

The implementation branch was checked against the pinned candidate while preparing this handoff and was identical. Remote Git does not reveal uncommitted local work: inspect local status and worktrees on resumption. Do not reset to this SHA if newer work exists.

This documentation commit changes no application code, runtime configuration, task ownership, production data, or existing agent worktrees. It is on a separate branch so the M5 agent's implementation branch is not advanced behind its working tree.

## 2. Evidence and release verdicts — keep these distinct

Sam relayed the implementation agent's clean-worktree report at the candidate:

- Director tests: **187 passed / 0 failed / 0 skipped**.
- Repository tests: **1,650 passed / 1 failed / 0 skipped**.
- Reported known failure: `test_discovery_api::test_design_lifecycle`; pre-existing baseline evidence references `d09d7d6e` and the 01B worktree verification.
- Rehearsal: **22/22**.
- Reported dependencies: Python **3.11.15**, PyYAML **6.0.3**.

These counts are **implementation-agent reports**, not a new independent execution or CI receipt produced by this handoff. The reviewing assistant inspected committed source; it did not independently rerun the complete suite for `2a5a12cf`.

**Review disposition:** retain the commit as an observation-only commissioning candidate. This is not a blanket acceptance of every A/B/C guarantee. Live receiver/identity/outcome-verification issues remain below. Host commissioning is NOT RUN. Live authority remains DISABLED.

Existing audit files that say PASS/GO must be read with their demonstrated scope and these open review findings. A passing simulated workflow does not prove an unfinished production adapter.

## 3. Product intent that must survive this pause

One durable, project-independent Engineering Director; DesignWins is its first project, not its identity. Preserve objectives, architecture, decisions, evidence, ownership, and history across changing models, agents, sessions, and machines.

Protect the critical path. Detect consequential changes, retrieve institutional context, distinguish facts from assertions and hypotheses, identify the responsible owner, coordinate already-authorized work, verify outcomes, and escalate only when human judgment is required.

For DesignWins, protect **prioritized, usable extraction supply**. Sam must not need five daily status checks to discover changed manufacturer endpoints, stalled acquisition, bad intake, missing admission, or unattended repairs. Remember the investigation and verified result so recurring problems do not require the same human diagnosis.

Do not confuse acquired PDFs, eligible inventory, admitted jobs, chunks, facet passes, leased work, verified output, worker-hours, CPU-core-hours, and elapsed coverage. Missing telemetry is not an empty queue. Existing inventory outside one queue does not prove an admission defect or idle extractors. Historical shadow counts are not today's measurements.

Machine != agent != OpenCode session != task != accountable owner. Multiple OpenCode CLI tabs can share a machine and operating-system account. Preserve human-owned interactive sessions; do not infer ownership from titles, nicknames, mail filenames, or claims alone.

Eventually, the Director may coordinate and spawn bounded work under approved delegations. The one-message commissioning limit is not the permanent product design. Spawning and broad delegation are NOT enabled now.

## 4. Resource and economic decisions — do not reopen without evidence

| Resource | Current user-stated assignment or constraint |
|---|---|
| Ryzen #1, 32 GB | Permanent director and local state; not the model server |
| Ryzen #2, 32 GB | Offload extraction/normalization services and managing agents |
| Ryzen #3, 64 GB | Offload heavier extraction/normalization services and managing agents |
| Optional fourth Ryzen, 32 GB / 2 TB NVMe | Extra execution capacity; not required for commissioning and not assigned by this checkpoint |
| M5 laptop | Existing development console; extraction services AND managing agents are migrating away |
| M4 laptop | Frontend development and interactive OpenCode workflow |
| Mac mini / Tapes / OpenCode Tapes | Aliases for the acquisition-factory host; existing N100 crawlers remain in that factory |
| Old MacBook Pro | Additional slow extraction resource |
| DGX / ASUS | Existing local inference, TP4, operational/overflow resources; existing 200 Gb/s DGX fabric |

These are assignments and migration targets, not proof that new machines are installed or healthy. Discover actual capabilities, source timestamps, reservations, and endpoint availability.

Sam authorizes use of registered local inference when available and his approved **GLM 5.3 Z.ai** and **MiMo 2.6 Pro** subscription resources within actual plan limits. Local DeepSeek TP4 exists; local GLM must not be assumed. Re-read registered Qwen/model identities rather than guessing versions or quantization. Protect production inference reservations.

**No discretionary OpenRouter or other metered fallback.** Access to a key is not spending authority. The first observer should have no unrestricted metered credentials. Subscription endpoints must actually use the subscription product; a provider name alone is insufficient. Existing separately approved production extraction workflows are outside this checkpoint's mutation scope.

Reasoning may be plentiful. Human attention, evidence quality, integration capacity, and productive throughput are scarce. Use intelligence to protect those resources, not to create activity or new architecture for its own sake.

## 5. Open review findings — preserve, do not silently mark closed

These are source-review findings at the pinned commit, not production incidents or newly executed exploit tests.

| ID | Remaining gate | Evidence / required resolution |
|---|---|---|
| R1 | Authenticate recovered acceptance | In `messaging.py::reconcile_with_receiver`, `rec.get("session_id")` is supplied as established origin. A scoped receipt's own identity field is not independent authentication. Production receiver retrieval must establish origin and carry the validated assignment/digest. |
| R2 | Enforce receiver project boundary | The same method selects/updates the project named by the receipt instead of first requiring equality with `self.project`. Reconciliation for project A must not mutate project B. Add the cross-project negative test. |
| R3 | Verify the actual source outcome | `incidents.py::validate_receipt` rejects selected failure phrases and checks path existence, but does not read/evaluate source evidence against recovery criteria. Implement the first narrow source-backed production verifier; formatting and file existence are not success. |
| R4 | Finish real session-origin and receiver adapters | The implementation explicitly reports missing production session-origin/scoped-receipt emission. A fixture or protected binding alone must not be called authenticated session origin. A trusted runtime bridge is a design option; native signed OpenCode tokens are not a required future vendor feature. Inspect the installed supported interface before choosing the integration. |
| R5 | Finish executable host runbook | `RYZEN-COMMISSIONING.md` contains placeholders and illustrative checks. Opening a SQLite file is not session-origin verification; directory existence is not NAS identity; a model-list request is not proof of subscription/spend routing. Resolve actual paths and test outcomes at commissioning. |

Do not reopen broad platform development or send a competing agent to solve these. The existing Director owner retains implementation responsibility. This document assigns no new production task and does not silently ratify a repair or release.

## 6. Remaining M5 dependencies

The candidate commissioning document names the following as unresolved; revalidate them against the actual migration state before relying on the laptop being dispensable:

| Producer / source | Risk when M5 sleeps |
|---|---|
| `fleet_state` / fleet changes and snapshots | Fleet observations stop updating |
| Sentinel / ALERTS and last-known-good | Watcher observations stop updating |
| Extraction dispatch / pipeline mirror | Queue view can freeze |
| Facet orchestration / completion ledgers | Consumption evidence can stop updating |
| OpenCode metadata | Session and ownership evidence can become stale |
| Shared-docs queue repository | Mirrored mail history can stop updating |

Moving the director process does not relocate these producers. A newly copied old file is still old evidence. Do not close M5 as a test until the remaining production dependencies are understood. Prefer isolated feed-loss tests first.

## 7. Restart sequence

### Before the Ryzen arrives

Preserve this handoff, candidate, existing reports, and open gates. The existing Director agent may remain paused; other acquisition/extraction work continues normally. No new broad PRD, no parallel Director implementation, no automated deployment.

### When Sam confirms the Ryzen is available

1. Read this document and the referenced commissioning/audit files. Check remote branch head plus local worktrees and uncommitted work. Report differences from this checkpoint; do not reset or merge blindly.
2. Confirm actual host identity, Ubuntu version, SSH/Tailscale access, and installation target with Sam. Privileged installation requires his separate go-ahead. The M5 OpenCode session can remain the development console; the service runs on Ryzen.
3. Stage the pinned candidate as a dedicated non-root **observer**. Restrict sources to approved read-only interfaces. Writable state/reports stay on the controller's local disk. No production inbox write permission, production DB write permission, real action approvals, or unrestricted metered keys.
4. Verify configuration, source identity/freshness, restart, local state recovery, consistent backups, and restore. Treat missing live adapters as blocked capabilities rather than substituting test doubles. No second authoritative controller.
5. Produce one operational briefing: for each active extraction lane, what prioritized work is executable, what is running, what is blocked, who owns the next step, and what evidence/unknowns support that answer. This is the observer's first useful result.
6. Keep live messaging, spawning, reassignment, cleanup, production repairs, and release authority disabled. Close R1-R5 against the actual integration before seeking a live communication grant.

### First live experiment, later and separately authorized

Re-resolve the current accountable owner; do not assume `agent:m5-opencode` or the old `SUPPLY-RECON-01` assignment is still correct. Freshly establish that the incident still exists.

One read-only investigation request may then be proposed: reconcile prioritized inventory with the lane's actual execution mechanism; identify the blocker, owner, and source evidence; do not modify production.

Require the real authenticated acknowledgment and source-backed report verification. A report that confirms production is still blocked can complete the investigation task, but must not close the production incident. Any repair and verified recovery remain separate steps under separate authority.

## 8. Read next and preserve history

- [CROSS-PATH-AUDIT.md](CROSS-PATH-AUDIT.md): implementation's operation/validator map and disclosed adapter gap; read with R1-R4 above.
- [RYZEN-COMMISSIONING.md](RYZEN-COMMISSIONING.md): candidate layout and host checklist; resolve placeholders before execution.
- [REMAINING-HOST-CHECKS.md](REMAINING-HOST-CHECKS.md): explicit software versus host gaps.
- [ACCEPTANCE-LEDGER-05C.md](ACCEPTANCE-LEDGER-05C.md), [RUNTIME-REHEARSAL.md](RUNTIME-REHEARSAL.md): historical coverage; check which commit each report describes.
- [../acceptance.py](../acceptance.py), [../messaging.py](../messaging.py), [../authorization.py](../authorization.py), [../incidents.py](../incidents.py): source paths for current gates.

Application authority still comes from approved project decisions/policies and exact action grants, not from this checkpoint. This is a navigational handoff, not another operational truth store.

## 9. Minimal resume instruction for a fresh session

> Read `director_consumer/docs/RESUME-HERE.md` from `director/05c-pause-checkpoint-2a5a12cf`. Reconcile it with the current implementation branch and local work; do not reset anything. Report the candidate, live blockers, remaining M5 dependencies, and next observer-commissioning step. Confirm whether the Ryzen is available and whether Sam has authorized installation. Do not deploy, send mail, spawn agents, or start DIRECTOR-06 merely because you read this file.

Next update should record the actual host, installed application SHA, dated checks/receipts, current blockers/owners, and the next permitted action. Preserve previous evidence. Do not replace unknowns with optimism.
