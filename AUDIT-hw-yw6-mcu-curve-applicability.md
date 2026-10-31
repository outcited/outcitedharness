# hw-yw6 — Audit: MCU curve applicability (investigation + test design ONLY)

Audited at PR66 head `61e952970fab` (extraction/norm-serve-p0-fix), per bead
note: "audit THE FIXED code where relevant, do not re-flag repaired defects."
Blocked-by hw-c1z satisfied (closed 06:31:21Z, PR #66). No extractor runs,
no corpus re-extraction, no production writes, no admission changes, no load
unfreeze — this document is the deliverable.

Synthetic fixtures from the validated STM32F051 example values (DocID022265
Rev7, sha `5cfb0e2e…d3b543`) are used below; the artifact itself is absent
from all searched stores (vault/.54/manifests/tar full-scan NEGATIVE) and was
NOT acquired, per bead direction.

---

## (1) Trace: family/reference document → multiple OPNs → extraction → norm-serve → served JSON

The path is layered so that family-grain documents never mint per-OPN rows:

- **Scope reading** — `harness/electronics/document_scope.py:151-270`
  (`read_scope`): the cover-page scope statement is captured verbatim
  (`scope_statement`, `governs_series`, document_scope.py:254-257). Filename
  binding is never used ("filename binding lies", module docstring lines 3-6).
  `scope_absent` is a first-class answer (lines 17-18, 262-263) — never a
  guess. Composite tokens are preserved verbatim, never expanded (lines 59-61).
- **Family matrix extraction** —
  `harness/electronics/family_device_matrix.py:1-31`: family tables produce
  `part_number: null` + `family` + `part_numbers_covered`; values land in the
  `variants` knife layer "bound to a concrete part number only when the
  document itself binds it (header token, header code list, or a printed
  device-summary join)" (lines 8-13). Unbindable columns stay
  `part_number: null` with the header token kept, "never a minted part
  number" (lines 17-19).
- **Norm-serve ingestion** — `harness/normserve/adapter.py:35-37`: every typed
  row REQUIRES an explicit `opn` (row-carried or operator-supplied default);
  a family row with no OPN is rejected, not folded into a family OPN. So the
  many-to-many OPN resolution (family doc covering ten STM32F051 base parts)
  is realized as: one `doc_sha` (5cfb0e2e…) × N explicit `opn` rows — the
  grain is OPN+sha+field+role+condition (`harness/normserve/schema.py:47-50`),
  which is fine-grained enough for per-OPN variants (finding F3 below notes
  one residual gap).
- **Served JSON** — `harness/normserve/loader.py:63-66`
  (`Store.claims_for_opn`, `WHERE opn=?`) → `harness/normserve/serve.py:18-33`
  (`GET /api/v1/opn/<OPN>`, pure key lookup, no LLM at serve time). A served
  claim therefore always names exactly one OPN and one doc_sha, with page +
  verbatim + verified_by + rule_version required (schema.py:12-13,
  validate_typed_row schema.py:91-120).

**Verdict: trace is intact.** Family membership enters only via explicit
per-OPN binding at extraction time; the serve layer cannot widen it.

## (2) Reference-manual vs datasheet authority

- `document_class` is printed-front-matter-only
  (document_scope.py:97-105, 203-217) with `document_subject` separating
  `device_family` / `cpu_core` / `peripheral` (lines 107-148) — the PM0214
  core-manual trap and CR's GD32F403 near-miss are both encoded as tests of
  this reader.
- **Gap (F1): document_class is not carried into norm_claims.** The typed
  row has `table_kind`, `verdict`, `evidence_ref` — but no `document_class`
  / `document_subject` column (schema.py:16-41). Today the power-axes lane
  only ingests datasheet rows, so nothing infers family→electrical evidence
  at serve time; but the schema would admit a reference-manual-sourced row
  without marking its authority class. Family membership never infers
  evidence **structurally** (adapter requires explicit OPN; family rows are
  `part_number: null` and rejected at adapter.py:35-37), but authority
  provenance is implicit in `rule_version`/`schema` rather than a typed
  field. Recommendation: add `document_class` (and `governs_scope_sha` or
  `document_subject`) as optional-but-validated columns before any lane
  other than datasheets feeds norm-serve. Not a P0 — no current lane can
  trip it.

## (3) Per-value conditions survive as distinct rows

- `condition_verbatim` is stored verbatim, passed through untouched
  (adapter.py:9-11, 64; no normalization, no defaulting).
- The uniqueness key **includes** `value_role` and `condition_verbatim`
  (schema.py:47-50), so:
  - `22 mA typ @ TA=25 °C` (condition inherited from §6.1.2 p42 context) and
  - `23.8 mA max @ TA=105 °C`
  land as **different rows** — different `value_role` (typ vs max) and
  different `condition_verbatim`. Never merged.
- Grade-7's `125 °C ambient conditional on T_J ≤ 125 °C + low dissipation`
  is a third distinct `condition_verbatim` string → third row.
- **Gap (F2): same-role-same-condition collisions are silently dropped.**
  `INSERT OR IGNORE` (loader.py:57-60) plus the UNIQUE key means two rows
  identical on (opn, doc_sha, field, value_role, condition_verbatim) but
  printed with *different values or on different pages* collide; the second
  insert is counted as a `duplicates` bump (loader.py:113-116) and its value
  is lost with only a counter — no rejections entry names the dropped row.
  Concretely: if the F051 datasheet printed the same `io_sink_current` typ
  row on two pages with different condition suffixes that normalize equal,
  one survives arbitrarily. Bounded fix: on duplicate, emit a rejection
  record (`"duplicate_suppressed"` with the full incoming row) so the
  conflict is visible; keep INSERT OR IGNORE for idempotent replay.

## (4) Variant / package / grade crosscutting

- K6 UFQFPN32 `GPIO=27` vs LQFP32 `GPIO=25`, and K6 `USART=2` vs K4
  `USART=1`: these bind at the family-matrix knife layer —
  family_device_matrix.py:8-13 requires the document itself to bind the
  column to the part (header token / code list / device-summary join); the
  label grammar separates `gpio_count` from `pin_count` (lines 23-27,
  rule at line 103) and `usart_count` from `uart_count`.
- Norm-serve grain (OPN+sha+field+role+condition, schema.py:47-50) preserves
  UFQFPN32-vs-LQFP32 differences **only if** the OPN differs. The STM32F051
  package code is inside the OPN (e.g. `STM32F051K6Ux` vs `STM32F051K6Tx`),
  so per-package counts serve correctly per OPN.
- **Gap (F3): wildcard-OPN truncation risk at adapter.** ST datasheets
  print `STM32F051K6Ux`-style wildcards; if extraction binds a variant-row
  value to the wildcard token rather than a concrete OPN, adapter.py:35
  accepts the wildcard string as-is (`"STM32F051K6UX"`), which then serves
  as if it were a concrete part. Recommendation: reject OPNs containing
  `X` wildcards at the adapter (or mark `opn_is_wildcard: true`) — concrete
  binding is the matrix layer's contract (family_device_matrix.py:17-19).

## (5) Curves: axes/units/test conditions + interpolation-method provenance

- `curve_atoms.py:51-107` (`axis_quantity`): axis label → canonical quantity
  key using the condition_model symbol table, with printed-unit family
  cross-check (lines 60-71); curve_type vocabulary frozen additive-only
  (lines 7-17). Deterministic, no model calls.
- `curve_evidence.py` carries the three laws in code: conditions never
  defaulted — `not_comparable` with reason otherwise (docstring lines 9-11);
  interpolation bounded by plotted support, "no clamping, no extrapolation"
  (lines 856-857, 943); typical-curve status rides every result (lines 14-16).
- **Interpolation provenance is explicit**: every derived value is tagged
  `_DERIVED_METHOD = "linear_interpolation_on_digitized_polyline"`
  (curve_evidence.py:984) and "linear interpolation between digitized
  samples, bounded by [support]" (line 1043); `typical_curves.py:503,589,618`
  marks each point `interpolated: True/False`.
- **Curve→norm-serve boundary gap (F4):** `GET /api/v1/opn/<OPN>` serves
  only norm_claims (serve.py:18-33); curve evidence objects
  (CURVE_EVIDENCE_SCHEMA, curve_evidence.py:37) are a separate store. A
  curve-derived current value cannot masquerade as a datasheet-row claim —
  good — but there is also no served route that joins a datasheet electrical
  row to its applicable curve families (the "applicability" the bead names).
  That join is future work, not a defect.

## (6) ONE regression case design (test design only, not implemented here)

**Case: `test_f051_io_sink_conditions_are_distinct_rows`** (new file
`tests/test_normserve_f051_regression.py`, synthetic fixture rows only).

Fixture: three rows, all `opn=STM32F051K6`, same 64-hex synthetic doc_sha
(sha256 of a temp-file fixture PDF stub registered via the vault test
helper), `field=io_sink_current`:

| # | value | value_role | condition_verbatim | page |
|---|-------|-----------|--------------------|------|
| 1 | 22 | typ | `Output current sunk ... TA = 25 °C (per §6.1.2)` | 50 |
| 2 | 23.8 | max | `TA = +105 °C` | 50 |
| 3 | 20 | max | `TA = +125 °C, T_J ≤ 125 °C, low dissipation` | 50 |

Assertions:
1. All three rows load (loaded=3, rejected=0).
2. `claims_for_opn` returns all three; no two share
   (value_role, condition_verbatim).
3. Replay of the identical input yields loaded=0, duplicates=3, and
   **rejected=0 with three `duplicate_suppressed` records** (requires F2 fix;
   the test pins the desired behavior).
4. A fourth row, same everything but verbatim `25` for a value claiming `26`,
   is rejected by the entailment gate (schema.py:138-161).
5. A row whose OPN is the wildcard `STM32F051K6UX` is rejected at the
   adapter (requires F3 fix; pins desired behavior).

## Bounded affected-record / reprocessing scope

Do NOT assume the corpus is wrong. The only structural defect capable of
data loss is F2 (silent same-key suppression). Scope, in order:

1. Enumerate candidates (read-only, when store access is authorized):
   ```sql
   SELECT opn, doc_sha, field, COUNT(*) c FROM norm_claims
   GROUP BY 1,2,3 HAVING c > 1;
   ```
   plus any lane-source JSONL rows that failed to load — the rejections
   ledger already retains those (loader.py:121-128). No PG access from this
   session: **affected-row count is UNKNOWN** (P5), bounded by the query
   above, not corpus-wide.
2. Reprocessing, if F2 finds real collisions: re-load only the affected
   (opn, doc_sha) JSONL lane files after the loader fix — replay is
   idempotent by design (replay=0-new-rows test, tests/test_normserve.py),
   so this is an append-only repair, never re-extraction.
3. F1/F3/F4 are schema/hardening recommendations with **zero** reprocessing
   scope (no existing rows can violate them today).

## Blockers

1. **Store access:** PG rows on x are not readable from this code lane; the
   F2 candidate count above is UNKNOWN until the factory owner runs the
   query (or a dump is provided read-only).
2. **Artifact absence:** STM32F051 DocID022265 Rev7 (sha 5cfb0e2e…) remains
   unfound (vault/.54/manifests/11.6 GB tar full-scan NEGATIVE); mayor's
   artifact search continues per bead notes. All fixtures above are
   synthetic from NEO-validated values — no copy acquired.
3. **FREEZE holds:** no PG, no batches; one new doc/MPN served with
   provenance remains gated on mayor re-review of PR #66. This audit does
   not unblock loads.
