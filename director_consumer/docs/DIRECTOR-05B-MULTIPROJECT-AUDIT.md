# DIRECTOR-05B-MULTIPROJECT-AUDIT — closure (P0-5)

## Identity preservation

Migration (`scope.py`) is per-table, PK-shape-detected, transactional,
and now AUDITED (`migration_log` rows with before/after counts — never
silent). Historical rows carrying project identity survive verbatim
(tested: `T-1/alpha/owner` intact). Unattributable legacy rows (no
project column) land in `default` with an audit entry — not silently
reassigned.

## Incident identity fix (found in 05B testing)

`incident_id` now folds the project: two projects hitting the same rule
with the same subject and window get SEPARATE incidents (05 had the
collision; the mandated isolation test caught it).

## Observed-state population

`run_projects` builds per-project ObservedState: incidents and messages
are project-filtered from the stores; the continuity funnel attaches only
to the project that declares the critical path (05A shadow-caught leak,
kept fixed); fleet/machine telemetry is shared-by-design with explicit
visibility. Context packets declare `sections.visibility`: shared = git,
agentmail, kernel docs; project-private = config, tasks, incidents,
messages, approvals — filtered by project_id everywhere.

## Evidence classification (receipts, not words)

Text terminology is never verification. Docs classify `assertion`
always; `verified-fact` comes only from structured measured store rows
(task `last_verified_progress`, incident `verified_cause`). Suspected
causes stay `hypothesis`; superseded decisions never current authority;
unsupported decision queries are BLOCKED.

## Closure proof

Two projects with identical local task/incident/finding/message names,
distinct authority: independent states, findings, recovery evidence, and
approvals (tests 05B-9/10 + 05/05a suites). One project cannot influence
another's findings, acks, or authority.
