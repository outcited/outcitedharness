# PR #20 Independent Review Checklist — head e1620a7e (spine impl a7170204)

Reviewer: ________________  Date: ________  Verdict: [ ] APPROVE [ ] REQUEST CHANGES

- [ ] **Source attribution** — reader_v2.bind_row: structure-first (reader page primary when fragment verifiably present); unique corrections recorded; repeated values/headings AMBIGUOUS → held. No adjacent-page acceptance anywhere.
- [ ] **Value/unit rules** — spine.value_in_evidence_family: rule-ids (mem-1024/si-1000/int-comma/hyphen-join/k-prefix-exact-1024); 1024-family ONLY for memory fields; k-prefix matches ONLY exact binary factorization (2050≠2K stays held); manufacturer text/value_raw never mutated.
- [ ] **Identity** — identity_anchor: content SHA (L2-recomputed) is identity; manifest URL stems are retrieval hints; no family→exact conversion path.
- [ ] **Blocklist enforcement** — L3 excludes + counts blocked; L4/L5 never see blocked rows; staging gate refuses; corrupt blocklist fails closed ("*"). Negative tests executed in-repo.
- [ ] **Regression** — 400/400 previously-verified re-verified; deterministic multiset hashes (pilot + shard rerun identical).
- [ ] **Reconciliation** — 4 buckets pairwise-disjoint, union == 50,259 exactly (reconciliation-independent.json).

Evidence: CP100-MANIFEST.json sha 81db49cfac737f45; dispositions/*.ids; test commands:
  .venv/bin/python tests/test_spine.py   (43 gates)
  blocklist enforcement chain: see commit f3e43432 message + wave2_checkpoint driver

DO NOT MERGE until this checklist is APPROVED by an independent reviewer (Sam's gate).
