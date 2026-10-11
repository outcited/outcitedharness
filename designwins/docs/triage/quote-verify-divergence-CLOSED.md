# CLOSED — `quote_verify` divergence, 2026-10-11

Incoming lead, after the fix in `dw-truth/src/quote.rs`. The two JSON files in
this directory are the **pre-fix** ledger; both diverging cases now agree.

```
python:harness.pipeline.quote_verify.verify_claims
  vs rust:dw_truth::verify_claims
  subjects  40   agreements  40   discrepancies  0   cutover_permitted yes
```

Root cause, triage outcomes, killed suspects, and latent (non-firing)
divergences live in `HANDOFF.md` §5.1 — single source, not duplicated here.

Triage-file key so the ledgers stay readable:

- `quote-verify-divergence.json` — post-`value_as_f64` fix, 170 field-level
  diffs across 2 of 3 cases. This is the ledger the fix closed to 0. It is
  the **only** ledger that exists: `ANSWERS-TO-INCOMING-LEAD.md` claims a
  stale `quote-verify-divergence-3cases.json` was kept alongside it — that
  file is not on disk. The "221 diffs" era has no ledger.
- `ANSWERS-TO-INCOMING-LEAD.md` — outgoing lead's answers. Two claims in it
  were wrong and are corrected in HANDOFF §5.1: the `norm(quote)[:40]` prefix
  collision is reference-shared (not a porting bug), and the
  context-dependence observation is dead (483/483 isolated runs match
  full-map).

The negative controls for this class are
`multibyte_window_edge_does_not_widen_span` and
`token_window_edge_does_not_widen_span` in `dw-truth/src/quote.rs`.
