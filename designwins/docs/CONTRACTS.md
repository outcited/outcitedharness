# CONTRACTS

**The law, in order. This document opens with `Build` because `Build` is the
product.** Everything below is a service to the funnel.

Three laws govern the whole surface:

1. **`derived` is never silent.** An inferred constraint is shown and
   confirmable. A silent derived constraint is a bug of the worst class.
2. **`cohort` is distinct parts — a type.** Not passage matches, not document
   hits. A cohort member is a candidate you could buy.
3. **`unknowns` is a first-class field.** What we could not evaluate is part of
   the answer, never an omission.

Anything that cannot be expressed in terms of serving `Build` does not belong
in this workspace.

---

## `Build` — the top of the funnel

```rust
pub struct Build {
    pub id:          BuildId,
    pub release:     ReleaseId,          // every answer is pinned

    // --- what the human said -------------------------------------------
    pub intent:      DesignIntent,       // text · image · block diagram · BOM · refs
    pub context:     DesignContext,      // industrial | automotive | consumer | medical
                                         // ta_range · isolation · rails · environment

    // --- what we made of it -------------------------------------------
    pub declared:    Vec<Requirement>,   // typed, explicit, from the human
    pub derived:     Vec<Derived>,       // INFERRED — shown + confirmable, never silent
    pub constraints: Vec<Atom>,          // hard/soft, typed, with required conditions

    // --- the answer ----------------------------------------------------
    pub cohort:      Cohort,             // DISTINCT PARTS, not passage matches
    pub explain:     Vec<Verdict>,       // PASS | NEAR_MISS | FAIL | UNKNOWN + evidence
    pub unknowns:    Vec<MissingAxis>,   // what we could not evaluate
    pub next:        Vec<Question>,      // ranked by expected cohort reduction
}
```

```rust
/// The funnel, end to end. One sentence in, a defensible cohort out.
pub trait Funnel {
    fn accept(&self, intent: DesignIntent, ctx: DesignContext) -> Result<BuildId, FunnelError>;
    fn derive(&self, build: BuildId) -> Result<Vec<Derived>, FunnelError>;   // never silent
    fn bind   (&self, build: BuildId, r: Requirement) -> Result<Vec<Atom>, FunnelError>;
    fn evaluate(&self, build: BuildId) -> Result<Build, FunnelError>;
    fn narrow (&self, build: BuildId, a: Atom) -> Result<Build, FunnelError>;
    fn ask    (&self, build: BuildId) -> Result<Vec<Question>, FunnelError>;
}
```

### Acceptance test (Week 5 gate)

> A human types one sentence about what they are building. The system returns a
> cohort, shows which constraints were derived and confirmed, names every
> exclusion with its evidence, lists what it could not evaluate, and suggests
> the next question that would narrow it most. **Every number opens to the page
> it came from.**

### Delivery gate (D7)

> Every front end renders the experience from a single response — no second
> call, no guess. Intent composer · constraint ledger · cohort explorer ·
> evidence viewer · next-question rail · coverage view · release lineage.

A response that is logically correct but omits evidence links, hides
`unknowns`, or needs assembling **fails**, even if the acceptance test passes.

---

## C1 — Vault (read-only)

```
/Volumes/M5_4TB/vault/{landing,cas}
```

Join on `sha256`. The tapes team owns crawl, hash, store, and the arrival feed.
This workspace **never writes to the vault** and never trusts a filename — only
a content hash.

```rust
pub struct VaultRef { pub sha256: Sha256, pub cas_path: CasPath }
```

---

## C2 — Substrate JSON

What the extractor is handed. Nothing is inferred here; everything is observed.

```jsonc
{
  "sha256": "...", "filename": "...",
  "page_count": 12, "text_chars": 48211, "text_quality": 0.97,
  "needs_ocr": false,
  "producer": "...", "extractor": "pymupdf", "extractor_version": "1.28.2",
  "page_labels": ["1","2","3", ...],
  "pages": [ { "page": 1, "label": "1", "text": "..." }, ... ]
}
```

`extractor` + `extractor_version` are mandatory: the D1 parity gate compares
engines, so the engine identity is part of the artifact.

---

## C3 — Claim

A claim is **unconstructible without its evidence**. See `Admitted` and
`Verbatim` in `dw-types`; there is a compile-fail test for this.

```jsonc
{
  "value": 600.0,
  "quote": "Drain - Source voltage  VDSS  600  V",
  "table": "Absolute maximum ratings (Ta = 25°C)",
  "source": { "sha256": "...", "page": 2,
              "row_header": "Drain - Source voltage",
              "column_header": "Value" },
  "condition_verbatim": "Ta = 25°C ,unless otherwise specified",
  "conditions_typed":   { "ambient_c": { "op": "eq", "value": 25.0 } }
}
```

---

## C4 — `catalog.db`

Tables stay as-is. **Rust becomes the writer at cutover, not before.**

`parts` · `part_sources` · `claims_canonical` · `consensus` · `doc_revisions`

---

## C5 — `pipeline.db`

Format stays.

`jobs` · `results` · `adjudication_ledger` · `budget_spend`

---

## C6 — Vocabularies

Frozen, additive-only. An unknown major version is a hard reject.

| vocabulary | contents |
|---|---|
| `condition-model.v1` | condition keys |
| `curve_type` | frozen, additive-only |
| `atom_class` | `curve` \| `thermal` \| `errata` |
| `rating` | `absmax` \| `recommended` \| `rated_limit` \| `typical` \| `transient` \| `surge` \| `unknown` |

Every artifact carries `schema_version`.

---

## C7 — API: a rendering substrate (D7)

Not resource endpoints a UI assembles into an experience. **Every response
carries what a front end needs to render, inline.**

```
GET  /healthz
GET  /api/extraction/v1/parts/{part_number}
GET  /api/discovery/v1/designs/{design_id}
GET  /api/pipeline/v1/jobs/{job_id}
POST /api/search/v1/query
GET  /api/coverage/v1/cube
GET  /api/coverage/v1/gaps
```

- auth: `Bearer crk_…`
- errors: `application/problem+json`, **also renderable** — a failed request
  still tells a human what happened and what to do
- `part_number` is a 1:1 facade join
- **every response carries `ReleaseId`**, and it must be renderable, not merely
  present

### Per-response feed contract (D7)

| # | required inline | serves surface |
|---|---|---|
| 1 | `release_id` + lineage affordance | release / lineage |
| 2 | evidence refs resolving to page render + row-band crop + `crop_sha256` **without another call** | evidence viewer |
| 3 | `unknowns: Vec<MissingAxis>` — never inferred from a missing field | constraint ledger, coverage |
| 4 | per-exclusion reason + evidence ref on every `Ineligible` | cohort explorer |
| 5 | ranked `next` questions with expected reduction + help prose | next-question rail |
| 6 | `derived` vs `declared` distinction preserved | constraint ledger |
| 7 | `cohort` as distinct parts with per-part verdict | cohort explorer |

```rust
pub struct Query {
    pub raw:       String,
    pub intent:    Option<DesignIntent>,
    pub filters:   Vec<Atom>,
    pub limit:     u32,
    pub release:   ReleaseId,
}

pub struct QueryResult {
    pub release_id:   ReleaseId,
    pub cohort:       Cohort,
    pub explain:      Vec<Verdict>,
    pub unknowns:     Vec<MissingAxis>,
    pub next:         Vec<Question>,
    pub evidence:     Vec<EvidenceRef>,
}
```

---

## C8 — Training pairs

Quote-native, byte-reproducible.

```jsonc
{ "value": ..., "quote": "...", "table": "...",
  "condition": "...", "sha256": "...", "page": 2 }
```

v1 (2,713) and v2 (87K) shapes must reproduce **byte-compatible**. The
generator lives in `dw-learn`; the gradient step is Python behind D3's
subprocess boundary.

---

## C9 — Elimination law

The law that makes a cohort defensible.

1. `Unknown` **never** eliminates.
2. `AbsMax` **never** satisfies an operating atom.
3. Margin is never silent — `margin_pct` is explicit and stored.
4. No interpolation outside an established domain.
5. `typical` never becomes guaranteed.
6. A condition must **COVER** the operating point.
7. A cohort counts **distinct candidates**.

```rust
pub enum Verdict {
    Pass,        // satisfies the atom under its required conditions
    NearMiss,    // fails by less than the stated margin
    Fail,        // eliminated — always carries reason + evidence
    Unknown,     // could not evaluate — NEVER eliminates
}
```

---

## C10 — Embed law (C6 ruling)

One embedder fleet-wide. `WeightFingerprint`-pinned vectors. ≤4 concurrent.
**Never mix generations. A second embedder is unrepresentable.**

```rust
pub struct WeightFingerprint { /* pinned hash of model weights */ }
pub struct Embedding { pub vector: Vec<f32>, pub fingerprint: WeightFingerprint }
```

---

## C11 — Absence law

`Option` is **banned for facts**. Absence is a fact and has three distinct
values.

```rust
pub enum Observed<T> {
    Present(T),
    NotObserved,     // we looked; it was not there
    NotApplicable,   // this axis does not apply to this object
}
```

`NotObserved != NotApplicable != None`.

---

## C12 — Conflict law

`Conflict` is sticky. There is **no promotion path** to `Validated`. Enforced
by the type system, with a compile-fail test.

---

## C13 — Provenance law

An evidence snapshot is tamper-evident and leak-free.

```
page render      -> content-addressed, 144 DPI
row-band crop    -> crop_sha256
```

Every number a front end shows resolves to one of these. No exceptions.

```rust
pub struct SourceRef {
    pub sha256:   Sha256,
    pub page:     u32,
    pub crop:     Option<CropRef>,      // row-band, content-addressed
}
pub struct CropRef { pub crop_sha256: Sha256, pub dpi: u32 /* 144 */ }
```

---

## Type-level enforcement

These are not conventions. They are compile errors.

| rule | mechanism |
|---|---|
| `Claim` unconstructible without `Verbatim` + `SourceRef` | sealed constructor + compile-fail test |
| `Conflict -> Validated` impossible | no such conversion exists + compile-fail test |
| `Option` banned for facts | `Observed<T>` in every fact position |
| a second embedder unrepresentable | `Embedding` carries `WeightFingerprint`; no untagged constructor |
| `Ineligible` without reason + evidence | unconstructible + compile-fail test |
| `derived` never silent | `Derived` has no `Default` and no silent coercion into `Atom` |

---

## Related

- `DECISIONS.md` — D1–D7, ratified. D7 governs C7. D1 governs C2's
  `extractor` field. D6 governs anything resembling a vendor assumption.
- `PLAN.md` §4 is the source these contracts were drawn from; if they disagree,
  this document wins for the workspace and `PLAN.md` is corrected.
