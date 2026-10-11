# HANDOFF — the Rust port

**Read this first if you are picking up cold.** This document plus
`PROVENANCE.md` and `ASSURANCE.md` are the entire handoff. Everything below is
measured, not assumed — if a claim here is wrong the tests will say so; if it
is right you do not need to rediscover it.

---

## 0. Start here — prove the workspace is green

```bash
export PATH="$HOME/.cargo/bin:$PATH"   # brew rustup leaves shims unlinked
cd designwins
cargo fmt --check && echo "FMT OK"
cargo clippy --workspace --all-targets -- -D warnings && echo "CLIPPY OK"
cargo test --workspace 2>&1 | grep "test result"
cargo deny check
```

**Green looks like:** `FMT OK` · `CLIPPY OK` · **16 `test result` lines, 106
tests** · `cargo deny` reporting `advisories ok, bans ok, licenses ok, sources
ok`.

**One line is expected to FAIL:** `g7_path_segments_match_python_reference`.
That is a **gate**, not a regression — it blocks `dw-pdf` until the residual in
§5 closes. Any *other* failure is real.

If it is not green, fix that first. Nothing below means anything on a red build.

### Orientation

| thing | answer |
|---|---|
| repo root | `/Users/samkim/Harnessv1` (a Python factory being ported) |
| Rust workspace | `designwins/` |
| **entry point** | `dw_funnel::accept(sentence, registry, cohort, facts, release)` → `Build` |
| **replacement proof** | `dw-parity --bin dw-replace --mode <condition\|quote>` — both engines live, one run |
| dependency graph | `dw-types → dw-normalize → dw-decide → dw-knives → dw-funnel`, acyclic. `dw-truth` / `dw-evidence` / `dw-release` / `dw-parity` / `dw-pdf` alongside |
| toolchain | `rustc 1.98.1`, `cargo-deny 0.20.2` |
| lint bar | `clippy::pedantic` at `-D warnings` for library code. Test modules carry a documented `#[allow]`. **Do not weaken the library lint** |

### Do not touch

`git status` shows **~36 uncommitted changes that are not ours** — modified
`harness/*.py`, `scripts/*.py`, `tests/*.py`, root `.md` files, all predating
this work on `curve/07e-evidence-supply`. **Do not stage, commit, revert, or
clean them.** Ours is committed: `designwins/`, `scripts/{emit_pdf_engine_reference,
fetch_pdfium,freeze_corpus,locate_claim_pages,build_evidence,prepare_quote_parity,
parity_reference}.py`, `tests/fixtures/gold/*`, `PLAN.md`.

---

## 1. Definition of done — three gates, and where each stands

| gate | source | state |
|---|---|---|
| **content** | `PLAN.md` §0 — sentence → cohort + derived + exclusions + unknowns + next + every number opens to the page | **executable**, asserted clause-by-clause in `dw-funnel::accept` |
| **delivery** | `DECISIONS.md` D7 — every front end renders from one response | **not met** — needs `dw-api` |
| **trust** | `PROVENANCE.md` §9 — evidence resolves, releases resolve, unresolvable ⇒ `Unknown` | **met on links 1–6**; link 7 partial, link 8 measured |

All three must pass. The trust gate is the one that lets an engineer stake a
design on the answer, which is the only reason this product exists.

### PLAN §0 clause by clause

| clause | state |
|---|---|
| types one sentence | ✓ `dw-funnel::accept` |
| returns a cohort of distinct parts | ✓ `dw_decide::Cohort` |
| constraints derived **and confirmed** | ✓ `Derived` + `ConfirmState`, `Pending` contributes nothing |
| every exclusion with its evidence | ✓ `Ineligible`, unconstructible without evidence |
| lists what it could not evaluate | ✓ `MissingAxis` first-class |
| next question that narrows most | ⚠ ranked by `affected` proxy, not measured reduction |
| **every number opens to the page** | ✓ **86,398 / 86,736 located (99.6%)**, evidence rendered |

---

## 2. The replacement proof — this is the headline

`dw-parity --bin dw-replace` invokes **both engines live, on the same inputs,
in the same run**. Reference is a subprocess (`scripts/parity_reference.py`),
candidate is in-process, neither shares state, only output is compared, and
both engine identities are recorded.

### `condition_model.parse_condition` — **PROVEN**

```
python:harness.electronics.condition_model.parse_condition
  vs rust:dw_normalize::parse_condition
  subjects  10270   agreements 10270   discrepancies 0   cutover_permitted yes
```

### `quote_verify.verify_claims` — **gate built, 1 of 3 cases agree**

```
python:harness.pipeline.quote_verify.verify_claims
  vs rust:dw_truth::verify_claims
  cases 40 prepared (9,890 claims, 2.13M chars)   1/3 sample cases agree
```

**221 field-level diffs remain across the 2 diverging cases.** Triaged by
class, not yet fixed. See §5.

### Why this is stronger than the gold gates

`N1` / `N4a` / `N4b` compare Rust against fixtures **generated from Python at an
earlier moment**. They prove parity against a snapshot and cannot catch a change
on either side the snapshot predates. The replacement gate closes that.

Run it:

```bash
cd designwins
cargo run -q -p dw-parity --bin dw-replace -- \
  --mode condition --corpus ../tests/fixtures/gold/claim_conditions_v1.jsonl \
  --field condition_verbatim
cargo run -q -p dw-parity --bin dw-replace -- \
  --mode quote --corpus ../tests/fixtures/gold/quote_parity/quote_parity_cases.jsonl \
  --field case_path --out /tmp/qled.json
```

---

## 3. Gate inventory

| gate | substrate | bar | state |
|---|---|---|---|
| build / fmt / clippy pedantic / deny | workspace | green | **green** |
| N1a `condition_model_v1.jsonl` | 167 curated | 167/167 | **green** |
| N1b `rating_class_v1.jsonl` | 63 curated | 63/63 | **green** |
| N2 property tests | units · coverage · margin | pass | **green** |
| N3 compile-fail | 4 laws | 4/4 | **green** |
| N4a `condition_typed_catalog_v1.jsonl` | 9,551 from `catalog.db` | 9,551/9,551 | **green** |
| N4b `claim_conditions_v1.jsonl` | 10,270 conditions / 64,245 claims | 10,270/10,270 | **green** |
| C3/C13 provenance | 86,736 claims | quote+sha 100%, **page 0 → 99.6%** | **green** |
| D4 phrase telemetry | 7,423 weighted searches | floor 632 bound / 625 `ProvenFit` | **green** |
| Week 0 parity | ledger | empty | **green** |
| trust gate | links 1–6 | real | **green** |
| **replacement: condition** | 10,270 live | 0 diffs | **green** |
| **replacement: quote** | 40 cases live | **1/3 sample agree** | **OPEN** |
| D1 G7 path segments | PDFium vs MuPDF | vertex parity ≥99% | **OPEN — expected** |

---

## 4. Storage — three surfaces, know which is which

| surface | path | what it is | use |
|---|---|---|---|
| **yosemiteraid5** | `/Users/samkim/mnt/yosemiteraid5/{Public,Home}` | **local NVMe, fast, WRITABLE.** `nobrowse`, so hidden from `/Volumes` | the work target. Evidence store lives at `Public/m5-opencode/evidence/` |
| **M5_4TB** | `/Volumes/M5_4TB` → symlink to `Electronicdataassets/m5-4tb` | SMB `//harness@charleybasecamp.local`, spinning disk. **Unmounts** | read-only source. Never a store target (PLAN §10) |
| **Electronicdataassets** | `/Users/samkim/Volumes/Electronicdataassets` | SMB mirror + `backups/m5/` | cross-checks |

**SQLite on these mounts needs `?mode=ro&immutable=1`.** Plain `connect()`
fails with "unable to open database file" because SQLite wants journal space.
`catalog.db` copies to `yosemiteraid5` in ~2.7 s (58 MB) and reads fine there —
that copy is at `…/m5-opencode/scratch/catalog.db`.

**`catalog.db` is the substrate and it is all readable:**

| table | rows | use |
|---|---:|---|
| `claims_canonical` | **86,736** | the "87K" of PLAN §12 |
| `conditions_typed` | 9,551 | N4a oracle |
| `consensus` | 107,033 | resolution trail |
| `parts` / `part_sources` | 1,882 | matches PLAN's discovery count |

**Still denied:** `designwins-v{1,2,3}/canonical/**` — 18 SFT pair files on
`harness-training/datasets/`. Not blocking: the *claims* are open.

---

## 5. OPEN WORK — in priority order

### 5.1 `header_binds` divergence — the one real porting bug left

`quote_verify` replacement gate: **221 field-level diffs across 2 of 3 sample
cases**. Triaged by class:

| class | n | reading |
|---|---:|---|
| `verdict REVIEW→SUPPORTED` | 90 | my `header_binds` returns `Some(true)` where the reference returns `None` |
| `verdict UNSUPPORTED→SUPPORTED` | 56 | same root, downstream effect |
| `reason partially_bound_header→unbound_header` | 33 | partial vs unbound |
| `verdict SUPPORTED→REVIEW` | 21 | reverse |
| `verdict UNSUPPORTED→REVIEW` | 14 | |
| `severity P0→P1` | 4 | |

**Known asymmetry that is not the bug:** the reference emits
`"reason_code": null` but **omits** `evidence_quote` when absent. Both fields
must be serialised to match — `reason_code` always, `evidence_quote` only when
present.

**Suspect:** `norm(quote)[:40]` slicing and the `idx`-based span window. Note
the reference is **context-dependent per case** — running `verify_claims` on one
claim in isolation can give a different answer than the same claim inside the
full map. Find out why before changing anything.

### 5.2 `dw-api` — the D7 delivery gate

The only thing between here and "every front end renders from one response."
Seven routes, evidence inline. See `CONTRACTS.md` C7 for the per-response feed
contract. **No corpus dependency** — buildable now.

### 5.3 Question ranking by measured reduction

Currently ranks by `affected` count — an honest proxy, named as such. The real
one is `harness/discovery/service.py::next_question`, verified over 1,881 parts
/ 86,736 claims. **Port it, do not re-derive it.**

### 5.4 G7 residual — blocks `dw-pdf`

`msp430g2744` p15 y-distribution mismatch. PDFium bakes a parent's transform
into enumerated Form XObject children, so composing the form matrix
double-applies it. Recursing with the **parent** matrix took `msp430fr6005` p29
to exact drawing count (208 = 208). Then decomposition normalization (`re` →
4 corners, Bézier triples) to ≥99% vertex parity.

### 5.5 Everything else

`dw-search` (D4 front door) · `dw-coverage` (cube + gap ledger) ·
`dw-knives` axis registry generalisation (F-03: 5 axes for all of
electronics) · `dw-structure` / `dw-recon` / `dw-graph` · `dw-extract` /
`dw-queue` / `dw-model` / `dw-learn` · `dw-controller` · cutover.

---

## 6. WHAT THE GATES CAUGHT — do not re-learn these

**Fifteen-plus instances of one class: an assertion or a rule that could not
fail on the property it claimed to test.** `ASSURANCE.md` exists because of
them. Read it before writing a check.

### The three mechanisms

1. **The assertion shares the code's blind spot.** `get("page").is_some()` is
   true for a present-but-`null` field — I reported "86,736/86,736 carry quote
   + sha + page" from a test I had just authored. Truth: **0 pages.**
2. **The bar is a floor and floors do not discriminate.** `coverage >= 0.5`
   passed at **131.2%** because OPNs were summed across family documents
   instead of unioned. `with_quote >= 90%` passed at 90% and I reported 100%.
3. **Reported numbers came from assertions, not queries.** `PRD-NORMALIZE`
   opened with three wrong cardinalities (43 vs 41 symbols, 96 vs 63 rating
   rows, 138 vs 141 typed) — all recalled, none counted.

### Porting gotchas (each cost real time)

| gotcha | why it bit |
|---|---|
| Python `Pattern.match(s, pos)` **anchors at `pos`** | Rust `find()` searches anywhere. Separator and value scans returned **zero matches** on well-formed input |
| Rust raw strings have **no `\`-newline continuation** | Python does. Patterns contained literal backslash-newlines |
| `fancy-regex` is **required** | `_TEMP_UNIT` uses `(?![A-Za-z])`, `_BARE_TEMP` uses `(?<![\w.])`. The `regex` crate has no lookaround |
| `fancy_regex` API | `find` not `search`, `captures` for named groups, `is_match` → `Result`, `Match` has no `.name()` |
| a tokenizer must **discard punctuation** | `\d+\|[a-z]+` never yields `-source` from `drain-source`. Mine did, and header binding silently failed |
| `#[serde(skip)]` needs `serde(default)` on the way in | bit `Verbatim` (which is why `Deserialize` is deliberately absent there) and `ReleaseManifest` |
| **gold fixtures are not bit-exact on floats** | `15*1e-9` is `1.5000000000000002e-08` in **both** Python and Rust; the gold stores `1.5e-08`. Gate N1 compares structure exactly, numerics with tolerance |
| **claim `value` is a string in the corpus** | `"0.5"`, not `0.5`. `Value::as_f64()` returns `None` for a string and silently skipped the ordinal wrong-cell guard |
| reference wire shape is **asymmetric** | `reason_code` emitted as `null`; `evidence_quote` omitted when absent |
| array order is not semantic for keyed records | reference emits claim order, `BTreeMap` emits sorted |

### Semantics the gold corrected

- **`table_kind` is NOT consulted for rating class.** Qualifier + symbol
  vocabulary + the word "operating" decide it. `None` ⇒ fail-closed `unknown`.
  `unknown` went 0/17 → 17/17.
- **Pulse/surge vocabulary fires wherever the symbol text is printed**, not
  only in the symbol cell. Gold row 39 carries `E*3 AS` in `parameter`.
- **The curated gold was stale on comparator order.** The live reference emits
  symbol-table order; the fixture had document order. I had bent the *code*
  toward the fixture. See `DECISIONS.md` **D8** — *a fixture is a sample of the
  reference, not the reference.*
- **`value_regex` needed `(?i)`** because `condition_norm` is lowercase-
  normalized. Dropping it broke 81% of the real corpus while N1 stayed green.

### Design laws that are load-bearing

- **A `Pending` derived constraint contributes nothing** until confirmed.
- **An `absmax` rating yields `Unknown`, not `Fail`.** Wrong physical quantity;
  the part may be fine, we just have not established it.
- **Margin is applied to the requirement**, never the claim, and never silently.
- **A constraint that cannot fail is not a constraint.** Boolean atoms need a
  threshold (`MinRating >= 1.0`) or they can never narrow — which made the
  phrase layer decorative.
- **Unresolvable evidence is `Unknown`.** `Provenance` is a parameter of
  `dw_decide::judge`, not a caller-side check, because a check at the call site
  is a check that gets skipped.

---

## 7. Ground truth — where the answers already are

| thing | location |
|---|---|
| the plan | `PLAN.md` (Revision 3) — **body partly stale**, read §19 Corrections at the bottom |
| ratified laws | `designwins/docs/DECISIONS.md` — **D1–D9** |
| contracts | `designwins/docs/CONTRACTS.md` — `Build` first, then C1–C13 |
| normalization spec | `designwins/docs/PRD-NORMALIZE.md` |
| phrase/axis spec | `designwins/docs/PRD-PHRASE.md` |
| trust model | `designwins/docs/PROVENANCE.md` |
| check discipline | `designwins/docs/ASSURANCE.md` — **read before writing a test** |
| Python reference, gate side | `harness/pipeline/{quote_verify,admission,ladder,crop_verify,ball_validate}.py` |
| Python reference, extraction | `harness/electronics/*.py` (49 files) |
| **the funnel, verified** | `harness/discovery/` — 1,881 parts / 86,736 claims |
| condition gold (167) | `tests/fixtures/gold/condition_model_v1.jsonl` |
| rating gold (63) | `tests/fixtures/gold/rating_class_v1.jsonl` |
| conditions corpus (9,551) | `tests/fixtures/gold/condition_typed_catalog_v1.jsonl` |
| claim conditions (10,270) | `tests/fixtures/gold/claim_conditions_v1.jsonl` |
| claim provenance (86,736) | `tests/fixtures/gold/claim_provenance_v2.jsonl` |
| located pages | `tests/fixtures/gold/claim_pages_v1.jsonl` |
| quote parity cases | `tests/fixtures/gold/quote_parity/` |
| knife coverage | `tests/fixtures/gold/knife_coverage_v1.json` |
| category graph | `tests/fixtures/gold/category_graph_v1.json` |
| search telemetry | `tests/fixtures/gold/search_telemetry_v1.jsonl` |
| corpus freeze | `tests/fixtures/gold/corpus_freeze.json` |
| **colloquial chips (D4)** | `…/exports/cr_drops/20260813_mcu_yellow_occasion_holes/holes.json` |
| **four-drawers ruling** | `…/agent-inbox/cursor-cr/20260814T174540Z_*ti-subcategory-vs-application*.md` |
| knife coverage substrate | `…/exports/cr_drops/20260814_yellow_scorecard/2026-08-14.json` |
| evidence store | `…/m5-opencode/evidence/{pages,crops}/` |
| agent mail | `…/Electronicdataassets/agent-inbox/` + `INDEX.jsonl` |

---

## 8. Agent network — I am `m5-opencode`

Protocol: `/Volumes/M5_4TB/AGENT_NETWORK.md` + `MAIL_ONBOARDING_m5-opencode.md`.
Canonical inbox is **`Electronicdataassets/agent-inbox`** (migrated 2026-10-08);
old trees are frozen. Send with
`repos/outcited-ai-sandbox-20260822/scripts/orchestration/send-message.ts`
(its `node_modules` is currently broken on the NAS path — write the file
direct-on-tree with correct YAML frontmatter and append to `INDEX.jsonl`).
**Stamp `agent-inbox/m5-opencode/.heartbeat` whenever you read mail.**

Correspondents: `cursor-cr`/`cr-core` (ingest, publish, gold) · `cursor-tapes`
(embedder + ontology) · `claude-code-browser` (+ `-m4`) (crawlers) ·
`mac-mini` (NAS, fleet, **Hermes advisor**) · `m4opencode` (QA) · `samson`.

**Sent this session:**

| to | subject | state |
|---|---|---|
| `mac-mini` | `hermes-extraction-position-gold-set-and-lessons-20261011` | sent — endorsed the proposal gate, corrected the gold set (86,736 claims not 200 docs), argued experiment one is model×document-class, offered `dw-lessons` |
| `cursor-tapes` | `embedder-coordination-open-plus-lessons-20261011` | sent — three asks from 2026-10-09 still open + lessons-learned request |

**Open inbound:** `mac-mini` offered (a) a 200-doc pinout gold-set
collaboration and (b) extraction-scoped advisor cycles. `cursor-tapes` has not
answered the embedder asks since 2026-10-09. `mac-mini` 2026-10-10 said
*"Tapes_4TB gone — vault is NAS-copy only, relocation pending Sam"* — that was
the permission break, since fixed.

---

## 9. The Hermes question — my position as lead

`mac-mini` has **already built it** (2026-10-11, commit `2e02aaf`, live in
shadow): proposal gate with a DB **trigger forcing `applied=false`** on any
non-baseline source, a `tp4-flash` advisor (DeepSeek 4.1-flash, 1M ctx, tools +
vision, free), Hermes 0.19.0 on the mini, and `mfg orch-replay` 48 h
counterfactual scoring.

**I endorse it.** The trigger is enforcement by construction, not by intention
— the same move as a compile-fail test. And their rule is ours:

> *"the model that generates the answer never grades it. Your Rust/verifier
> side stays the authority on gold promotion."*

**Three positions I put in writing:**

1. **The gold set is 86,736 claims with 99.6% located and hash-verified
   evidence**, not 200 docs. `held_out_ref` should be a claim with a quote, a
   page, a crop hash, and a `ReleaseId`.
2. **Experiment one is model × document-class, not DPI.** Four vision models
   and one is free (DeepSeek local). Free changes the search: cover the matrix,
   don't tune one cell. `MODEL_SETTINGS.md` already shows MiMo-flash and
   DeepSeek-TP4 **tie** on series-count and axis-key and **split** on point
   error and axis-label verbatim — the class decides.
3. **The fourth question has no home.** Their loop is *why → what → did it work
   → how do we preserve the learning*. The first three have a home; the fourth
   does not. **I own `dw-lessons`** — a queryable, git-versioned index over
   `agent-inbox/` + `DECISIONS.md` + `ASSURANCE.md` + the experiments ledger,
   sitting **in front of** the proposal gate so no experiment can be proposed
   without first being checked against known lessons. This session I fumbled SSH
   for an hour on a lesson sitting in my own inbox since 2026-10-08.

**Added a fifth rule to their four:** every promoted change ships with a
**negative control** — a case where the old behaviour was wrong and the new one
catches it. Their `docs/burn 0.972 vs 0.934` is trustworthy because
`wrong-harvests 12 vs 25` is the negative case.

**Boundary:** keep the advisor on **extraction quality only**, off the
claim/admission path until `dw-api` lands. Optimising a surface that is still
moving measures nothing.

**Success metric: not accuracy — caught classes graduating to deterministic
validators.** `microchip:diode → needs_human` (2,950 attempts / 2,938
`permanent_dead` refusals) is a walled-refusal class both advisors converged on
independently on cycle one. The right output is a free permanent validator, not
a better prompt. That is PLAN §9: *a caught class never becomes model-judgment
again.*

---

## 10. What I need from the human

1. **Nothing blocking right now.** The vault permission fix landed and the
   corpus is readable. The 18 SFT pair files are still denied and are **not**
   blocking — the claims are open.
2. **`~/.cargo/bin` on PATH** would save an `export` per command.
3. **The 87K pair-shape question is closed** — `claims_canonical` (86,736) is
   the corpus PLAN §12 means, and N4a/N4b run against it.

---

## 11. Next session — do these in order

1. **`header_binds` divergence** (§5.1). The one real porting bug left. Note it
   is context-dependent per case — find out why before changing anything.
2. **`dw-api`** (§5.2). The D7 gate. No corpus dependency.
3. **Question ranking** (§5.3) — port `next_question`.
4. **`dw-lessons`** — my open offer to `mac-mini`, and the thing that would have
   saved this session two hours.
5. Then §5.5.

The acceptance test is one sentence long. Everything on that list is in service
of it.
