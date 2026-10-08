# DATASHEET EXTRACTION SERVICE — INTERFACE SPEC v1

Status: SPEC (not implemented). Provenance: vision-compare-multibrand-v2-20260913
(323 pages, 12 vendors, 646 calls) and vision-compare-v1-20260913 pilots.
Implement against this document; do not improvise the contract.

## 1. Purpose

A single fleet capability for extracting structured tables (pin/pinout, electrical
characteristics, register maps) from electronics datasheet PDFs, available to all
harness agents. Pipeline: words-first readiness, vision escalation only on failure,
per-row provenance, machine-checkable QA. Philosophy: NEVER send a page to vision
that the text layer already answers; never return an extraction without an
expected-vs-found QA record.

## 2. Surfaces

| Surface | Contract |
|---|---|
| Python API | `harness.electronics.extraction_service.extract(request: dict) -> dict` |
| CLI | `scripts/run_extraction_service.py --request <json>` (same schema) |
| Gateway (phase 2) | POST `/v1/extract/datasheet` via harness gateway after Python API is GA |

## 3. Request schema (`harness.electronics-extraction-request.v1`)

```json
{
  "document": {"source_path": "...", "document_sha256": "..."},   // one required
  "pages": "auto" | [10, 11],          // default auto = keyword/type detection
  "table_types": ["pin", "ec", "register"],  // or ["auto"]
  "options": {
    "render_dpi": 220,                  // 72..300
    "max_pages": 40,                    // cost guard
    "lane": "auto" | "text_only" | "vision_required",
    "budget_usd": 1.0                   // hard stop; service refuses beyond
  },
  "caller": {"agent": "qwen38_foreman", "work_id": "..."}   // accounting
}
```

Validation: source exists or sha matches registry; budget > 0; types valid.
Unknown fields rejected (strict).

## 4. Pipeline stages

1. **Page map (words-first, free).** pymupdf words per page; type detection by
   keyword scoring (reuse sampler logic from vision-compare-multibrand-v2).
   Output per page: `page_type`, `keyword_score`, `n_words`, word set.
2. **Text-lane extraction.** For pin: word-column alignment (existing
   `table_extractors.pin_identity_rows` path). For EC/register: word-layer row
   segmentation. Produces candidate rows WITHOUT any model call.
3. **Readiness gate (the Sam metric).** Per page: expected count from words
   (printed package pin count, header row count) vs text-lane found count.
   - found == expected AND grounding >= 0.9 -> emit, vision never called.
   - found != expected or words absent/scanned -> vision lane, reason recorded.
   - reason is one of: `count_mismatch`, `no_text_layer`, `low_grounding`,
     `layout_complex`, `caller_requested`.
4. **Vision lane (escalation ladder).** ds_v4_vision (effort low, 32k) first;
   escalate to glm5v_turbo (16k) when: `finish_reason == length`, parse failure,
   or grounding < 0.4. One retry max per model. Register-type pages: prefer
   glm5vt directly until register quality is fixed (both models ~0.45 grounding).
5. **QA & emit.** Cross-check vision rows against word layer; merge text+vision
   results; ground every row; never drop a discrepancy silently.

## 5. Response schema (`harness.electronics-datasheet-extraction.v1`)

```json
{
  "schema": "harness.electronics-datasheet-extraction.v1",
  "document_sha256": "...",
  "status": "complete" | "partial" | "refused",
  "pages": [{
    "page_1based": 10,
    "page_type": "pin",
    "lane": "text" | "vision_ds" | "vision_glm",
    "tables": [{
      "table_type": "pin",
      "rows": [{"pin_number": 1, "pin_name": "PA0", "type_or_function": "I/O",
                "provenance": {"lane": "vision_ds", "grounded": true}}],
      "row_count": {"found": 64, "expected": 64, "match": true,
                    "reason": null}
    }],
    "qa": {"grounding": 0.94, "discrepancies": []}
  }],
  "usage": {"pages_text_lane": 12, "pages_vision": 3, "model_calls": 5,
            "cost_usd": 0.012, "wall_seconds": 41.2},
  "refusal": null | {"reason": "budget_exhausted", "spent_usd": 1.0}
}
```

Row schemas per type (strict, versioned):
- pin: `{pin_number: int|null, pin_name: str, type_or_function: str}`
- ec: `{parameter: str, condition: str|null, min, typ, max, unit: str|null}`
- register (BETA): `{register_name: str, address: str|null, description: str}`
  — flagged beta in response until quality gate passes (see §7).

## 6. Receipts

One JSONL receipt per MODEL CALL, append-only, sha-pinned dir per work_id:
`harness.electronics-extraction-receipt.v1`:
`{work_id, caller_agent, case_id, model, finish_reason, reasoning_tokens,
parse_ok, n_rows, grounding, latency_s, cost_usd, image_sha256, ts}`
Enables per-agent accounting and reproducibility. Never log prompt bodies.

## 7. Gates & SLA

| Gate | Threshold | Status |
|---|---|---|
| Coverage (escalation rescues all DS failures) | 100% | proven (55/55, run2) |
| Parse reliability GLM lane | 323/323 | proven |
| Grounding (comparative) | >= 0.6 avg | proven (0.605/0.626) |
| **Correctness vs ground truth** | **precision/recall >= 0.9 on pin+EC** | **NOT MEASURED — GA blocker** |
| Register quality | >= 0.6 grounding | NOT MET (0.45) — register stays beta |
| Cost ceiling | <= $0.015/page blended | proven ($0.0085) |

GA for external agents requires the correctness pass: >= 50 docs scored against
ground_truth.py pinouts + CR-drop parametric records. Until then the capability
ships `status: "beta"` in every response and callers must not treat rows as
authoritative (they may use them for triage/queuing only).

## 8. Model lanes (already wired in config/models.yaml)

| Lane | Model | Config | Role |
|---|---|---|---|
| ds_v4_vision | deepseek/deepseek-v4-flash-vision-exp | effort low, 32k | bulk vision |
| glm5v_turbo | z.ai direct (OpenRouter fallback) | thinking off, 16k | overflow rescue + register |

Both stay `enabled: false` (dispatch-disabled); the service selects them
explicitly by key. Enable for dispatch only after GA.

## 9. Implementation notes

- Reuse: `page_evidence.py`, `regions.py`, `table_extractors.py`,
  `local_verification.py` (grounding thresholds), `ground_truth.py` (correctness
  pass). Word-column alignment code in `word_columns.py` is the vision-lane
  agent's in-flight file — coordinate before importing.
- The 323-page frozen set (`results/vision-compare-multibrand-v2-20260913/`) is
  the regression corpus: any prompt/model/pipeline change re-runs it and diffs
  grounding + row counts before merge.
- All vision payloads: temperature 0, data-URI PNG, 220dpi renders.
- No fine-tuning, no caching of model outputs across callers beyond receipts.

## 10. Non-goals

- Full-document understanding (scope = table extraction per page set).
- Register-map GA (beta until §7 gate passes).
- OCR of scanned sheets beyond what vision lanes provide incidentally.
- Multi-PDF batch semantics (callers iterate; service stays page-scoped).

## 11. Ownership (decided 2026-09-14, at Sam's direction)

ONE owner of the vision layer: **the vision-lane agent** (Sam's other tab,
m5-cursor session) — it already owns the text-lane modules this service wraps
(`word_columns.py`, `power_datasheet.py`, `table_extractors.py` lineage) and
the active extraction pipeline. It implements `extraction_service` against
this spec.

This session (opencode/GLM) is **spec author and gate-keeper**, not implementer:
owns this document (contract changes require its review), the frozen regression
corpus (`results/vision-compare-multibrand-v2-20260913/`, 323 pages), the
correctness-gate scorer (`results/extraction-correctness-gate-20260914/`), and
the model-lane config in `models.yaml`/`pricing.yaml` until GA.

Merge rule: any change to the service or model lanes re-runs the frozen corpus
and the correctness gate; numbers go in the PR/receipt. Disagreements on
approach escalate to Sam with both agents' evidence, not dueling commits.
