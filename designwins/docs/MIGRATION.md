# MIGRATION

**How the Python factory becomes the Rust platform.**

This document is the operating procedure for the port: what is being replaced,
how parity is proven, and what cutover and rollback actually mean. It absorbs
the intent of `TRUTH_PLAN.md` and `CONTEXT_HANDOFF.md`, both of which are
superseded.

Companion documents: `CONTRACTS.md` (the law), `DECISIONS.md` (D1–D7, ratified).
If this document and either of those disagree, **those win**.

---

## 1. The shape of the migration

One funnel. Everything else is in service of it.

```
WHAT ARE YOU BUILDING?  ->  typed constraints  ->  COHORT
   (text · image · BOM · "like an X but…")
                         ->  compare @ your point  ->  explain  ->  next question
```

**The funnel already exists.** `harness/discovery/` is documented as the
*Intent-to-Knife laboratory: designs → requirements → discover → ledger →
next-question*, verified over 1,881 parts / 86,736 claims. It was planned as
one crate among many. **It is the product.**

Nothing is rewritten in place. Python keeps producing the reference output for
the whole port. Rust is gated against it at every step.

---

## 2. What is replaced

### Product A — ported to Rust

| Rust crate | Python it replaces | Gold that gates it |
|---|---|---|
| `dw-types` | invariants in `claims.py`, `admission.py`, `condition_model.py`, `rating_class.py` comments | compile-fail tests |
| `dw-pdf` | PDF access in `word_columns.py`, `regions.py`, `locator.py`, `page_evidence.py` | D1 G7 path-segment parity |
| `dw-structure` | `word_columns.py`, `regions.py`, `locator.py`, `table_extractors.py`, `census.py` | gold table/feature fixtures |
| `dw-recon` | `family_census.py`, `family_document.py`, `family_device_matrix.py`, `document_scope.py`, `page_index.py`, `corpus.py` | category clusters byte-stable |
| `dw-knives` | `evaluator::AXES`, `power_axes_v2.py`, `rating_class.py`, `curve_atoms.py`, `key_features_grid.py`, `aec_grades.py` | 5 axes 1:1, condition 167/167 |
| `dw-truth` | `quote_verify.py`, `condition_model.py`, `admission.py`, `ladder.py`, `crop_verify.py`, `ball_validate.py` | 87K claim diff = 0 unexplained |
| `dw-normalize` | `condition_model.py` typing, `mode_currents.py`, `thermal_metrics.py` | property tests |
| `dw-graph` | `ordering_codes.py`, `power_topology.py`, `errata_deltas.py` | topology gold |
| `dw-search` | **built, not ported** — `harness/search/` is empty | D4 query telemetry |
| `dw-decide` | `discovery/evaluator.py`, `discovery/service.py`, `discovery/curves.py`, `discovery/store.py` | 1,881 parts / 86,736 claims |
| `dw-coverage` | `coverage.py`, `coverage_report.py` | coverage cube answers category × aisle × vendor |
| `dw-extract` | `extraction.py`, `bom_extract.py`, `key_features_grid.py` | extraction gold |
| `dw-queue` | `pipeline/store.py` | queue cap, sweep, FIFO |
| `dw-release` | `holdout.py`, `ground_truth.py` | immutability tests |
| `dw-model` | `local_model.py`, `cloud_vision.py`, `frontier_batch.py`, `providers/*` | prompts byte-identical |
| `dw-learn` | pair materialization, promotion gate | pair format byte-compat |
| `dw-api` | `extraction_api.py`, `discovery/api.py`, `pipeline/api.py` | D7 feed contract |
| `dw-controller` | `factory_control.py`, `burn_loop.sh`, `sentinel.py`, `soak_sample.py` | unattended run |

### Product B — leaves the repo

`gateway/` (foreman/orch), `dispatch.py`, `shadow/`, `greenfield/`, `sandbox/`,
`task/`, `serial/`, `cases/`, `cli.py`, `orch_loop.py`, `checkpoints.py`,
`context_compiler.py`, `optimize.py`, `economics.py`, `report.py`, `rescue.py`,
`tournament.py`, `repo_contract.py`, `training/code_curriculum.py`,
`training/categoryrank.py`.

### Stays Python

`training/` (queue, ledger, exporters, promotion, worker, adapters),
`evaluation/`, gold-fixture generators. Rust owns the loop and the promotion
gate; Python owns only the gradient step (D3).

---

## 3. The parity process

### 3.1 Freeze the input

```
python3 scripts/freeze_corpus.py tests/fixtures/gold \
  --release designwins-2026-W46-freeze-0 \
  --out tests/fixtures/gold/corpus_freeze.json

python3 scripts/freeze_corpus.py tests/fixtures/gold \
  --verify tests/fixtures/gold/corpus_freeze.json
```

A freeze is content-addressed: every member carries a SHA-256. Re-running
`--verify` after any change reports `DRIFT` and names the file. **A parity run
against an unfrozen corpus is not a parity run.**

### 3.2 Run the comparison

```
cargo run -p dw-parity -- --corpus tests/fixtures/gold/corpus_freeze.json \
  --out target/parity-ledger.json --check
```

`dw-parity` runs the Python reference and the Rust candidate over the same
frozen subjects and writes a discrepancy ledger. `--check` exits non-zero if
anything blocks cutover.

### 3.3 Classify every difference (D2)

| class | blocks cutover | action |
|---|---|---|
| `rust_bug` | **yes** | fix the port |
| `python_bug` | no | fix the reference, or record why it stands |
| `intentional_improvement` | no | **requires a signed entry in `DECISIONS.md`** naming the decision |
| `unexplained` | **yes** | triage. This is the only class that means "we do not know" |

There is no fifth class and no way to skip classification. An entry cannot
exist without one.

### 3.4 The Week 0 gate

> The ledger runs **empty** against Python's `quote_verify` on the frozen set.

An empty ledger is not "nothing found yet." It is proof the port has not
drifted. That gate is what makes every later week's numbers meaningful.

---

## 4. Cutover

**Permitted when `unexplained == 0` and `rust_bug == 0`.** (D2)

Sequence, in order. Each step is reversible until the last.

```
shadow      Rust and Python both run on live traffic; Python remains authoritative
verify      ledger stays clean over a sustained window
cutover     Rust becomes the writer (C4 / C5 formats do not change)
delete      the Python paths in §2 are removed
measure     report LOC / throughput / latency / memory / failures / provenance
```

`measure` reports numbers. It does not gate: D2 forbids a speedup gate because
PLAN §12 forbids speedup claims before numbers.

### Rollback triggers

Any one is sufficient. Rollback is not a decision, it is a reflex.

1. **Claim-level regression** on the frozen corpus.
2. **Provenance failure** — missing page render, or `crop_sha256` mismatch (C13).
3. **Performance floor** — throughput or p99 worse than **2× the Python
   baseline**. A harm floor, not a target.

---

## 5. Day 1 execution order

| # | item | status |
|---|---|---|
| 0 | `rustup` + `clippy` + `rustfmt` + `cargo-deny` | done |
| 1 | `git`: branch clean off `main` | **blocked** — 35 uncommitted changes on `curve/07e-evidence-supply`, needs a call |
| 2 | `CONTRACTS.md` | done |
| 3 | `crates/dw-types` + compile-fail tests | done |
| 4 | PDFium spike → engine verdict | **partial** — engine viable, G7 parity open (see `DECISIONS.md`) |
| 5 | parity harness skeleton | done |
| 6 | corpus freeze script | done |
| 7 | `MIGRATION.md` | this document |

**Week 0 gate:** the ledger runs empty against Python's `quote_verify` on the
frozen set.

---

## 6. What is never negotiated

These are not preferences. They are enforced by types or by CI.

1. One implementation of each engineering rule → crate boundaries.
2. No duplicate truth across machines → one `ReleaseId` per answer.
3. No unverified promotion → `Admitted` newtype.
4. No lost lineage → append-only releases.
5. No unsupported exclusion → every `Ineligible` carries reason + evidence ref.
6. Every publish immutable, reproducible, reversible.
7. Rust owns plumbing; Python only where model/training deps force it.
8. SQLite never on the NAS — local transactional DB per box.
9. `derived` constraints are never silent.
10. `Unknown` never eliminates.
11. Absence is a fact (`NotObserved` != `NotApplicable` != `None`).

See `CONTRACTS.md` for the mechanical form of each.
