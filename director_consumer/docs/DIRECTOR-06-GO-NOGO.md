# DIRECTOR-06-GO-NOGO — conditions for the first live coordination

## GO conditions (all met by 05A)

- [x] one real process operates two projects with isolated state
- [x] restart/kill/backup/restore leave no duplicate actions
- [x] authorization is file-backed, scope-checked, digest-bound,
      replay-proof; delivery paths cannot bypass it
- [x] acknowledgments require attributable senders
- [x] context packets receipt-classified; superseded never current;
      unsupported conclusions BLOCKED
- [x] reconciler reads real stores; missing work is detected without
      error reports; no-action reasons recorded
- [x] economic law enforced (metered refusal, no fallback, attributable)
- [x] deterministic operation with zero LLM endpoints

## Remaining NO-GO gates (small, named)

1. **Operator approval rehearsal** — Sam writes one real approval file and
   the adversarial suite passes against it on the production workspace
   copy (5 minutes).
2. **Recipient confirmation** — `agent:m5-opencode` mailbox identity is
   the current accountable owner of SUPPLY-RECON-01 per the ownership
   reconciler (it may no longer be on M5; re-resolve first).
3. **Delivery enablement** — flip `delivery.enabled` + authorization_ref
   for exactly one message thread; everything else stays denied.

## Verdict: **CONDITIONAL GO**

The platform is ready for the one-thread experiment described in
DIRECTOR-05/06 recommendations once the three gates above are signed.
Nothing else should be enabled in the first week. Success = one delivered,
attributably-acked, evidence-verified recovery loop with zero duplicates
and zero unauthorized attempts in the audit trail.
