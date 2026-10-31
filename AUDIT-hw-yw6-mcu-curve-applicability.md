# hw-yw6 — Audit REV 2: MCU curve applicability (investigation + test design ONLY)

Revision of pinned audit `108f6cc236477a797ec4cc788863f6be5e6343d0` per the NEO
REVISION MANDATE on the bead (mandates (a)–(f)). This revision supersedes the
v1 verdicts that NEO marked UNSUPPORTED; v1 text stands superseded, not defended.

Audited at PR66 head `61e952970fab` (extraction/norm-serve-p0-fix). No extractor
runs, no corpus re-extraction, no production writes, no admission changes, no
load unfreeze. This document is the deliverable.

**Evidence base for this revision** — every load-bearing claim below is either
(a) a verbatim code citation at `61e95297`, (b) an actual-path synthetic
reproduction executed for this revision (script + raw output referenced in the
Appendix), or (c) an explicitly marked UNKNOWN. Blob identities verified:

| blob | sha1 | identical 47cac640 ↔ 61e95297 |
|---|---|---|
| `harness/extraction_api.py` | `323d0f804d9cda1c9a6c25775e446bf681d99f29` | YES (git rev-parse both heads) |
| `scripts/extract_typical_curves.py` (curve-anchor) | `8c06f48eff6532da64ac4f14a9ab79def5ee40af` | YES (git rev-parse both heads) |

PR66 touched only `harness/normserve/{adapter,loader,schema}.py` +
`tests/test_normserve.py` (61e95297 diffstat). It did NOT touch the extraction
API, the curve anchor, or any serving path. Per mandate (e), the three serving
paths are assessed separately in §1b.

---

## 0. Correction of v1's "trace is intact" verdict (mandate (1)/(d)/(e))

v1's verdict rested on the norm-serve path alone and on the presence of an
explicit/operator-supplied OPN. **An explicit OPN is not applicability proof.**
The norm-serve lookup (`Store.claims_for_opn`, `WHERE opn=?`,
loader.py:66-68 @61e95297) does establish that a served claim names exactly the
requested OPN — but two upstream serving/conversion paths have now been
reproduced mis-stamping OPNs and roles. The correct verdict is **per-path**:

| path | verdict at 61e95297 | evidence |
|---|---|---|
| norm-serve (adapter→loader→`GET /api/v1/opn/<OPN>`) | OPN lookup correct; **OPN provenance gap** (§3, R3a); same-key silent drop (§4, R3b) | this revision §3–§4 |
| curve API (`GET /api/extraction/v1/parts/<PN>?atom_class=curve`) | **leak reproduced**: shared-SHA K6-only curve served stamped as K4, HTTP 200 | §1b, R1 |
| family-export / staging per-part curve files | **susceptible, not separately reproduced** — same stem→sha join feeds it (extraction_api.py:215-233 @323d0f80); incidence UNKNOWN pending census | §1b, §6 |
| `_table_facts` curve-anchor conversion | **max→typ relabeling reproduced by citation** — role discarded at the facts boundary | §1b, R2 |

## 1b. Actual-path synthetic reproductions (executed this revision, at 61e95297)

All reproductions are synthetic (no corpus PDF, no PG, no extractor run over
any real document). Script: `scripts/yw6-repro/` design below; raw output
archived with this audit's commit. Environment: repo venv Python 3.12.

**R1 — shared-SHA K6-only curve requested as K4 returns HTTP 200 stamped K4.**
Mechanism (verbatim, `harness/extraction_api.py` @323d0f80):
- line 96-99: `PART_SHAS` is a "Global part-stem -> [doc sha]" join built from
  delivery manifests and curve-lane source stems — a *family/stem*-grain index;
- lines 368-374: for a requested part the handler unions catalog shas with
  `PART_SHAS.get(part)` ("ALWAYS union with our own joins");
- lines 379-386: each curve row from ANY unioned sha is stamped
  `{**cr, "part_number": part}` — the *requested* part, not the source doc's part.
Reproduction: seeded `PART_SHAS["STM32F051K4"] = [synthetic_sha]` +
`CURVES_BY_SHA[synthetic_sha] = [one K6-only synthetic curve row]`, issued the
real HTTP request through the real `Handler` with bearer auth.
**Observed: `status=200`, `part_number="STM32F051K4"`, `row_count=1`,
row `pdf_sha` = the synthetic shared sha.** The served row is a K6-document
curve wearing a K4 label. PR66 did not touch this path (blob unchanged).

**R2 — `_table_facts` converts max to typ (mandate (e); citation + design,
PDF-level repro not executed — see Blockers).**
`scripts/extract_typical_curves.py:65-106` @8c06f48e: `mode_current_rows(...)`
returns rows that carry a printed role — the reader parses `min|typ|max|nom`
roles (`harness/electronics/mode_currents.py:44-77`, `_ROLE_HEADER` /
`_role_of`). But the facts boundary discards it:
```python
facts.append({
    "field": f"{row['mode']} current",
    "typ": row["value"],          # extract_typical_curves.py:98 — role dropped
    ...
})
```
Every mode-current value — including a `max`-role row — is filed under the key
`"typ"`. Downstream consumers of `_FACTS_CACHE` therefore see max printed
values as typical. This is a **role-relabeling defect, not an invented value**
(the number itself is verbatim from the row); severity is mis-scoped evidence,
violating "conditions never defaulted / roles preserved" (curve_evidence.py
docstring:9-11). The PDF-level end-to-end repro requires a table-bearing
synthetic PDF fixture; design is given in §7 (R2 case). Not executed here —
marked as designed-but-not-run (P5).

**R3a — adapter accepts an unevidenced OPN via `opn_default` (mandate (d)).**
`harness/normserve/adapter.py:38-41` @61e95297:
`opn = (raw.get("opn") or opn_default or "").strip().upper()` — an
operator-supplied default stamps the OPN with no document binding to it.
Reproduction: one synthetic fully-provenanced row (64-hex sha, verbatim,
page, condition, verdict, `rule_version`) with **no** row-carried `opn`,
loaded with `opn_default="STM32F051K6U7"`:
**Observed: `typed["opn"] == "STM32F051K6U7"`, `validate_typed_row` → `[]`
(zero validation errors).** The row then serves under K6U7 forever; nothing in
the stored row records that the OPN was operator-supplied rather than
document-bound. The v1 sentence "family rows with no OPN are rejected, not
folded into a family OPN" is true only for the no-default case. This is the
"accepts K6U7 applicability with opn_default=UNRELATED-OPN, validation errors
[], then drops applicability" reproduction NEO cited — now confirmed
independently on the fixed head. Note the applicability *loss* is the inverse
face of the same defect: the doc is the ten-part F051 family sheet, so a K6U7
row inherits family-document evidence K6U7's own datasheet may not carry.

**R3b — same-key collision silently drops the second row; GROUPBY/HAVING
cannot see it (mandate (d)).**
`loader.py` @61e95297: `INSERT OR IGNORE` (insert, line ~63-72); collision
detected only by row-count delta (load_rows: `if after == before:
report.duplicates += 1`); no rejection record is emitted for the dropped row.
Reproduction: two fully-provenanced synthetic rows, identical
`(opn, doc_sha, field, value_role, condition_verbatim)` = identical UNIQUE key
(schema.py:47-50), differing only `page` (50 vs 51) and `value` (22.0 vs 23.8):
**Observed: `loaded=1, rejected=0, duplicates=1`; stored =
`[(22.0, page 50)]` — 23.8@p51 is gone; the proposed census query
`SELECT opn, doc_sha, field, COUNT(*) ... GROUP BY 1,2,3 HAVING c > 1`
returns `[]` despite the proven drop.** Exactly as NEO stated: the v1 census
query is incapable of detecting this loss class.

**Ra — X-character ban is invalid (mandate (a)).**
`harness/electronics/ordering_codes.py` @61e95297: `"X": 240` is a legitimate
ST pin letter (line ~34, `ST_PIN_LETTER`), and PIC32MX is a supported scheme
(lines ~55-56, `_PIC32 = re.compile(r"^PIC32M[XZK]...")`). Executed:
`decode("STM32F051K6UX")` → `{'scheme': 'stm32', 'flash_kb': 32,
'pin_count_nominal': 32}`; `decode("PIC32MX795F512L")` → `{'scheme': 'pic32',
'flash_kb': 512, 'pin_count': 100}`. v1's "reject OPNs containing X" (L109-115)
is **withdrawn** — it would ban real parts. Replaced by the typed
classification in §5.

## 2. Reference-manual vs datasheet authority (v1 §2 retained, F1 stands)

Unchanged from v1 (re-verified at 61e95297): `document_class` is printed-
front-matter-only (document_scope.py:97-105, 203-217) and is **not** a typed
`norm_claims` column (schema.py:16-41). Family membership cannot infer
electrical/package evidence structurally — norm-serve requires an explicit
`opn` per row — but R3a shows the explicit OPN may be *operator-supplied* with
no document binding, which weakens the guarantee to "explicit", not
"evidenced". F1 recommendation extended: when `document_class` is added as a
typed column, also add `opn_provenance ∈ {row_carried, operator_default}`
(default REJECT in persist mode unless explicitly acknowledged). Family
authority matrix + document_scope remain the counterevidence layer (mandate (f)).

## 3. Per-value conditions (mandate (c) — fixture corrected)

PDF-grounded facts (NEO-supplied, DocID022265 Rev7, sha
`5cfb0e2e…d3b543` — artifact still NOT acquired, all fixtures synthetic):
- **22 mA typ @ TA=25 °C** — IDD RUN SUPPLY CURRENT, Table 25 p50, condition
  inherited from §6.1.2 p42 context;
- **23.8 mA max @ TA=105 °C** — same table, own condition row.

v1's `io_sink_current` / "Output current sunk" relabeling is **retracted**;
v1's invented 20 mA @125 °C third row is **removed**. Grade-7's 125 °C
ambient conditional on T_J ≤ 125 °C + low dissipation enters the design as a
**condition-only row (value UNKNOWN)** — the condition string is third and
distinct; no value is attributed to it in fixtures.

Schema behavior at 61e95297 (re-verified): `condition_verbatim` passes through
untouched (adapter.py:9-11 docstring contract; adapter copies verbatim); UNIQUE
key includes `value_role` + `condition_verbatim` (schema.py:47-50), so the two
PDF-grounded facts are two rows, never merged — **when they survive the load**
(R3b shows the same-key loss class; here roles+conditions differ, so both
retain). R3b's variant also proved the distinct-condition pair loads as 2 rows.

Exact replay = DUPLICATE receipt, not rejection (mandate (c)): current
behavior reports `duplicates+1, rejected=0` — that is the correct *duplicate*
semantic and should be kept, but each duplicate receipt must also record the
full incoming row (see §7 assertion 4) so a distinct-observation collision is
an explicit RETAINED CONFLICT rather than a silent drop.

## 4. Variant / package / grade crosscutting (mandate (b) — OPN set corrected)

The family table crosscut facts stand (v1 §4, family_device_matrix.py:8-13,
23-27): K6 UFQFPN32 GPIO=27 vs the LQFP32 sibling GPIO=25; K6 USART=2 vs
K4 USART=1. Package identity is inside the OPN, so norm-serve grain
(OPN+sha+field+role+condition) preserves per-package values **iff the rows are
bound to the right OPNs and the OPNs are evidenced** (R3a is the open wound:
an opn_default row is per-OPN in name only).

Mandate (b): the regression design no longer treats bare `STM32F051K6` as a
complete OPN. §7 uses **two verified OPNs + a package pair**:
- OPN A: `STM32F051K6` (UFQFPN32, 32-pin, GPIO=27, USART=2)
- OPN B: `STM32F051K4` (same family sheet, USART=1)
- Package pair: K6 UFQFPN32 (GPIO=27) vs the LQFP32 sibling (GPIO=25) — the
  LQFP32 sibling's exact OPN string is **UNKNOWN** pending the artifact
  (bead supplies the counts, not the ordering code); the test takes it as a
  parameter and is marked `skip` until the artifact supplies it. No OPN is
  invented.

## 5. Wildcard / X--character policy (mandate (a)+(3) — v1 F3 replaced)

No character is banned. Classification, never rejection-by-spelling:

- **evidenced-exact OPN**: decodes via `ordering_codes.decode` AND the row's
  `evidence_ref`/verbatim binds the full ordering string in the document
  (header token, order-code list, or device-summary join). Serves as-is.
- **wildcard OPN** (family/temperature-range wildcard in a value-bound row,
  e.g. trailing `x` in a *variant-binding position*): loadable but typed —
  required field `opn_is_wildcard: true` + `wildcard_scope` (which positions
  are open). Serves with the wildcard flag on every claim; consumers must not
  treat it as a single-part answer.
- **undecodable OPN**: reject with reason (existing adapter path).

Legitimate family coverage is preserved: a wildcard row covering the F051
family is still loadable and still counterevidence (mandate (3)) alongside
document_scope / family matrix / spine / richer CurveEvidence. This is a
recommendation; no admission change is made in this audit (boundaries hold).

## 6. Bounded affected-record / reprocessing scope (mandate (2) — census required)

v1's "zero reprocessing scope" is **withdrawn**; affected counts are **UNKNOWN**.
`INSERT OR IGNORE` destroys the dropped observation at insert time — stored-row
counts cannot recover what was never retained (R3b: the 23.8@p51 row leaves no
trace in norm_claims). The census must therefore reconcile **retained raw /
rejection / attempt evidence** against stored rows:

1. **Retained-raw candidate census** (read-only, when store access is
   authorized): for each lane-source JSONL that ever fed norm-serve, count
   rows per `(opn, doc_sha, field, value_role, condition_verbatim)` in the
   RAW file; compare against the same key count in norm_claims. Any raw key
   with n_raw > n_stored is a **proven drop** (this is the query class R3b
   shows the stored-side GROUPBY cannot substitute for).
2. **Rejections-ledger reconciliation**: `LoadReport.rejections` rows are
   retained with snippets (loader.py) — count them per (opn, doc_sha) to
   bound rejected-not-lost vs dropped-silently.
3. **Serving-side exposure census** (independent of norm-serve): for each
   part token served by extraction_api, list `(part_number stamped, pdf_sha)`
   pairs and flag any sha that joins PART_SHAS under a *different* family
   stem (R1 mechanism). This bounds the curve-API mis-stamping exposure
   without any reprocessing.
4. Until 1–3 run, **impact = UNKNOWN for all paths** (P5). Source
   susceptibility (all three paths are susceptible by code) ≠ production
   incidence (mandate (f)) — incidence is exactly what the census measures.
5. Reprocessing, if the census finds real drops: append-only reload of the
   affected (opn, doc_sha) lane files after the loader emits
   duplicate-conflict records — replay is idempotent (replay=0-new-rows test,
   tests/test_normserve.py), so this is never re-extraction.

## 7. ONE regression design (test design only — not implemented in this audit)

**Case: `test_f051_applicability_regression`**
(`tests/test_normserve_f051_regression.py`; synthetic fixtures only; two
verified OPNs + a package pair per mandate (b); PDF-grounded values per
mandate (c)).

Fixtures (synthetic doc_sha = sha256 of fixture bytes; vault-blob layout as
loader expects):

| # | opn | value | value_role | condition_verbatim | page | provenance |
|---|-----|-------|-----------|--------------------|------|------------|
| 1 | STM32F051K6 | 22 | typ | `TA = 25 °C` (§6.1.2 p42 inherited) | 50 | verbatim `22 mA typ @ TA = 25 °C`, rule_version set |
| 2 | STM32F051K6 | 23.8 | max | `TA = +105 °C` | 50 | verbatim `23.8 mA max @ TA = +105 °C` |
| 3 | STM32F051K6 | *no value* | — | `TA = +125 °C, T_J ≤ 125 °C, low dissipation` | 51 | condition-only probe; expects value UNKNOWN, not stored as a claim |
| 4 | STM32F051K4 | 22 | typ | `TA = 25 °C` | 50 | family-sheet row bound to K4 (USART=1 sibling) |
| 5 | package-pair row | — | — | — | — | LQFP32 sibling GPIO=25 — parameterized, `skip` until artifact |

Assertions:
1. Rows 1, 2, 4 load (loaded=3, rejected=0); `claims_for_opn("STM32F051K6")`
   returns rows 1+2 with distinct (value_role, condition_verbatim); K4's claim
   is NOT returned for K6 and vice versa.
2. Row 3 is not stored as a value claim (condition-only, no invented value);
   its condition string appears in no stored row's value fields.
3. **Same-key conflict is a RETAINED CONFLICT**: a fifth row identical to row 1
   on the full UNIQUE key but value 22.5, page 53 → loader reports
   `duplicates+1` AND emits a conflict record carrying the full incoming row
   (requires the §3 loader recommendation; test pins desired behavior).
4. **Exact replay is a DUPLICATE receipt, not a rejection**: replaying rows
   1–4 verbatim yields loaded=0, duplicates=4, rejected=0, and each duplicate
   receipt names the replayed row (provenance-complete), so replay audit is
   reconstructible.
5. **Wildcard typed, not banned**: a row bound to wildcard `STM32F051K6Ux`
   loads only with `opn_is_wildcard=true` + `wildcard_scope`, and
   `decode("STM32F051K6UX")` still succeeds (ordering_codes.py X pin letter);
   a bare `STM32F051K6X`-style *undecodable* string is rejected with reason.
6. **opn_default is recorded**: loading with
   `opn_default="STM32F051K6U7"` stores `opn_provenance="operator_default"`
   (requires §2 recommendation; pins desired behavior). Until implemented,
   the test asserts the CURRENT defect as a documented-known-behavior guard
   (xfail with reason R3a) so the gap cannot regress silently.
7. **R1 pin (curve API)**: seeding `PART_SHAS["STM32F051K4"]` with a sha whose
   only curve rows are K6-bound and requesting K4 must return either 404/zero
   rows or rows carrying their true source-part binding
   (`row["source_part_number"] != requested` flag) — never a silent
   `part_number` restamp (requires serving fix; pins desired behavior).
8. **R2 pin (anchor conversion)**: a table-facts fixture carrying a `max`-role
   mode-current row must surface in `_table_facts` output under a role-faithful
   key — never `"typ"` (requires extract_typical_curves.py:98 fix; pins
   desired behavior; synthetic PDF fixture per Blocker 2).

## Blockers

1. **Store/lane access (unchanged):** norm_claims + lane-source JSONL are not
   readable from this code lane. §6 census counts are **UNKNOWN** until the
   factory owner runs them (or provides read-only dumps). No PG from here.
2. **Artifact (unchanged):** STM32F051 DocID022265 Rev7 (sha `5cfb0e2e…`)
   still unfound (vault/.54/manifests/11.6 GB tar full-scan NEGATIVE);
   mayor's search continues; **not acquired**. Consequently: the LQFP32
   sibling's exact OPN is UNKNOWN (§4/§7 row 5), and the R2 PDF-level
   end-to-end repro (§1b) was **designed but not executed** — `_table_facts`
   needs a table-bearing PDF fixture to run the actual reader; the
   role-dropping mechanism is cited at extract_typical_curves.py:98
   (blob 8c06f48e, verified unchanged) and the repro script is included with
   this commit for whoever holds a fixture.
3. **FREEZE holds:** no PG, no batches; one new doc/MPN served with
   provenance remains gated on mayor re-review of PR #66. This audit does
   not unblock loads and changes no runtime behavior (repro scripts are
   additive test assets).

---

### Appendix — reproduction receipt (this revision)

- Script: `scripts/yw6-repro/yw6_repro.py` (committed with this audit; runs
  read-only synthetic fixtures against the 61e95297 worktree; env:
  repo venv py3.12, `DISCOVERY_CATALOG` pointed at an empty temp catalog,
  `CR_API_KEYS` temp value).
- Raw output committed alongside: `scripts/yw6-repro/output.json`. Key lines:
  - R1: `"R1_status": 200, "R1_part_number": "STM32F051K4", "R1_row_count": 1`
  - R3a: `"R3a_typed_opn": "STM32F051K6U7", "R3a_validation_errors": []`
  - R3b: `"loaded": 1, "rejected": 0, "duplicates": 1`; `"R3b_stored":
    [[22.0, 50]]`; `"R3b_groupby_having": []`
  - Ra: `STM32F051K6UX → {'scheme': 'stm32', 'flash_kb': 32,
    'pin_count_nominal': 32}`; `PIC32MX795F512L → {'scheme': 'pic32',
    'flash_kb': 512, 'pin_count': 100}`
- What was NOT run: R2 PDF-level repro (no table-bearing fixture — Blocker 2);
  §6 census (no store access — Blocker 1). Both explicitly UNKNOWN, not green.
