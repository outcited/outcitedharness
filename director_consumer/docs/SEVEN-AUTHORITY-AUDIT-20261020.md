# Seven authority audit — 2026-10-20

## Evidence and limit

Repository baseline: `76997b2bca6231939b9b2848bf45962fa89f4c15`.
Audit branch: `audit/seven-authority-20261020`. The commit containing this report
is the candidate revision; tests below apply to its code diff against that base.
No service, credential, approval artifact, production state or host was changed.

Sources read through the GitHub connector:
- https://github.com/outcited/outcitedharness/issues/12#issuecomment-6284527447
- https://github.com/outcited/outcitedharness/issues/12#issuecomment-6262814447
- https://github.com/outcited/outcitedharness/pull/14

The October 20 receipt reports successful credential rotation and recovered
203/EXEC startup failure. These are agent claims, not our runtime observations.
The system-manager service reportedly executes as `harness-director`, not root.
“Root-level” describes its systemd manager, not its process UID.
PR #14 is independently observed merged into `director/06-fleet-intelligence`
(merge `812646e5048d92f5e69cf6db428a2eb38778340a`, GitHub merged_at
2026-10-20T19:43:14Z). That does not prove the host runs that revision.

Independent host attempt: `ssh -o BatchMode=yes -o ConnectTimeout=5 seven true`
failed resolving `seven` in this environment. No host session was established.
Live unit fragments, drop-ins, scripts, command lines, paths, timers, file
ownership/inodes, journal tails and deployed revisions remain UNKNOWN. This
repository contains deployment documentation, not either live Director unit or
`run-observer.sh`. We cannot classify the two live services as competing or
complementary from their names or the receipt alone.

## Service and scheduling map

| Component | Evidence | Execution / writes / trigger |
|---|---|---|
| System `harness-director.service` | Oct 20 agent receipt | Script called `run-observer.sh`; script token-loader hierarchy reported as credentials directory, /etc token, releases token. Service user reportedly harness-director. Actual arguments, workspace, restart configuration and drop-ins unknown. Clean cycle reported at 05:46:38 UTC. |
| User `harness-observer.service` | Oct 20 agent receipt | Active with zero restarts; credential in gci.conf drop-in. Actual ExecStart, arguments, workspace and role unknown. “Working one” is the agent's description. |
| Earlier user/system run loops | Oct 19 agent receipt | Both reportedly `director_consumer.cli run --machine-id seven --no-llm --interval 600`; user revision 89d3177a, system /opt/harness-director/current. Historical evidence only; cannot substitute for Oct 20 inspection. |
| GitHub ingest | Oct 19 agent receipt | harness-ingest.timer every five minutes; oneshot task ingestion. Reported lost monotonic timer anchor, manually rearmed and unattended tick observed by agent. Current timer/unit and database path unknown. |
| Daily brief | Oct 19 agent receipt | harness-daily-brief.timer at 07:00; timezone, current ExecStart and state paths unknown. |
| Repository deployment contract | UBUNTU-DEPLOYMENT.md | System unit runs CLI, /var/lib/harness-director workspace, /etc/harness-director config, Restart=always / 30 seconds. Mirror timer OnCalendar every five minutes, hourly health verify. This is intended configuration, not installed unit evidence. |

`cli run` has its own sleep loop (default 600 seconds), not a per-cycle timer.
`DirectorConsumer(mode='observe')` STILL WRITES state and can invoke
`_deliver_authorized()` when separately configured and authorized. Observe is
not a read-only process boundary. Retaining a second full CLI loop as an
“observer” does not make it passive.

## Repository persistence and routing

`DirectorConsumer` opens `<workspace>/director.db`. The same connection backs
TaskStore, incidents, project registry and authorization state. Ingest commits
cursors, seen fingerprints, proposals and journal together. Journal kinds include
assessment, marker, run, llm and investigation. Task history is in task_events,
not identical to the hash-chained journal. `record_model_usage` uses model_usage.

Derived writes include outbox/, escalations/, briefings/, ownership-latest.json,
sessions-latest.json, fleet observations/state/measurements and product graph
state (see consumer.py, briefing.py, product_graph.py). Code-intel CLI findings
use a separate `<workspace>/code-intel-findings.sqlite`. cli register-project and
cli verify instantiate DirectorState; **verify is not a strictly read-only host
inspection command** because construction runs schema/migration setup.

GitHub ingest is GET-only and keys task definitions by project/task ID and
repo#issue correlation. Sequential repeated ingestion reports duplicate. That
is not global deduplication across workspaces. Task transitions read an epoch,
then UPDATE by task/project without epoch compare-and-swap; ingest create and
transition are separate commits. Multiple task-mutating authorities are unsafe;
this patch does not claim to fix all TaskStore concurrency. Approval/transport
checks remain independent and unchanged.

Before this patch OpsBrainClient defaults include ASUS2 plus DGX1 overflow.
DesignWins policy also allows TP4 DeepSeek and z.ai/MiMo subscriptions.
Direct CLI construction uses defaults; consumer attaches project policy later.
Previously that checked endpoint *names* while retaining pre-policy URL/model.
Code investigations directly call the hardcoded ASUS2 URL/model in consumer.py,
through investigate._llm_call; they bypass ModelPolicy rate/usage accounting,
although investigation receipts include model/usage. `--no-llm` prevents client
construction and run_code_intel returns without a client in this revision.
Neither actual live service's current model flag is independently known.

## Reproduced defects and minimal correction

1. Two DirectorState connections alternate journal appends: A, B, A. A reuses
   its cached head, so its third row points behind B and verify_chain fails.
   Fix: acquire SQLite write transaction before head read; always read current
   committed/transaction-local head. Concurrent independent connections now
   preserve all 60 receipts and verify the chain. No history rewrite/migration.
2. No CLI run-loop exclusion existed. Add local advisory flock for the lifetime
   of `cli run` before consumer construction. Contention fails before DB setup;
   lock releases on normal exit, exception or process death. Never unlink it.
   This is per resolved workspace, not a global leader election or protection
   against old binaries/direct Python callers. Distinct workspaces still require
   operational authority consolidation. A competing service may restart-loop;
   deploy only after inspection, not as a way to evict a working process.
3. Remove non-ASUS defaults and DesignWins allowlist entries. Derive actual
   URL/model from the current policy at request time, including late attachment.
   On ASUS2 outage return advisory unavailable; do not route to DGX/cloud.
   Generic explicit policies remain configurable; installed config must match
   the reviewed policy. This is application routing, not a network firewall;
   HTTP redirects/proxies and direct investigation accounting require separate
   egress/governance work if strict transport enforcement is required.

## Intended single-authority arrangement (NOT deployed)

Keep the currently working observer unchanged until host evidence is collected.
Designate exactly ONE full Director run loop and canonical workspace as the
state-writing authority. Prefer preserving the working user service and its
history for initial cutover if host receipts confirm it owns the useful state;
choose the system service only after a separately reviewed migration. Do not
select /var/lib state merely because the deployment document names it.

All GitHub task mutation must feed that canonical authority. Avoid an external
ingest writer running alongside built-in ingest without proving serialization;
queue fetched evidence for the authority or disable one ingest schedule during
approved cutover. Derived briefs should be rendered by the authority. Necessary
observation/mirroring remains enabled, but a complementary process must read
source evidence and publish into a separate inbox/mirror, with no canonical
DB/task/brief writes or dispatch capability. Do not call a full CLI loop passive.

## Read-only host evidence required before cutover

Record UTC observation time and host identity. For BOTH system and appropriate
user manager obtain FragmentPath, DropInPaths, User, Group, WorkingDirectory,
MainPID, ActiveState, SubState, ExecMainStartTimestamp, NRestarts, Restart,
RestartUSec, TriggeredBy/Triggers and unit/timer schedules. Do not print full
Environment, credential files, raw script contents or unredacted ExecStart:
these may expose old inline secrets. Inspect locally and redact credentials.
Capture sanitized launch arguments and script SHA/mode/owner, resolved release
symlink/commit, workspace/config paths, process UID/cgroup/open DB files.

Inspect `list-timers --all` in both managers for ingest, brief, mirror and health;
record last/next activation, OnCalendar/monotonic settings, Persistent, timezone
and exact service associations. Review sanitized journal tails for successful
cycles and failures, not merely ActiveState.

Resolve DB/output paths to devices/inodes and realpaths. Inspect each DB using
SQLite URI mode=ro and a consistent read transaction; do not invoke CLI verify
on production merely to collect read-only evidence. Compute chain verification
on a SQLite backup obtained without changing source schema. Count tasks,
task_events, journal/investigation rows and last run, inventory pending outbox
and approvals without exposing secrets. Distinct DBs may contain divergent
history; do not concatenate hash chains or replay task events into production.

## Observable acceptance and reversible cutover requirements

Before any change: pin candidate commit and config digests; validate launch
permissions as the actual service UID (including parent-directory traversal).
Back up both workspaces with SQLite backup API plus all ancillary DBs/output
files, configuration/unit metadata, owner/mode and pending delivery evidence.
Test restore and chain verification on copies. Preserve originals intact.

Require separate operational authorization for service disable/enable/restart,
root-service replacement or model/credential deployment. Credentials are NOT
changed by this PR. Do not use the historical rotation approval as a new grant.

At approved boundary: quiesce the redundant writers/timers deliberately, confirm
no in-flight dispatch, snapshot again, select the authoritative history and
retain the other as a read-only archive. If histories diverge, produce a separate
source-stamped reconciliation plan; never overwrite or silently merge them.
Start only the selected writer at pinned revision/config. Preserve source
observers/mirror timers after their write boundaries are verified.

Acceptance: one intended run-loop PID; all writes resolve to the chosen state;
other observers do not open canonical DB read-write; chain remains valid across
at least two unattended cycles and a separately approved restart; repeated
GitHub issue ingest leaves task count/epoch/history stable; pending outbox is
recovered without duplicate delivery. ASUS2 outage yields unavailable, ingestion
continues, zero attempted model calls to DGX/TP4/subscriptions. Verify timer next
activation after failure and manager restart; a broken monotonic schedule must
be replaced only using reviewed installed unit evidence. No production outage
or kill test is authorized by this document.

Rollback: stop only the newly activated writer under the approved window,
restore prior code/config/unit metadata and reactivate the previously working
writer with its preserved history. Do not restore an old DB over new accepted
tasks: reconcile post-cutover events/dispatch receipts before any state rollback.
Do not restart GCI or undo the successful credential rotation as part of rollback.

## Test receipt

Full baseline SHA: `76997b2bca6231939b9b2848bf45962fa89f4c15`.
Python 3.12, pytest 9.1.1. `tests/seven_offline_guard.py` denies socket connects;
load it explicitly so no test can contact private model endpoints:

```
PYTHONPATH=tests:. python -m pytest -p seven_offline_guard -q tests/test_seven_authority.py tests/test_director_investigate.py
PYTHONPATH=tests:. python -m pytest -p seven_offline_guard -q tests/test_seven_authority.py tests/test_director*.py
```

Focused: **23 passed** (six new authority tests plus 17 existing investigation
tests). Broader candidate: **351 passed, 8 failed**. Same offline baseline:
**345 passed, 8 failed**, with identical failing node IDs:

- test_director_01a.py::test_backfill_suppression
- test_director_01b.py::test_consumer_level_idempotency_after_truncation
- test_director_02.py::test_llm_total_outage_with_policy_ingestion_continues
- test_director_02.py::test_service_down_machine_online_distinction
- test_director_02.py::test_fleet_events_flow_through_rules
- test_director_03.py::test_total_model_outage_including_subscription
- test_director_consumer.py::test_sam_reserved_generates_escalation_draft_only
- test_director_consumer.py::test_model_assessment_never_fact

The suite is not green; these baseline failures are not repaired by this audit.
Some fixtures hardcode October 16 freshness. Both runs also warn that the
asyncio_mode option has no plugin in this runtime. The unchanged base explicitly
reproduced alternating journal writes as `(False, 3)`; the new regression passes.
`git diff --check` passes. No production restart/recovery acceptance was run.

An initial broad test run was rejected by automatic review because an existing
test could send fixture/event data to a private model endpoint. Subsequent
reported suite results use the network-denying plugin; live endpoint testing
remains unperformed and is not required to review this patch.
