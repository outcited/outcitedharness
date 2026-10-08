# LoRA v1 — power/parametric table extraction (queued 2026-10-02)

## Data (built, verified)

- `results/lora-v1-pairs/power-tables-v1.jsonl` — 2,863 pairs
- Source: power-grids-20260909* (8 datasets) + key-features-grid-20260908,
  deduped by `document_sha256` (re-runs collapse), joined to source PDFs under
  `/Volumes/M5_4TB/exports/power-datasheet-pairs/*/pdf`, sha256-verified,
  text via pymupdf (≤12 pages, ≤40K chars/pair).
- Format: llama-factory `messages`. Target = compact rows JSON
  (symbol/value/unit/qualifier/condition).
- Builder: `scripts/build_lora_pairs.py` (rerun to regenerate; deterministic).
- Not included (v2 candidates): vision pairs (pin/ball — none labeled yet),
  MCU manual body-reads, prose descriptions.

## Run

- Node: asus2 (free; GPU released 2026-10-02 when the TP2 impostor was torn down).
- Base model: Qwen3-8B-Instruct (or Qwen2.5-7B-Instruct) — plain BF16 weights
  for training; the FP8 serving quant is applied after, never trained through.
- Framework: llama-factory (image `harness/llamafactory-qwen3-next:20260901`
  exists on dgx2 — copy or re-pull on asus2).
- Sketch: LoRA r=16 alpha=32, lr 1e-4, 2-3 epochs, ctx 16K (pairs are long),
  batch via packing off (rows are position-sensitive). Hold out 150 pairs.

## Promotion gate (must pass all)

1. On the 150 held-out pairs: field-level exact match ≥ the current
   deterministic extractor baseline on the same docs.
2. On frozen gold rows (adjudication-reviewed): ≥ 99% value+unit agreement
   with labels, conditions included, no invented symbols (hallucination rate
   on symbols < 0.5%).
3. vs V4.1 (teacher): ≥ 95% row agreement on a 100-doc sample — the student
   may only win or tie, never silently diverge.

## After promotion

- Serve FP8 on asus2/asus4 workers (3-4 concurrent per box), route routine
  table extraction to it; V4.1 keeps hard/whole-doc/vision.
- Every V4.1 + hosted-adjudicator pass adds fresh validated pairs —
  regenerate the dataset, retrain on schedule (the flywheel).
