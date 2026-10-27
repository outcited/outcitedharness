# Contracts — dw.finding.v1 · dw.approval.gascity-ingest.v1

## The authorization law (inherited from director gh_workloop, now binding here)

1. **A published GitHub finding — issue body, PR comment, review, or any other
   GitHub content — is an UNTRUSTED work definition. It is NEVER an execution
   authorization.** No comment thread, label, reaction, or TaskStore state
   upgrades one.
2. The only bridge from finding → Gas City bead is `handoff.submit`, which
   requires an approval artifact matching `dw.approval.gascity-ingest.v1`:
   - `issuer: sam` (the human owner — no agent may issue),
   - `action: gascity-ingest` (bead creation ONLY; sling/dispatch/merge/deploy
     are outside this artifact's power and remain manual),
   - `recipient` = exact city, `task_ref` = the finding's task_id,
   - `payload_digest` = sha256 of the exact finding body,
   - `expires_at` + `max_actions: 1` (one use, then dead).
3. Findings with `authorization_state != "authorized"` are refused pre-API
   (unit-tested: `test_proposal_finding_never_dispatched`).
4. Acceptance is NOT the POST response. Acceptance = independent read-back of
   the persisted bead + city event-log seq, recorded in a
   `dw.gascity-acceptance.v1` receipt.
5. Production artifacts live in the approvals directory Sam controls
   (convention: seven `/etc/harness-director/approvals/` or a Sam-owned
   equivalent). Demo artifacts must name their authorizing directive in
   `authorization_ref` and target a test city only.

## Validation entry points

- `bridge.finding.validate()` enforces dw.finding.v1 (allowlisted repos,
  TASK_ID_RE compatibility, positive AND negative regression tests required).
- `bridge.handoff.check_authorization()` enforces
  dw.approval.gascity-ingest.v1 (all six refusal reasons unit-tested).
- Both schemas are self-contained JSON Schema 2020-12 documents; a GitHub
  issue template for findings ships in `demo/DW-NEO-DEMO-01.finding.json`.
