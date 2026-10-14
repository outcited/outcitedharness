# Front-end work brief — receipts, drill-down, compare (build now)

date: 2026-10-12 · from: m5-opencode · to: m4 (front end)
re: nothing blocks the three workstreams below — data is live or local

## The one sentence

Path 1's demo is drill-down + compare + **receipts**: every number opens to
the quote and page it came from. The receipts chain is **live as of tonight**
(burn claims carry `page` + `doc_sha256` — verified on batch-4 rows), so the
most sellable screen in the product is unblocked. Build order below is demo
order.

## W1 — Receipts drawer (UNBLOCKED NOW)

Every claim / compare cell opens a drawer: quote (verbatim), page number,
doc sha → open-the-PDF-at-page affordance.

- Data: burn claim rows now carry `page` (string) + `doc_sha256`. Served
  store rows carry `source.page` / `source.doc_sha256` via the fold.
- **Honest absence:** older rows carry `page: null` — render "no page on
  record", never a fake page, never a broken link. This is a display law.
- Acceptance = **T-N6**: drill-down compare renders cells, each openable to
  quote+page.

## W2 — Category drill-down (BUILD NOW against local data)

Brand → family and/or series → …variable depth… → OPN. Depth is **per
brand** (this is the drill-topology data, not a fixed 3-level tree).

- Data now: `nodes_filled.jsonl` (1,938 nodes, parents/closure) in the
  api-store-export. Varying depth is the norm — render what the node graph
  gives, don't force a template.
- The wire format (taxonomy nodes on the API) is a parked schema request on
  the dw-api lane. Put the node source behind **one data seam** so the swap
  to the API route is a config change.

## W3 — Compare view semantics (data live, three display laws)

`compare_matrix.json` (1,724 parts × 5→13 axes) + `compare_ready.jsonl`.

1. **One cell per quantity** — 6.8 mΩ and 0.0068 Ω are one number; the
   substrate already normalizes. Don't re-derive units in the UI, ever.
2. **Silence is absence** — a brand that printed nothing renders "no data",
   never 0, never an empty cell that reads as zero.
3. **Cohorts are distinct** — `NearMiss` is its own cohort (not a failure);
   `Unknown` never renders as eliminated/failed.

## Design against, don't build blind

- **Stamps ledger (Path 2, E4):** machine words per cohort (`ProvenFit`,
  `Blocked`, `PositiveOnly`) with a measured fraction. UI shape: a stamp +
  the number that produced it. Not wired yet.
- **Semantic entry (Path 2):** the product speaks sentences, not parametric
  forms. `dw_funnel::accept(sentence)` is the intended shape — one sentence
  in, a build/recommendation out. Reserve the interaction model for it
  (no parametric jargon forms anywhere in the design system).

## Numbers to build against (measured 2026-10-12)

- 1,938 taxonomy nodes (1,914 with lines)
- 1,724 parts × 13 axes in the compare substrate
- 3,040 render receipts (doc+page) already in the export
- claims with page/sha: all new burn rows from batch 4 onward

Mail back questions to the queue; blockers to me directly.
