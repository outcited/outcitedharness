# hw-yw6 — Audit: MCU curve applicability — REVISION 2 (investigation + test design ONLY)

**Revision of the audit pinned at `108f6cc2`**, per the NEO revision mandate in
bead hw-yw6 (2026-10-31). Every mandate item is addressed below with code
citations re-verified this session at PR66 head `61e952970fab`
(extraction/norm-serve-p0-fix). Audited-code blobs:

- `harness/extraction_api.py` = `323d0f804d9cda1c9a6c25775e446bf681d99f29`
- `scripts/extract_typical_curves.py` = `8c06f48eff6532da64ac4f14a9ab79def5ee40af`

Both blobs are **byte-identical at `47cac640`, `61e95297`, and `origin/main`**
(verified via `git rev-parse <rev>:<path>` this session). PR66 touched only
`harness/normserve/{adapter,loader,schema}.py` and `tests/test_normserve.py`
(`git diff --stat 47cac640 61e95297`). Therefore the shared-SHA leak and the
max→typ relabel **still apply at PR66 head** — stated per-path in §1.

Scope unchanged: investigation + test design ONLY. No extractor runs, no
re-extraction, no production writes, no migrations, no admission changes, no
load unfreeze. Synthetic fixtures only; artifact `5cfb0e2e…d3b543` was NOT
acquired.

---

## 0. Retractions and corrections carried out (mandate items)

| Mandate | Correction made in this revision |
|---|---|
| (1) "trace is intact" was UNSUPPORTED | Retracted. §1 separates **three serving paths** (normserve / extractionAPI curves / family export) and §3 specifies independent actual-path reproductions for the two defective ones. Normserve's per-OPN lookup does NOT establish the correctness of the other paths. |
| (2) "zero reprocessing scope" was UNSUPPORTED | Retracted. §6 replaces it with a **census design**; affected counts remain **UNKNOWN** until the census runs. `INSERT OR IGNORE` has already discarded observations that stored-row counts cannot recover. |
| (3) blanket X-wildcard ban was INVALID | Retracted. §4 replaces the F3 recommendation: classify evidenced-exact vs wildcard OPN; **never ban a character**. `X` is a legitimate ST pin letter. |
| (4) fixture relabeling (`io_sink_current`, invented 20 mA row) | Removed. §5 uses the actual PDF-grounded facts: **IDD (RUN SUPPLY CURRENT) 22 mA typ @ TA=25 °C** (Table 25, condition inherited from §6.1.2 p42 context) and **23.8 mA max @ TA=105 °C**. No third value is asserted. Synthetic plumbing fixtures are explicitly labeled as such. |

---

## 1. Trace, per path (mandate (e): state each path separately)

Family/reference doc → multiple OPNs now traces through **three distinct
serving paths** with different guarantees:

### Path A — normserve (`GET /api/v1/opn/<OPN>`)

- `document_scope.py:151-270` reads scope verbatim from front matter
  (`scope_statement`, `governs_series`, 254-257); `scope_absent` is a
  first-class answer (17-18, 262-263); filename is never used (docstring 3-6).
- `family_device_matrix.py:8-19` binds values to a part only when the
  document binds it; unbindable columns stay `part_number: null`, "never a
  minted part number".
- `normserve/adapter.py:35`: `opn = (raw.get("opn") or opn_default or
  "").strip().upper()` — the typed row **requires** an explicit or
  operator-supplied OPN (:37 rejects empty), so a family row cannot be
  folded into a family OPN silently.
- `normserve/loader.py:63-66` (`claims_for_opn`, `WHERE opn=?`) →
  `normserve/serve.py:18-33` serve pure per-OPN lookups.
- **Verdict for Path A: structurally per-OPN**, with two defects that DO
  apply inside this path (F2 loader silent drop, §2; F3 applicability gate,
  §4). "Trace intact" was over-claimed: adapter.py:35 accepts an
  `opn_default` **without checking the row's document actually covers that
  OPN** — at `61e95297`, a K6-row with `opn_default="UNRELATED-OPN"` produces
  a typed row with `opn=UNRELATED-OPN`, zero validation errors, and the
  row's real applicability is silently dropped. Reproduction T4 (§3).

### Path B — extractionAPI curve serving (`GET /api/extraction/v1/parts/<part>`)

**Defective: shared-SHA cross-OPN leak, unchanged by PR66** (blob
`323d0f80…` identical before/after):

- Curves are indexed by document sha: `CURVES_BY_SHA` (extraction_api.py:385).
- `part_shas` for the requested part is assembled from: the power catalog
  (`part_sources WHERE opn=?`, :364-365), a fallback of **any** doc_sha
  joined to the requested opn token (`SELECT doc_sha256 FROM
  claims_canonical WHERE opn=? LIMIT 1`, :368-369), and manifest stem joins
  that **always** union in (`:376-378`, comment: "same datasheet, different
  file, different sha").
- Every curve row found under any of those shas is then **stamped with the
  requested part**: `rows.append({**cr, "part_number": part})` (:389); same
  for errata (:392); the response body asserts `"part_number": part` and
  `"pdf_sha": part_shas[0]` (:402-404).

**Consequence (actual path, not hypothetical):** a document whose curves
were extracted under K6 only, requested as K4, returns **HTTP 200 with the
K6 curve stamped `part_number: K4`**. The stamp is minted at serve time from
the URL token — no per-row OPN evidence is consulted.

### Path C — family export / document_scope matrix

`document_scope.py` + `family_device_matrix.py` export family-grain rows
with `part_number: null` + header tokens preserved. This path does not mint
per-OPN electrical claims and remains the **counterevidence source** per
mandate (f): a wildcard-token column bound by the document, the family
matrix, or the spine keeps valid coverage from being discarded when §4's
classification lands.

---

## 2. Loader silent drop — restated with the actual loss shape (mandate (d))

At `61e95297` (schema.py:47-50, 84-87): the UNIQUE key is
`(opn, doc_sha, field, COALESCE(value_role,''), COALESCE(condition_verbatim,''))`
— **`page` is not in the key**. `loader.py:58` uses `INSERT OR IGNORE`;
`loader.py:113-116` detects the ignore by a count delta and bumps
`report.duplicates`. Therefore:

- Two rows identical on the key but printed with **different values on
  different pages** (e.g. 22 @ page 50, then 23 @ page 51, same role, same
  condition text) → the second is **silently discarded**; only a counter
  bumps; no rejection record names it.
- The previously proposed census `GROUP BY opn, doc_sha, field HAVING
  COUNT(*) > 1` runs **against stored rows** — since the loser was never
  stored, this query **returns `[]` even where loss provably occurred**.
  That query is retired as a census instrument (mandate (d)).
- Exact replay of an identical row is a **DUPLICATE receipt** (counter),
  not a rejection — correct for idempotence, but indistinguishable from the
  loss case above. A distinct-observation collision must be recorded as an
  explicit **RETAINED CONFLICT**, not a bare counter (§5, assertion A5).

---

## 3. Required synthetic reproductions (test designs — actual code paths, no extractor runs)

All fixtures synthetic; PDF-grounded values per §5. Each test states the
**current** behavior (verified by reading the cited lines) and the **desired
pinned** behavior.

- **T1 — shared-SHA K6→K4 leak (Path B).** Fixture: catalog DB with
  `claims_canonical` row for K6 under sha S; `CURVES_BY_SHA[S]` containing
  one K6 curve. `GET /api/extraction/v1/parts/STM32F051K4` (K4 absent from
  `part_sources`). Current: HTTP 200, row stamped `part_number: K4`
  (extraction_api.py:368-369, 376-389). **Pin desired: 404 or a row that
  carries its true K6 provenance; the URL token must never stamp a row.**
- **T2 — max→typ relabel (curve lane).** Fixture: one mode-current table
  row printing both typ and max columns. `mode_current_rows` correctly emits
  per-column `value_role` (mode_currents.py:289). Current:
  `scripts/extract_typical_curves.py:98` writes `"typ": row["value"]`
  discarding the role, and `typical_curves.py:546`
  (`fact.get("typ", fact.get("value"))`) then prefers that key — **a max
  value is fed to curve anchor checking as if typ**. **Pin desired: the
  fact dict preserves `value_role`; anchor matching distinguishes typ vs
  max facts and never treats a max fact as the typical anchor.**
- **T3 — loader same-key distinct-value loss (Path A).** Fixture: 22 mA
  typ row @ page 50, then 23.8 mA row @ page 51, same key otherwise.
  Current: second silently dropped, `duplicates=1`, stored value 22;
  `GROUP BY … HAVING COUNT(*)>1` returns `[]` (proves invisibility).
  **Pin desired: RETAINED CONFLICT record emitted naming both rows.**
- **T4 — adapter `opn_default` applicability (Path A).** Fixture: K6-row
  with no `opn`, `opn_default="UNRELATED-OPN"`. Current: typed row accepted,
  `validate_typed_row` errors `[]` (adapter.py:35, loader.py:103-105).
  **Pin desired: applicability gate — an operator default may fill
  provenance for a Sam-directed sample but must be recorded as
  operator-supplied and checked against the document's scope/OPN list
  before serving; an unrelated default is a rejection, not a silent
  rebinding.**
- **T5 — exact replay = DUPLICATE receipt.** Same fixture loaded twice:
  `loaded=0, duplicates=N`, no rejections (preserves the replay=0-new-rows
  property from PR66), AND each duplicate is distinguishable from a
  distinct-observation collision (T3) by the presence/absence of a
  conflict record.
- **T6 — wildcard classification, no character ban.** `"STM32F051K6UX"`
  and an evidenced-exact OPN both parse under `ordering_codes.py` — `X` is
  a legal ST pin letter (`"X": 240`, ordering_codes.py:34) and
  `PIC32M[XZK]…` (:56) proves X occurs in real families. **Pin desired: a
  classification field (`evidenced_exact` vs `wildcard`) decided by
  document binding (document_scope / family matrix / spine), never by
  character content of the token.**

---

## 4. Wildcard OPNs — classification, not rejection (mandate (a), (3), (f))

The pinned audit's "reject OPNs containing X" recommendation is **retracted**
as invalid policy: `ordering_codes.py:34` defines ST pin letter `X` = 240
pins, and `ordering_codes.py:56` matches `PIC32M[XZK]` — a blanket X-ban
would reject legitimate parts and contradicts the grammar the codebase
already encodes.

Replacement design: every OPN token arriving at the adapter is classified —
- **evidenced-exact**: the row's document (or document_scope binding /
  family-matrix device-summary join / spine) binds the complete OPN;
- **wildcard/underbound**: the token carries an ordering wildcard or the
  binding is only partial (header token, series).

Wildcards are **retained** with the classification attached and the
document-bound counterevidence kept (mandate (f)) — they serve only as
family-grain claims, never as per-OPN electrical evidence. Source
susceptibility is not production incidence: whether any wildcard row exists
in the store is a census question (§6), not assumed.

---

## 5. Regression case design — PDF-grounded facts, ≥2 verified OPNs + package pair (mandate (b), (c), (4))

**Case: `test_f051_idd_conditions_are_distinct_rows`** (design only; new
file `tests/test_normserve_f051_regression.py`).

PDF-grounded values (STM32F051 DocID022265 Rev7, sha `5cfb0e2e…d3b543`,
NEO-validated example; artifact absent everywhere searched — vault / .54
deep dirs / manifests / 11.6 GB tar full-scan all NEGATIVE — and NOT
acquired, per bead direction):

| # | quantity | value | value_role | condition | source |
|---|----------|-------|-----------|-----------|--------|
| 1 | IDD (RUN SUPPLY CURRENT) | 22 mA | typ | TA = 25 °C — section context inherited from §6.1.2, p42 | Table 25, p50 |
| 2 | IDD (RUN SUPPLY CURRENT) | 23.8 mA | max | TA = +105 °C | Table 25, p50 |
| 3 | grade-7 125 °C row | (value per artifact) | — | 125 °C ambient **conditional on T_J ≤ 125 °C + low dissipation** | Table 25 footnote |

No third numeric value is asserted from memory. Row 3's value and row-2's
exact page-local context are to be pinned from the artifact when mayor's
search finds it; until then row 3 participates as a **condition-distinctness
fixture** (its `condition_verbatim` differs, therefore it must be a separate
row), with its numeric value explicitly `UNKNOWN` — never invented.

**OPNs (mandate (b)):** the regression exercises **two verified base OPNs**
from the datasheet's ten-base-part example — **STM32F051K6 and STM32F051K4**
— each in **complete OPN form** (package/temp suffix from the ordering
information table), plus a **package pair**: K6 in UFQFPN32 (GPIO = 27,
USART = 2) vs K6 in LQFP32 (GPIO = 25). The concrete suffixes (e.g.
`STM32F051K6Ux` → exact orderable code) are **UNKNOWN until the artifact is
found** — the test parameterizes on them and carries an explicit
`pytest.skip` reason tied to artifact discovery rather than inventing
suffixes. Package-count asymmetries (GPIO 27 vs 25; K6 USART 2 vs K4
USART 1) assert that per-OPN serving never lets a K6 count answer a K4
query (and vice versa) on **any** of the three paths in §1.

**Assertions:**
1. A1: rows 1 and 2 load as **distinct rows** — different `value_role`
   (typ vs max) and different `condition_verbatim`; `condition_verbatim`
   passes through verbatim (adapter.py:9-11 passes untouched).
2. A2: `claims_for_opn` on K6-complete returns both; on K4-complete returns
   only K4 rows; package pair asserts GPIO count answers per package OPN.
3. A3: exact replay of the fixture → `loaded=0, duplicates=N`, zero
   rejections — **DUPLICATE receipt semantics** (mandate (c)).
4. A4: same-key distinct-value pair (22 @ p50 vs different value @ p51) →
   **RETAINED CONFLICT record** naming both rows (requires §2 fix; pins
   desired behavior).
5. A5: T1/T2/T4 reproductions from §3 run in the same file as path-level
   regressions.

---

## 6. Census design — replaces "zero reprocessing scope" (mandate (2), (d))

Affected counts are **UNKNOWN** and stay UNKNOWN until a census runs. Stored
row counts cannot recover observations already discarded by
`INSERT OR IGNORE` (loader.py:58) — the loser never reached the table, so
no stored-side query can find it. The census therefore reconciles
**retained raw evidence against stored rows**, read-only:

1. **Candidate universe**: enumerate every lane-source row that ever
   reached `load_rows` — retained raw JSONL lane files, load reports /
   attempt ledgers (the rejections list at loader.py:121-132 retains only
   rejected rows, **not** silently-ignored duplicates), and any
   `duplicates` counters in historical LoadReports.
2. **Reconciliation**: for each raw candidate, recompute the UNIQUE key
   (schema.py:47-50) and compare its (value, page) against the stored row
   with that key. A mismatch = a proven loss. This is done against **raw +
   stored** artifacts, not `GROUP BY` on the store alone (§2: that query
   provably returns `[]` despite loss).
3. **Scope bound**: reprocessing, if losses are found, is an append-only
   re-**load** of the affected (opn, doc_sha) JSONL lane files after the
   RETAINED-CONFLICT loader fix — replay is idempotent (PR66 replay
   property), so this is never re-extraction. F1/F4 hardening and the §4
   classification carry **no** reprocessing scope of their own beyond what
   the census proves.
4. **Store access remains a blocker** (below): the census SQL/file
   reconciliation is specified here; execution needs the read-only store
   dump the factory owner must provide.

---

## Blockers

1. **Store access**: production rows unreadable from this code lane; census
   (§6) cannot execute here — affected-record impact UNKNOWN (P5).
2. **Artifact absence**: DocID022265 Rev7 (`5cfb0e2e…d3b543`) unfound
   everywhere searched; mayor's search continues. Complete-OPN suffixes and
   Table 25 row-3 value remain UNKNOWN; fixtures stay synthetic; no copy
   acquired.
3. **FREEZE holds**: no PG, no batches; one new doc/MPN served with
   provenance remains gated on mayor re-review of PR #66. This audit does
   not unblock loads.
4. **Coordination**: hw-x9g (PR66 corrections round 2) keeps lane priority;
   this revision does not touch any code it may be repairing.
