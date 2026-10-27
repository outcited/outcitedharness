# Contracts — dw.finding.v1 · dw.approval.gascity-ingest.v1

## The authorization law (inherited from director gh_workloop, now binding here)

1. **A published GitHub finding — issue body, PR comment, review, or any other
   GitHub content — is an UNTRUSTED work definition. It is NEVER an execution
   authorization.** No comment thread, label, reaction, or TaskStore state
   upgrades one. A finding cannot authorize itself.
2. **An `issuer` string proves nothing.** Trust comes from PLACEMENT: an
   artifact is trusted only inside a registered trust root that is not
   group/world-writable (production: a Sam-controlled directory, e.g. the
   convention on seven `/etc/harness-director/approvals/`; staging: this
   repo's `demo/trust-staging/` fixture root). Artifacts outside a root
   refuse (`untrusted-path`); writable roots refuse (`untrusted-trust-dir`).
3. **Environment separation.** `environment: production` artifacts
   (`issuer: sam`) target production cities only; `environment: staging`
   artifacts (`issuer: staging-fixture`) target staging cities only. Cross
   targets refuse (`environment-mismatch`).
4. Every field is enforced: `project` const, `action: gascity-ingest` (bead
   creation ONLY — sling/dispatch/merge/deploy are outside this artifact's
   power and remain manual), `recipient` = exact city, `task_ref` = the
   finding's task_id, `expires_at`, `max_actions` = exactly 1,
   `spend_scope: none`, `max_spend_usd: 0`.
5. **`payload_digest` is mandatory** and must equal the sha256 of the exact
   finding body. Omission refuses (`invalid-approval`); mismatch refuses
   (`digest-mismatch`). This binds one artifact to one finding body.
6. **Single use, durably accounted.** Every acceptance appends to a
   replay-safe approval ledger (`approval-ledger.jsonl`, append + flock +
   fsync). Replaying the same artifact with the same finding returns the
   prior receipt with NO new API call; reuse against a different finding
   refuses (`approval-reused`). If the ledger is lost, the label pre-check
   still prevents a second bead.
7. Findings with `authorization_state != "authorized"` are refused pre-API.
8. **Acceptance is proof-gated, never assumed:**
   - `accepted` (`stage: BEAD_ACCEPTED`) requires independent read-back
     matching labels + metadata digest + task-id title, AND a matching
     `bead.created` event (`payload.bead.id` == bead id, `neo-finding:` label).
   - `accepted-persisted` (`BEAD_ACCEPTED_PERSISTED_ONLY`) — read-back
     verified, creation event not found in the scan window: explicitly weaker.
   - `unverified` — bead may exist but proof failed (read-back missing,
     digest mismatch, HTTP error): NEVER accepted. Fail-closed.
   - The POST response alone is never a receipt.
9. **Stages are distinguished:** a receipt proves only BEAD_ACCEPTED /
   BEAD_ACCEPTED_PERSISTED_ONLY. `AGENT_RUNNING` (bead in_progress or
   assigned) and `WORK_VERIFIED` (bead closed + verification receipt with
   PR + passed CI) are derived downstream by `neo_status.verification_stage`
   — the handoff never asserts them.

## Validation entry points

- `bridge.finding.validate()` enforces dw.finding.v1 (allowlisted repos,
  TASK_ID_RE compatibility, positive AND negative regression tests required).
- `bridge.handoff.load_approval(path, trust_roots)` + `check_authorization()`
  enforce dw.approval.gascity-ingest.v1 (14 refusal reasons unit-tested).
- Negative controls: `tests/test_bridge.py` (authenticity/proof),
  `tests/test_publisher.py` (stability/sanitization/checkout guard).
