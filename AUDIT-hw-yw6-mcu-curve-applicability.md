# hw-yw6 — Audit REV 3 (FINAL): MCU curve applicability (investigation + test design ONLY)

Revision of REV2 snapshot `0de3c800b4ce1b4dc3704687032b691eb143e87a61c38c399064d6643156b4eb`
(AUDIT-hw-yw6-mcu-curve-applicability.md @ `curve/mcu-audit-rev` commit `743c18ea`)
per the NEO consolidated parent review on the bead (8 verbatim corrections).
REV1 `108f6cc236477a797ec4cc788863f6be5e6343d0` remains superseded.

**Labels used throughout** (parent-mandated format):
**TESTED** = executed by this worker at `61e95297` (PR66 head), raw output in
`scripts/yw6-repro/output.json`; **SOURCE-CONFIRMED** = verbatim code citation
verified in the worktree but NOT executed end-to-end by this worker;
**DESIGNED** = test/repro design only, not run; **UNKNOWN** = not knowable from
this lane (P5: reported as UNKNOWN, never guessed green).

Audited at PR66 head `61e952970fab`. Blob identities carried from REV2
(re-verified this revision): `harness/extraction_api.py` =
`323d0f804d9cda1c9a6c25775e446bf681d99f29`, `scripts/extract_typical_curves.py`
= `8c06f48eff6532da64ac4f14a9ab79def5ee40af`; both identical
47cac640 ↔ 61e95297 (git rev-parse, **SOURCE-CONFIRMED**). PR66 touched only
`harness/normserve/{adapter,loader,schema}.py` + its tests. No extractor runs,
no corpus re-extraction, no production writes, no admission changes, no load
unfreeze. FREEZE holds.

---

## 1. Per-path verdicts (REV2 §0/§1b retained with corrected evidence labels)

| path | verdict @61e95297 | evidence label |
|---|---|---|
| norm-serve (adapter→loader→`GET /api/v1/opn/<OPN>`) | OPN lookup correct; opn_default provenance gap (R3a); same-key silent drop (R3b) | R3a/R3b **TESTED** |
| curve API (`GET /api/extraction/v1/parts/<PN>?atom_class=curve`) | shared-SHA K6-only curve served stamped K4, HTTP 200 | R1 **TESTED** |
| staging curve index (`extraction_api.py:218-233` glob) | susceptible — same stem→sha join feeds PART_SHAS; incidence **UNKNOWN** pending census | **SOURCE-CONFIRMED** + **UNKNOWN** |
| family-export (`designwins/crates/dw-extract/tools/export_api_store.py`) | does NOT serve curves — projects `claims_canonical` → PartRecord wire shapes; cube cells hard-code `curves: False` (export_api_store.py:127,176); page fold keeps `page: null` + `match` reason (docstring, "honest absence") | **SOURCE-CONFIRMED** |
| `_table_facts` anchor conversion (max→typ) | role discarded at extract_typical_curves.py:98 | R2 **SOURCE-CONFIRMED**; PDF-reader E2E **DESIGNED, not executed** (parent correction 2) |

### R2 label correction (parent correction 2)
REV2's "reproduced by citation" was **invalid**. Correct status: the role-drop
mechanism is **SOURCE-CONFIRMED** (extract_typical_curves.py:98 files every
mode-current value under `"typ"`; the reader carries the printed role —
mode_currents.py:44-77 — which the facts boundary discards). This worker did
NOT execute the conversion. The parent independently executed the actual
function in memory with synthetic parsed rows and observed the max→typ
conversion; that is the parent's execution, not this worker's. The
PDF-reader end-to-end repro is a **distinct, still-unexecuted** design (§7
assertion 8; Blocker 2). Two separate evidence tiers; not conflated.

### R1 summary (**TESTED**; unchanged from REV2)
Seeded `PART_SHAS["STM32F051K4"] = [synthetic_sha]` +
`CURVES_BY_SHA[synthetic_sha] = [K6-only row]`, issued the real request through
the real `Handler`: **status=200, part_number="STM32F051K4", row_count=1**
(output.json). The row `{**cr, "part_number": part}` restamp
(extraction_api.py:385 @323d0f80) is the mechanism. Blobs unchanged by PR66 —
this leak **STILL APPLY** at 61e95297.

### R3a summary (**TESTED**; unchanged)
One fully-provenanced synthetic row with no row-carried OPN loaded with
`opn_default="STM32F051K6U7"`: `typed["opn"]=="STM32F051K6U7"`,
`validate_typed_row → []` (adapter.py:38-41 @61e95297). Serves under K6U7 with
no record that the OPN was operator-supplied.

### R3b summary (**TESTED**; unchanged)
Two provenanced rows identical on the full UNIQUE key (schema.py:47-50),
differing only page (50 vs 51) and value (22.0 vs 23.8): loaded=1, rejected=0,
duplicates=1; stored = [(22.0, p50)]; the 23.8@p51 observation is destroyed;
GROUPBY/HAVING `count>1` on stored rows returns `[]`.

---

## 2. Reference-manual vs datasheet authority (REV2 §2 retained)

`document_class` is printed-front-matter-only (document_scope.py:97-105,
203-217), not a typed column. Family membership cannot structurally infer
electrical/package evidence; R3a (**TESTED**) weakens "explicit OPN" to
possibly operator-supplied. Recommendation stands: typed
`opn_provenance ∈ {row_carried, operator_default}`, default REJECT in persist
mode unless acknowledged; family matrix / document_scope / spine remain
counterevidence (parent correction 8: keep these safeguards).

---

## 3. Per-value conditions (REV2 §3 retained; row-3 correction — parent correction 5)

PDF-grounded facts (NEO-supplied, DocID022265 Rev7, sha `5cfb0e2e…d3b543`,
artifact NOT acquired; fixtures synthetic):
- **22 mA typ @ TA=25 °C** — IDD RUN SUPPLY CURRENT, Table 25 p50, condition
  inherited from §6.1.2 p42;
- **23.8 mA max @ TA=105 °C** — same table, own condition row.

REV2's invented "condition-only TA=125 °C row attributed to IDD page 51" is
**removed**. Corrected: the known thermal operating condition lives at
**p47 / Table 20, with the p112 measurement method — a DISTINCT parameter,
not IDD**. The regression fixture therefore carries a thermal-conditions row
(parameter ≠ IDD, page 47, method p112, value UNKNOWN — no invented value),
never a TA=125 °C IDD row on p51.

Schema behavior (**SOURCE-CONFIRMED**): `condition_verbatim` passes through
(adapter.py docstring contract); UNIQUE key includes value_role +
condition_verbatim (schema.py:47-50), so the two PDF-grounded facts are two
rows — they differ on both role and condition, so both retain. R3b
(**TESTED**) proved identical-key rows collide.

Exact replay = DUPLICATE receipt (**SOURCE-CONFIRMED** at loader.py:
`duplicates+1, rejected=0`); a *distinct-observation* collision must be an
explicit **RETAINED CONFLICT** record — and per parent correction 4, conflicts
are a **separate metric**, never folded into the duplicate count (§5 assertion
3 pins this as DESIGNED behavior).

---

## 4. Variant / package / grade crosscutting — honest fixture labeling (parent correction 1)

Family crosscut facts stand (family_device_matrix.py:8-13, 23-27):
K6 UFQFPN32 GPIO=27 vs LQFP32 sibling GPIO=25; K6 USART=2 vs K4 USART=1.

**Labeling corrected per parent correction 1:** `STM32F051K6` and
`STM32F051K4` used in the regression fixtures are **base-part SYNTHETIC
FIXTURES**, honestly labeled as such. They are deliberately incomplete OPNs
(no package/temperature-range suffix), used to exercise key structure — they
are **NOT verified complete OPNs**, and REV2's "two verified OPNs" phrasing is
**withdrawn**. Exact-OPN integration remains **UNPROVEN until identity
evidence** (row-carried evidence binding a full ordering string to the
document — §5's evidenced-exact class) is available; that requires the
artifact (Blocker 2), so it is **UNKNOWN**, not green.

---

## 5. ONE regression design (**DESIGNED** — test design only, not implemented)

**Case: `test_f051_applicability_regression`**
(`tests/test_normserve_f051_regression.py`; synthetic fixtures only).

Fixtures (synthetic doc_sha = sha256 of fixture bytes):

| # | opn | value | role | condition_verbatim | page | note |
|---|-----|-------|------|--------------------|------|------|
| 1 | STM32F051K6 (base-part fixture) | 22 | typ | `TA = 25 °C` (§6.1.2 p42 inherited) | 50 | PDF-grounded: `22 mA typ @ TA = 25 °C` |
| 2 | STM32F051K6 (base-part fixture) | 23.8 | max | `TA = +105 °C` | 50 | PDF-grounded: `23.8 mA max @ TA = +105 °C` |
| 3 | STM32F051K6 (base-part fixture) | **no value** (UNKNOWN) | — | thermal operating condition | **47** | **DISTINCT parameter** — thermal conditions per Table 20, method p112; never an IDD row; never page 51 |
| 4 | STM32F051K4 (base-part fixture) | 22 | typ | `TA = 25 °C` | 50 | USART=1 sibling fixture |
| 5 | LQFP32 sibling | GPIO=25 | — | — | — | exact OPN string **UNKNOWN** pending artifact; parameterized, `skip` until supplied; never invented |

Rows 1, 2, 4 are stored claims (3 rows). Row 3 is a distinct-parameter
conditions row and is **never stored as an IDD value claim**.

Assertions (counts corrected per parent correction 4):
1. Rows 1, 2, 4 load: `loaded=3, rejected=0`; `claims_for_opn("STM32F051K6")`
   returns rows 1+2 with distinct (value_role, condition_verbatim); no
   cross-OPN leakage between the K6 and K4 fixtures.
2. Row 3 (thermal, p47/Table20, method p112) is stored — if at all — under its
   own distinct parameter, never as an IDD claim, never on page 51, never with
   an invented value.
3. **RETAINED CONFLICT is its own metric**: a sixth row identical to row 1 on
   the full UNIQUE key but value 22.5, page 53 → loader emits a *conflict*
   record carrying the full incoming row, counted in a separate
   `conflicts` field — **not** counted in `duplicates` (requires loader
   change; pins DESIRED behavior).
4. **Exact replay = DUPLICATE receipt, counts fixed**: replaying rows 1, 2, 4
   verbatim yields `loaded=0, duplicates=3, rejected=0` — **3, not 4**: row 3
   is never a stored claim and contributes no duplicate; conflicts (assertion
   3) are excluded from this count. Each duplicate receipt names the replayed
   row so replay audit is reconstructible.
5. **Wildcard typed, not banned** (parent correction 6 — evidence labels fixed):
   a row bound to wildcard `STM32F051K6Ux` loads only with
   `opn_is_wildcard=true` + `wildcard_scope`; `decode("STM32F051K6UX")` and
   `decode("PIC32MX795F512L")` succeed (ordering_codes.py X pin letter / PIC32
   scheme — **TESTED** in Ra, output.json). However: the adapter currently
   checks the OPN only as a non-empty string (adapter.py:38-41 — **SOURCE-
   CONFIRMED**); decoder acceptance is **not** verified orderability, and the
   X examples do not prove commercial existence of any specific X-suffixed
   part. The assertion that an *undecodable* string is "rejected by the
   existing path" is **UNKNOWN** (the existing path has no such rejection
   today); the typed rejection is **DESIGNED** desired behavior, pinned as
   such.
6. **opn_provenance recorded**: `opn_default` loads store
   `opn_provenance="operator_default"` (requires §2 change; DESIRED). Until
   implemented: xfail guard documenting the current R3a defect so it cannot
   regress silently.
7. **R1 pin (curve API)**: a sha whose curve rows are K6-bound must not serve
   stamped as K4 — either 404/zero rows or rows carrying a true
   `source_part_number` binding; never a silent `part_number` restamp
   (requires serving fix; DESIRED behavior pin).
8. **R2 pin (anchor conversion)**: a `max`-role mode-current row must surface
   under a role-faithful key, never `"typ"` (requires extract_typical_curves.
   py:98 fix; DESIRED behavior pin; PDF-reader E2E **DESIGNED, unexecuted** —
   needs a table-bearing synthetic PDF fixture, Blocker 2).
9. **Wrong-part exclusion from accepted OPN results** (parent correction 8):
   accepted per-OPN results must exclude wrong-part curve rows; flagged
   wrong-part rows are kept separately as raw diagnostics, never merged into
   accepted output (DESIRED behavior pin; requires §1 serving fix).

---

## 6. Bounded affected-record scope — census semantics corrected (parent correction 3)

Affected counts remain **UNKNOWN**. REV2's "n_raw > n_stored is a proven drop"
is **corrected**: raw counts are inflated by exact replays, so a raw-vs-stored
count deficit alone is NOT proof of loss. The census must classify within the
actual UNIQUE key by comparing canonical observation content + evidence:

1. **Per-key classification census** (read-only, when store access is
   authorized): for each `(opn, doc_sha, field, value_role,
   condition_verbatim)` key present in retained lane-source JSONL:
   - **exact duplicates**: raw rows identical to the stored row (content +
     evidence) → replay inflation, not loss;
   - **distinct suppression**: raw rows differing from the stored row within
     the same key (e.g. R3b's 23.8@p51 vs 22.0@p50) → proven silent drop
     (**TESTED** loss class, R3b);
   - **rejections**: rows in `LoadReport.rejections` (retained with snippets,
     loader.py:184-186) → rejected-not-lost, bounded separately.
2. **Serving-side exposure census** (independent of norm-serve): for each
   served part token, list `(part_number stamped, pdf_sha)` pairs and flag shas
   joining PART_SHAS under a different family stem (R1 mechanism) — bounds
   curve-API mis-stamping exposure; **UNKNOWN** until run.
3. Until 1–2 run: **impact = UNKNOWN for all paths**. Source susceptibility
   (all paths susceptible by code) ≠ production incidence (mandate (f)).

**Reprocessing promise removed (parent correction 7):** REV2's "this is never
re-extraction" is **withdrawn**. Where retained raw context is missing,
re-extraction may be genuinely required; whether that is needed is part of
what the census determines. This audit makes no load-permission promise
either way. FREEZE unchanged.

---

## 7. Family-export vs staging curve index — separated (parent correction 8)

REV2 collapsed these; corrected:

- **family-export = `export_api_store.py`** (`designwins/crates/dw-extract/
  tools/`): a corpus *export* to ApiStore wire shapes — catalog claims
  (quote+page evidence chain, `page: null` + `match` reason on unlocated
  rows), family records via the live reference grid, cube spine. It does NOT
  serve curves: cube cells carry `curves: False` (export_api_store.py:127,
  176). This path is **not** implicated in the R1 curve leak
  (**SOURCE-CONFIRMED**).
- **staging curve index** = the per-part staging JSON glob inside
  extraction_api.py:218-233 @323d0f80, which feeds `PART_SHAS` — the same
  join R1 exploits. This is the path under audit for wrong-part curve
  exposure (incidence **UNKNOWN** pending §6 census 2).
- Desired end-state (DESIGNED pins, §5 assertions 7/9): accepted per-OPN
  results exclude wrong-part curve rows; flagged wrong-part rows are retained
  as separate raw diagnostics, never silently restamped or merged.

---

## Blockers (unchanged in substance)

1. **Store/lane access**: norm_claims + lane-source JSONL unreadable from this
   lane; §6 census counts **UNKNOWN** until run by the factory owner or via
   read-only dumps. No PG from here.
2. **Artifact**: STM32F051 DocID022265 Rev7 (sha `5cfb0e2e…d3b543`) unfound
   (vault/.54/manifests/11.6 GB tar NEGATIVE); mayor's search continues; NOT
   acquired, per bead directive. Consequences: exact-OPN integration
   **UNPROVEN** (§4), LQFP32 sibling OPN string **UNKNOWN** (§5 row 5), R2
   PDF-reader E2E **unexecuted** (needs a table-bearing fixture).
3. **FREEZE holds**: no PG, no batches; ONE new doc/MPN served with
   provenance remains gated on mayor re-review of PR #66. This audit changes
   no runtime behavior.

---

### Appendix — evidence receipt (REV3)

Carried from REV2 (verified again this revision):
- **TESTED** at 61e95297, `scripts/yw6-repro/yw6_repro.py` + raw
  `scripts/yw6-repro/output.json` (commit `743c18ea` on `curve/mcu-audit-rev`):
  R1 (200/K4 restamp), R3a (opn_default accepted, errors []), R3b (silent
  same-key drop, GROUPBY/HAVING []), Ra decodes (X pin letter, PIC32MX).
- **SOURCE-CONFIRMED** this revision (verbatim, at 61e95297 worktree):
  adapter OPN string-only check (adapter.py:38-41); export_api_store
  `curves: False` (export_api_store.py:127,176) + `page: null` honest-absence
  docstring; staging glob feeding PART_SHAS (extraction_api.py:218-233);
  blob identities unchanged 47cac640↔61e95297.
- NOT run (labeled, not green): R2 PDF-reader E2E, §6 census, exact-OPN
  identity evidence. Parent's in-memory R2 execution is acknowledged as the
  parent's, not duplicated here.
