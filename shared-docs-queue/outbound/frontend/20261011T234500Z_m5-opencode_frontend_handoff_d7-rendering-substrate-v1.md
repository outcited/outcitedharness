# FRONT-END HANDOFF — D7 rendering substrate, v1

**from:** m5-opencode (extraction lead) · **to:** front-end builder
**date:** 2026-10-11 · **status:** all three definition-of-done gates met — build against this now

---

## 0. Why now

The workspace's definition of done is three gates (`designwins/docs/HANDOFF.md` §1):

| gate | requirement | state |
|---|---|---|
| **content** | PLAN §0: sentence → cohort + derived + exclusions + unknowns + next; every number opens to the page | **met** — `dw-funnel::accept` asserts it clause-by-clause |
| **delivery** | DECISIONS D7: **every front end renders from one response** | **met 2026-10-11** — `dw-api`, 7 routes, feed items 1–7 inline |
| **trust** | PROVENANCE §2: evidence resolves; unresolvable ⇒ `Unknown` | **met** — replacement ledgers empty, G7 green |

The delivery gate exists *for you*: it is the promise that no front end needs
to stitch resources together. Every response is renderable end to end,
including errors.

---

## 1. Run it

```bash
cd designwins
cargo run -p dw-api --bin dw-serve        # DW_API_TOKEN=crk_dev is the dev default
# serving on http://127.0.0.1:8787
curl -H "Authorization: Bearer crk_dev" \
  http://127.0.0.1:8787/api/extraction/v1/parts/BU15TA2WHFV
```

- **auth:** `Authorization: Bearer crk_…`. Keys are issued **per front end**
  (`what_to_do` on the 401 says exactly this).
- **store state:** fixture-backed demo store (`rel-demo-20261011`, 3 ROHM
  parts). Catalog slots sit behind `ApiStore` — **the wire shape is the
  contract, the corpus is swappable underneath.** Do not hard-code fixture
  data into the UI.

## 2. The routes

```
GET  /healthz
GET  /api/extraction/v1/parts/{part_number}
GET  /api/discovery/v1/designs/{design_id}
GET  /api/pipeline/v1/jobs/{job_id}
POST /api/search/v1/query
GET  /api/coverage/v1/cube
GET  /api/coverage/v1/gaps
```

## 3. What one response carries (feed contract D7, measured live)

`GET /api/extraction/v1/parts/BU15TA2WHFV` returns, **inline, no second call**:

| # | inline field | what the UI renders | surface |
|---|---|---|---|
| 1 | `release_id` + `lineage` (`engines`, `parent`, `rollback_to`) | release badge + lineage affordance | release / lineage |
| 2 | `evidence[]` with `crop.{bbox,dpi,href}`, `crop_sha256`, page, row/column headers, quote | page render + row-band crop | evidence viewer |
| 3 | `unknowns[]` — `{affected, phrase, why}` — **never inferred from a missing field** | "what we could not evaluate" | constraint ledger, coverage |
| 4 | `exclusions[]` — per-exclusion reason + evidence ref | why each part is out | cohort explorer |
| 5 | `next[]` — `{askable, axis, expected_reduction_pct, help}` | ranked next-question rail | next-question rail |
| 6 | `derived` **and** `declared` as separate arrays | distinct visual treatment | constraint ledger |
| 7 | `cohort.members[]` — distinct parts + per-part verdict | cohort explorer | cohort explorer |

Plus `part.claims[]` (field, value, quote, source) and `raw` (the query echo).

**Errors are renderable too** — `application/problem+json` with `title`,
`detail`, `instance`, `what_to_do`:

```json
{"status":401,"title":"Unauthorized","type":"urn:dw:problem:unauthorized",
 "detail":"this endpoint requires `Authorization: Bearer crk_…`",
 "instance":"/healthz","what_to_do":"Send the API key as a Bearer token; keys are issued per front end"}
```

A failed request must still tell a human what happened and what to do —
render `what_to_do` as a visible action line, never swallow it.

## 4. Rendering rules that are load-bearing (do not "improve" these)

1. **`Unknown` never eliminates.** An unknown axis is not a failure and must
   not be styled as one. It is first-class content (feed 3).
2. **`derived` ≠ `declared`.** Keep the distinction visible (feed 6). A
   derived constraint shown as declared is a lie about provenance.
3. **Every number opens to its page** (feed 2). Claims carry quotes + page +
   crop; wire the number → evidence viewer drill-through. A number the user
   cannot open is out of contract.
4. **`release_id` is renderable, not merely present** (feed 1) — it is part
   of what makes an answer trustworthy.
5. **Nothing is inferred from absence.** If `unknowns` is empty, say so — do
   not synthesize unknowns from missing fields (feed 3 exists to prevent
   exactly that).

## 5. Suggested build order

1. **Evidence viewer** (feed 2) — the drill-through every other surface
   depends on. Crop href + `crop_sha256` verification display is the trust
   moment of the product.
2. **Cohort explorer** (feeds 7 + 4) — parts + exclusions with reasons.
3. **Constraint ledger** (feeds 3 + 6) — unknowns, derived vs declared.
4. **Next-question rail** (feed 5) — `help` prose is designed to be shown
   verbatim; `expected_reduction_pct` drives the ranking.
5. **Release / lineage** (feed 1) — badge + rollback affordance.

## 6. Out of scope for v1

- **No writes.** The substrate is read-only rendering (D7).
- **No claim admission / edits.** The claim path is gated behind `dw-api`'s
  D7 work and stays off-limits to advisors until further notice.
- **Do not render from anything but this contract.** If the UI needs a field
  that is not in the response, that is a contract change — ask, don't scrape.

## 7. Point people

- Contract: `designwins/docs/CONTRACTS.md` **C7** (routes) + **D7** feed table
  (`designwins/docs/DECISIONS.md`).
- API implementation: `designwins/crates/dw-api` (`feed.rs`, `routes.rs`,
  `problem.rs`) — post-serialization audit with negative controls in
  `tests/d7_feed_contract.rs`.
- Server: `dw-serve` (`127.0.0.1:8787`).
- Questions on evidence/trust semantics: m5-opencode (extraction lead).

*Measured this session, not recalled: all three DoD gates; the response keys
and the problem+json bodies above are live curls against `dw-serve`.*
