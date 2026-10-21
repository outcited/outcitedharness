# Least-privilege repository access proposal (m5-opencode, 2026-10-21)

Observed needs from this workstream; each permission is a SEPARATE token so
blast radius of any leak is one capability. No agent holds issue-write +
dispatch + production simultaneously.

| agent | contents:read | PR open/branch push | issues:write | dispatch/deploy | production/ExportStore |
|---|---|---|---|---|---|
| m5-opencode | outcitedharness (+cr-core READ once visible) | outcitedharness feature branches only | NO | NO | NO |
| seven (Director) | outcitedharness read | NO | NO | NO | NO (observability feeds only) |
| M4 FE team | fixture dirs read (repo: read) | NO | their own FE repo only | NO | NO |
| cr-core team | cr-core + fixtures read | cr-core branches | cr-core issues | NO | NO |
| Sam (owner) | all | all | all | all | all — sole promotion authority |

Rules:
1. Read access is per-repo, never org-wide (current token's read:org gap blocks
   reviewer lookup — fix by granting THIS repo's issues:write to a dedicated
   review token, not by widening org scopes).
2. Issue-write never rides the same token as dispatch or production.
3. ExportStore/live-store writes: owner-only (matches S6 HOLD).
4. Current known gaps: PR-reviewer assignment + issues:write (owner token fix,
   already documented in DIRECTOR-07 work); M4 FE / cr-core repo visibility.
