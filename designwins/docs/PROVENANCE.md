# PROVENANCE — the trust model, honestly assessed

**Status:** planning · **Written:** 2026-10-10, before handoff · **Supersedes:** nothing. This is the first time the trust model has been stated against what is actually built.

The question this document answers:

> What needs to happen so an answer is **believable**, in the most reliable
> and provenance-first way, for our use case — an engineer staking a design on
> a cohort of parts?

---

## 1. Our use case, stated plainly

An engineer types *"an outdoor weather thermostat with bluetooth"*. They get a
cohort. They spend money and schedule on the parts in it.

If the cohort is wrong, they find out at bring-up or in the field. So the
product is not "a good answer." The product is **an answer whose every number
can be opened, checked, and re-derived** — including by someone who does not
trust us.

That is the standard. Not accuracy. **Accountability.**

---

## 2. The trust chain

Each link must hold or the ones after it are worthless.

| # | claim the answer makes | how it is enforced | state |
|---|---|---|---|
| 1 | the number is what the document printed | `dw-truth::quote` — quote locates, value parses from quote | **real** |
| 2 | it is the *kind* of number that can answer this | `dw-normalize::rating` — absmax never satisfies operating | **real** |
| 3 | it holds *at my operating point* | `dw-normalize::coverage` — condition must COVER | **real** |
| 4 | I can **see** it on the page | `C13` page render + row-band crop | **TYPED, NOT REAL** |
| 5 | I can reproduce this answer | `ReleaseId` → immutable manifest | **TYPED, NOT REAL** |
| 6 | nothing was silently assumed | `derived` is never silent · `unknowns` is first-class | **real** |
| 7 | the port did not drift | gold gates + `dw-parity` ledger | **real, partial** |
| 8 | the colloquial mapping is right | `Stamp` measured against the floor | **real, unclosed** |

**Links 4 and 5 are the gap.** And they are the two that turn a correct
answer into a *defensible* one.

---

## 3. The gap, precisely *(closed 2026-10-10 — kept so the reasoning is visible)*

> **This section describes a gap that has since been closed.** `dw-evidence` and
> `dw-release` landed on 2026-10-10 and `dw-decide::judge` now refuses an answer
> whose evidence does not resolve. It is kept verbatim because *why* the gap was
> dangerous is the useful part, and because §6 explains what the work taught us.
> The live state is §2 and §9.

### 3.1 A `SourceRef` today is a number with no picture

```rust
pub struct SourceRef {
    pub sha256: Sha256,      // resolves to the vault (C1)  ✓
    pub page: u32,           // a page number               ✓
    pub crop: Option<CropRef>, // { crop_sha256, dpi, bbox } ✓ typed
}
```

Nothing in the workspace **renders** a page. Nothing **crops** a row band.
Nothing **serves** either. So `crop_sha256` is a field that can never be
populated except by hand, and "every number opens to the page it came from" is
a type signature.

This is the exact failure mode the plan names as the worst class — **silent
wrongness** — wearing a different hat. A provenance field that is present but
unresolvable is worse than one that is absent, because it *looks* like
accountability.

### 3.2 `ReleaseId` today is a string

Every response carries one (C7). Nothing resolves it. Two runs can both say
`designwins-2026-W46` and disagree. §16.2 ("no duplicate truth across
machines") and §16.4 ("no lost lineage, append-only releases") are asserted by
a `String` newtype.

---

## 4. What "resolvable provenance" means

### 4.1 Evidence resolution (C13)

The property that matters is not "we store an image." It is:

> **Provenance breaks loudly, not silently.**

If the pixels change, the hash changes, and the claim becomes *unverifiable* —
not "probably still fine."

```
sha256  --(vault CAS, read-only)-->  PDF
                                  ->  render page @ 144 DPI   -> page_render_sha256
                                  ->  crop row band           -> crop_sha256
```

Design rules, each from a law already ratified:

| rule | source | why |
|---|---|---|
| content-addressed, never by filename | `C1` | a filename is not evidence |
| render at **144 DPI**, the resolution `C13` pins | `C13` | the crop hash is meaningless without a fixed raster |
| crop hash is over the **crop bytes** | `C13` | so a re-render under different settings breaks visibly |
| renderer is **engine-pinned** and recorded | `C2` (`extractor` + `extractor_version`) | a different engine produces different pixels; the hash says so |
| store locally, never on the NAS | §10 | the NAS unmounts — measured, twice, this session |
| served inline in the response | `D7` | a second call is a chance to get a different answer |

**API shape** (this is what makes D7 true rather than asserted):

```
GET /api/evidence/v1/doc/{sha256}/page/{n}      -> page render (PNG, 144 DPI)
GET /api/evidence/v1/crop/{crop_sha256}         -> row-band crop (PNG)
GET /api/extraction/v1/parts/{part_number}      -> claims, each with both refs inline
```

A claim whose `crop_sha256` does not resolve is **`Unknown`**, not `Pass`.
That is `C9.1` applied to provenance itself.

### 4.2 Release manifest (§16.2, §16.4)

```rust
struct ReleaseManifest {
    release_id:      ReleaseId,
    previous:        Option<ReleaseId>,   // append-only lineage
    corpus_freeze:   Sha256,              // which inputs (freeze_corpus.py)
    vocab_versions:  BTreeMap<String, String>,  // condition-model.v1, curve_type, rating
    embedder:        Option<WeightFingerprint>, // C10
    engine:          EngineId,             // C2: name + version
    code_version:    String,               // git sha
    created_at:      Timestamp,
}
```

Immutable. Content-addressed. The `ReleaseId` in a response **resolves** to
this, and the manifest is what makes "reproducible" a fact instead of a claim.

Without it, the parity ledger (D2) has no fixed point to compare against.

---

## 5. Reordered critical path — trust first

The original ordering optimised for completing the acceptance *sentence*. That
was the wrong objective: the sentence has eight clauses and two of them are the
trust links. Building clause 6 before clause 4 produces a UI that looks
accountable and is not.

### Tier 1 — an answer can be believed. **DONE 2026-10-10.**

| # | work | unlocks | landed in |
|---|---|---|---|
| 1.1 | **Evidence resolution**: page render @ 144 DPI + row-band crop, content-addressed, engine-pinned | "every number opens to the page" — link 4 | `crates/dw-evidence`, `dw_pdf::render_rgba`, `RenderedPage::crop` |
| 1.2 | **Release manifest**: `ReleaseId` resolves to an immutable manifest | "I can reproduce this" — link 5 | `crates/dw-release` |
| 1.3 | **Unresolvable ⇒ `Unknown`** in the evaluator | provenance failure is an elimination *refusal*, never a silent pass | `dw_decide::judge`, via the `Provenance` trait |

Do **not** rebuild these. They are tested and green.

### Tier 2 — an answer can be relied on at scale

| # | work | unlocks |
|---|---|---|
| 2.1 | **Gate N4: the 87K claim diff** | proof the port did not drift. **Blocked on corpus location — see §7** |
| 2.2 | **Phrase-layer gate** against the query telemetry (1,485 rows / 8,653 searches, count-weighted) | proof the colloquial mapping is right, not just typed |

### Tier 3 — an answer is useful

| # | work | unlocks |
|---|---|---|
| 3.1 | **Question ranking** — port `next_question` (verified over 1,881 parts / 86,736 claims) | "the next question that would narrow it most" |
| 3.2 | **`dw-search`** | the front door |
| 3.3 | **`dw-coverage`** | "lists what it could not evaluate" at corpus scale |

### Tier 4 — it runs

`dw-structure` / `dw-recon` / `dw-graph` · `dw-extract` / `dw-queue` ·
`dw-model` / `dw-learn` · `dw-api` (D7 delivery gate) · `dw-controller` · cutover.

---

## 6. What we got wrong, and what it changed

This is the part worth reading. Each of these cost real time or would have
shipped real damage.

### 6.1 Wrong gate formulations

| what I wrote | what was true | cost |
|---|---|---|
| D1: word bbox edge-for-edge ≤ 1px | MuPDF reports the font **em-box**, PDFium the **ink extent**. The bar would have failed both engines for a semantic difference | measured first, caught before enforcing |
| Gate N1: "byte-parity" | the gold is **not bit-exact on floats** — `15*1e-9` is `1.5000000000000002e-08` in *both* Python and Rust, gold stores `1.5e-08` | gate reformulated to structure-exact + numeric-tolerance |

**Lesson:** a gate is a hypothesis about the artifact. Measure the artifact
before you enforce the gate, or the gate tests the serialiser instead of the
port.

### 6.2 Invented rules where the gold already had them

| what I wrote | what the gold said | cost |
|---|---|---|
| rating class driven by `table_kind` | **`table_kind` is never consulted.** Qualifier + symbol + "operating" decide it. `None` ⇒ fail-closed `unknown` | `unknown` 0/17 → 17/17 after cross-tabulating 63 rows |
| pulse vocabulary fires on `symbol` | it fires **wherever the symbol text is printed** — gold row 39 has `E*3 AS` in `parameter` with an empty symbol | `transient` 5/6 → 6/6 |

**Lesson:** cross-tabulate the gold before writing the rule. My first PRD had
three factual errors (43 vs 41 symbols, 96 vs 63 rating rows, 138 vs 141 typed)
that a self-review caught. A spec without measured numbers is invention.

### 6.3 Silent semantic divergence in the port

Four of these, all caught by gates, all would have shipped:

1. Python `Pattern.match(s, pos)` **anchors at `pos`**. Rust `find()` searches
   anywhere. Separator and value scans produced **zero matches** and the parse
   returned `NoAnchors` for well-formed input.
2. Rust raw strings have **no `\`-newline continuation**. Python does. The
   patterns contained literal backslash-newlines and did not compile.
3. My tokenizer kept punctuation: `drain-source` → `["-source"]`, so header
   binding silently failed. Python's `\d+|[a-z]+` never yields punctuation.
4. `#[serde(skip)]` needs `Default` to fill the field on deserialize. An empty
   `Verbatim` is illegal — so the fix was to **not implement `Deserialize`**,
   which is also more law-abiding: the laws live in the constructor, and a JSON
   round-trip would route around them.

**Lesson:** the only thing that catches semantic divergence is a gate with real
data. Reviewing the code would have passed all four.

### 6.4 Design bugs that tests caught

| bug | why it mattered |
|---|---|
| a boolean capability atom with `value: None` can never fail | therefore can never narrow, therefore **the phrase layer is decorative**. `MinRating >= 1.0` is the correct model of "has it" |
| `dw-decide` → `dw-knives` → `dw-decide` | planned the types without planning the layering. Composition needed its own crate (`dw-funnel`) |
| residue split per-word | four unknowns for `"talks to a car"` loses that a human wrote one phrase |
| dash placeholders defeat the ordinal wrong-cell guard | real reference behaviour that *looks* like a bug. Ported as-is with its own test documenting the limitation — "fixing" it silently would diverge from the reference |

**Lesson:** a constraint that cannot fail is not a constraint. Test the
*negative* path: "does this atom ever eliminate anyone?"

### 6.5 Process

- **The NAS unmounts.** G7 was blocked twice. Gates must be self-contained —
  which is why the gold corpus is in-repo and `tests/fixtures/gold/` carries
  its own PDFs.
- **Spikes need a stop rule.** G7 ran long on pdfium internals. It established
  the important thing (engine viable, coordinates align exactly) and should
  have been recorded and stopped sooner.
- **Never folded D4/D5 back into `PLAN.md`** as I said I would. That debt is
  still open — see §8.
- **Stopped to report mid-push.** The definition of done was never met; the
  reporting should have been the handoff, not a status.

---

## 7. Blockers

| blocker | blocks | what unblocks it |
|---|---|---|
| **87K corpus location** | Gate N4, which is the only corpus-scale drift proof | Candidates: `/Volumes/M5_4TB/exports/power-datasheet-pairs/{ti,rohm,infineon}-20260908/` (9,201 rows) and `results/lora-v1-pairs/`. Neither matches "2,713 v1 / 87K v2". **Needs a human to name it**, or a decision to redefine N4 against the 9,201-row corpus that does exist. |
| NAS availability | corpus-wide work, G7 | run gates against in-repo fixtures; treat the vault as an input, not a dependency |
| `~/.cargo/bin` on PATH | nothing, but it costs every command | one line in `~/.zshrc`, needs the user's word |

---

## 8. Debt in `PLAN.md` — correct it before handoff

`PLAN.md` is the historical plan and is now partly wrong. It must not be
silently edited; corrections belong where they can be seen.

| PLAN.md says | reality | correction |
|---|---|---|
| §5.7 `dw-search`: "FTS + sections first" | wrong — the funnel's front door is the colloquial→knife binding, not text retrieval | **D4 supersedes this.** Search is the residue path |
| §11 PDF spike: "matches on adversarial pages" | no threshold, not reproducible | **D1 supersedes this** with a measured bar |
| §6 vendor priors "populate TI, ST, NXP, Renesas, Vishay" | hand-authored priors are invented facts | **D6 supersedes this**: priors are a projection of `dw-coverage`, never authored |
| §12 "Week 1 gate: 87K claim diff = 0 unexplained" | corpus location unknown | blocked; see §7 |
| §13 docs collapse to 5 | we have 5 + `HANDOFF.md` + `PRD-NORMALIZE.md` | `HANDOFF` and `PRD-NORMALIZE` are additions, not scope creep; both earned their place |

**Action:** append a dated corrections block to `PLAN.md` rather than editing
its body. History should stay readable.

---

## 9. Definition of done — tightened

The two existing gates stand. This adds the third, which is the one our use
case actually depends on.

> **Trust gate.** Every number in a response resolves to a **content-addressed
> page render and row-band crop**. If either fails to resolve, the claim is
> `Unknown` — never `Pass`. Every response resolves to an **immutable release
> manifest** naming its corpus freeze, vocabulary versions, embedder
> fingerprint, and engine identity.

| gate | currently |
|---|---|
| content gate (`PLAN` §0) | **executable** — `dw-funnel::accept`, asserted clause by clause |
| delivery gate (`D7`) | typed, not met — needs `dw-api` and evidence inline |
| **trust gate (this document)** | **met on the evidence and release clauses** — links 4 and 5 resolve, and unresolvable ⇒ `Unknown`. Still open: corpus-scale drift proof (N4) and the phrase-layer eval (Tier 2) |

All three must pass. The trust gate is the one that lets an engineer stake a
design on the answer, which is the only reason this product exists.

---

## 10. Recommendation

**Tier 1 is done (2026-10-10).** What landed: `dw-evidence` (page render +
crop, content-addressed, engine-pinned, local-only) · `dw-release` (manifest,
append-only, lineage) · the trust rule in `dw-decide::judge`. **Do not rebuild
these.**

Next is Tier 2 — specifically:

1. `dw-evidence`: page render @ 144 DPI, row-band crop, content-addressed,
   engine-pinned, stored locally. Small, bounded, and it converts the largest
   class of typed-but-false claims into facts.
2. `dw-release`: `ReleaseId` → immutable manifest, append-only.
3. One rule in `dw-decide::judge`: **unresolvable evidence is `Unknown`.**

Then Tier 2 (N4 + phrase-layer eval) is the proof the whole thing is faithful,
and Tier 3 makes it useful.

Tier 1 is the foundation; Tier 2 is the proof it is faithful; Tier 3 is what
makes it useful. **The one thing that blocks Tier 2 is human input**: the 87K
corpus location (§7). Without it, the only corpus-scale drift proof cannot run
and the port is unverified at scale. Ask. Do not guess.
