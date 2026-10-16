# CONTEXT-TRUST-AUDIT — evidence classification (§5)

## The hardening

**Verification terminology no longer promotes anything.** The old
`verified`/`PASS`/`evidence` word-heuristic is gone. Only receipt-bearing
records (sha refs, measured-state rows, `verified_cause` columns,
receipt markers) classify as `verified-fact`. Decorated documents are
`assertion` — a document describing a passing test is not a receipt for
that test.

## Retrieval

Exact references FIRST: task IDs → task records (project-filtered),
D-numbers → decisions (with supersession), PRD refs, commit SHAs →
`git show`. Term matching is fallback only. Missing references report
"missing source" in uncertainties. Decision-bound queries with no
authority source set `authority_missing` + **BLOCKED** — unsupported
conclusions are blocked by construction.

## Classes (unchanged semantics, enforced harder)

authority | verified-fact (receipts only) | assertion | hypothesis |
superseded (never current) | unknown/contradictory.

## Acceptance scenarios (tested)

1. canon change → authority decisions/config retrieved
2. supply incident → exact task record via task-ID reference
3. conflicting ownership → task/claim records, hints classed assertion
4. superseded decision → superseded class + "excluded from authority"
5. Project B unrelated vocabulary → zero cross-project material
6. unverified causes → hypothesis (suspected ≠ verified)
7. authority-free decision query → BLOCKED flag

Packets carry sections (exact_references, objectives, task_history,
implementation_refs), source locators, freshness ages, uncertainties.
