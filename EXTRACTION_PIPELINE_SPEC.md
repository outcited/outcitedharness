# DesignWins Extraction Pipeline Specification v1.0

**Authority:** Sam Kim, 2026-10-22. This document is binding for ALL agents.
**Supersedes:** All prior extraction architecture decisions, including Reader-v3, cloud_extract, and any pipeline that puts LLM extraction before local extraction.

---

## 1. The Three-Stage Pipeline

```
┌─────────────────────────────────────────────────────────────────────┐
│ STAGE 1: LOCAL EXTRACTION (FREE — ALWAYS FIRST, ALWAYS FULL DOC)   │
│                                                                     │
│ Extract every page of text with pymupdf. Run every deterministic   │
│ reader (pillars, thermal, mode, aec, errata, pin tables). Index    │
│ every page. Produce claims with EXACT verbatims copied             │
│ character-for-character from the PDF.                              │
│                                                                     │
│ Input:  PDF file                                                    │
│ Output: Page-level text index + verified claims with exact          │
│         verbatims + page attributions + document SHA               │
│ Cost:   FREE (local CPU)                                            │
│ Speed:  46,000 docs/hour (measured)                                │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│ STAGE 2: LLM ENRICHMENT (CLOUD/BATCH — SUPPLEMENT, NOT REPLACE)    │
│                                                                     │
│ Send page text + local extraction results to DeepSeek V4.1 Flash   │
│ via OpenRouter batch. Ask TWO questions:                           │
│                                                                     │
│   a) "What specs exist here that local extraction MISSED?"         │
│   b) "What is on pages local extraction couldn't parse?"           │
│                                                                     │
│ The LLM NEVER re-extracts what local extraction already found.     │
│ It fills gaps. Its output is CANDIDATE claims (not verified).      │
│                                                                     │
│ Input:  Page text (from Stage 1) + local extraction results        │
│ Output: Candidate claims with field names, values, page numbers    │
│ Cost:   $0.0026/doc (batch) or $0.004/doc (realtime)               │
│ Speed:  533 docs/hour (batch, async)                               │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│ STAGE 3: DETERMINISTIC VERIFICATION (LOCAL CPU — THE GATE)         │
│                                                                     │
│ For every LLM candidate claim:                                     │
│   1. Normalize the field name to canonical spine fields            │
│   2. Parse the value to numeric                                    │
│   3. Go to the cited page in the PDF                               │
│   4. Find the exact text containing the value                      │
│   5. Replace the LLM's paraphrase with the exact quote             │
│   6. Run hardened L2 verification (sha + page + verbatim + value)  │
│                                                                     │
│ Claims that pass L2 are VERIFIED. Claims that fail are HELD.       │
│ No claim enters the spine without passing this gate.               │
│                                                                     │
│ Input:  LLM candidate claims + source PDFs                         │
│ Output: Verified claims with exact provenance                      │
│ Cost:   FREE (local CPU)                                            │
│ Speed:  >10,000 claims/minute                                      │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│ STAGE 4: VISION ESCALATION (MiMo — ONLY FOR IMAGE-ONLY CONTENT)    │
│                                                                     │
│ For pages where text extraction produced nothing or almost         │
│ nothing (scanned, raster, diagrams, curves):                       │
│                                                                     │
│   Send to MiMo: the page IMAGE + ALL accumulated text context      │
│   from Stages 1-3. The context tells the vision model WHAT the     │
│   image contains: "page 56 says 'Figure 12: Pinout Diagram'        │
│   and mentions PA0-PA31. Here's the image we can't read."          │
│                                                                     │
│ NEVER send a bare image without context. NEVER use vision for      │
│ pages with readable text layers.                                   │
│                                                                     │
│ Input:  Page images + text context from Stages 1-3                 │
│ Output: Extracted data from images (pinouts, curves, dimensions)   │
│ Cost:   FREE (MiMo subscription)                                   │
│ Speed:  ~80s/image                                                 │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 2. What Each Stage Receives and Produces

### Stage 1: Local Extraction

**Receives:**
- PDF file path (from corpus, intake, or delivery)

**Produces:**
- Page-level text index (pymupdf get_text per page)
- Deterministic claims (pillars, thermal, mode, aec, errata readers)
- Each claim has: field, value (numeric), unit, page_1based, verbatim (exact copy from page), pdf_sha
- Document classification (L0: datasheet, app_note, BOM, errata, etc.)
- Page scoring (which pages have spec tables, which are TOC, which are images)

**Rules:**
- Read the ENTIRE document, not first N pages, not top-N scoring pages
- Copy verbatims EXACTLY — no normalization, no reconstruction, no joining
- Track page attribution per claim (not per document)
- Preserve original unicode (µ ≠ μ, θ ≠ Θ)
- Keep original printed values alongside normalized ones
- If a reader can't extract from a page, record WHY (missing text, complex table, image-only)

**The page-level output from Stage 1 is the CONTEXT for all subsequent stages.**

### Stage 2: LLM Enrichment

**Receives:**
- Page text from Stage 1 (the actual text extracted from each page)
- Local extraction results: "we found flash_kb=256 on page 5, sram_kb=64 on page 5"
- List of pages where local extraction failed or found nothing
- Document classification

**Prompt structure (the exact template):**
```
CONTEXT FROM LOCAL EXTRACTION:
- Page 5: Found flash_kb=256, sram_kb=64, clk_max_mhz=168, n_gpio=112
- Page 23: Complex table, local extraction incomplete
- Page 57: No text layer (image-only page)
- Document type: datasheet (MCU)

TASK: Given this page text and what we already found, extract ADDITIONAL
engineering specifications that local extraction MISSED.

Return JSON array; each element:
{"field": "...", "value": ..., "unit": "...", "page_1based": N,
 "verbatim": "exact text from the page", "value_role": "typ|min|max|null"}

Focus on specs we DON'T already have. Don't repeat what's in the context.
```

**Engine selection (in priority order):**
1. **TP4** (local, free, thinking:false) — when queue < 4 hours
2. **OpenRouter batch DSV1** ($0.0026/doc, chunks of 400) — when TP4 would take > 4 hours
3. **QwenCloud DSV1** (subscription, concurrency=1) — ONLY when 1 and 2 are both saturated
4. **NEVER use qwen3.8-flash** for extraction (not the bar, proven inferior to DSV1)

**DSV1 settings (measured, do not change):**
- `model`: deepseek-v4.1-flash (or deepseek/deepseek-v4.1-flash:batch)
- `temperature`: 0
- `max_tokens`: 32000 (NOT 16000 — reasoning eats ~10K, response needs ~20K)
- `reasoning`: {"effort": "low"}
- For TP4 only: `chat_template_kwargs`: {"thinking": false}, `max_tokens`: 6000

**Produces:**
- Candidate claims (NOT verified — the LLM paraphrases verbatims)
- Discovery data: "page 56 has a pinout", "page 23 has absolute maximum ratings"
- Awareness of what local extraction missed

### Stage 3: Deterministic Verification (The Repair Gate)

**Receives:**
- LLM candidate claims from Stage 2
- Source PDFs (to verify against)

**Process (for each candidate claim):**
1. **Field normalization**: Map LLM field name to canonical spine field
   - "VDS Drain to Source Voltage" → max_vds_v
   - "Gate threshold voltage" → vgs_th_v
   - "Flash memory" → flash_kb
   - Unmappable fields are dropped (not guessed)

2. **Value parsing**: Convert LLM string values to numeric
   - "40V" → 40.0
   - "±20" → 20.0
   - "256 kB" → 256.0
   - "1.375 MB" → 1408.0 (binary conversion for memory fields)

3. **Verbatim repair**: Go to the cited page in the PDF, find the exact text
   fragment containing the value, replace the LLM's paraphrase with the
   exact substring from the page. This is deterministic text matching.

4. **L2 verification**: Run the hardened verifier:
   - SHA of PDF matches the claim's pdf_sha
   - Verbatim appears on the cited page (exact substring match)
   - Value appears on the cited page (in the claim's unit family)
   - Field-specific range checks pass
   - Min/typ/max semantics preserved

**Produces:**
- VERIFIED claims (passed all gates — these enter the spine)
- HELD claims (failed a gate — recorded with reason, never silently accepted)

**Yield expectation:** 15-30% of LLM candidates pass verification (measured: 4,621 of 15,371 = 30%)

### Stage 4: Vision Escalation

**Receives:**
- Page images (rendered from PDF at 200 DPI)
- ALL accumulated text context from Stages 1-3

**What triggers vision:**
- Page has < 80 characters of text (scanned/raster)
- Page contains image-only content (pinout diagrams, package drawings, curves)
- Stage 2 LLM identified content it couldn't read from text alone

**Prompt structure (with context):**
```
This is a page from an electronics document.

CONTEXT (text layer from surrounding pages):
- Page 55: "Table 4: Pin Descriptions for STM32F103"
- Page 56: "Figure 12: Pinout Diagram — all 48 pins"
- Page 57: "Electrical Characteristics — VDD 2.0V to 3.6V"

The image I'm sending is from page 56. Given this context, extract the
pinout information from the image.

Return JSON array; each element:
{"pin_number": N, "pin_name": "...", "type": "...", "alternate_function": "..."}
```

**Engine:** MiMo v2.6-flash (subscription, free)
- `max_tokens`: >= 16000
- `reasoning_effort`: "low"
- `temperature`: 0
- ALWAYS include text context alongside the image
- NEVER send a bare image without context

**Produces:**
- Extracted data from images (pinouts, dimensions, curves)
- These claims still need Stage 3 verification (against the source PDF)

---

## 3. What NOT To Do (The Mistakes)

| mistake | why it's wrong | what to do instead |
|---------|---------------|-------------------|
| Send page text to LLM without local extraction context | LLM re-extracts everything including what we already have; wastes tokens and produces paraphrased verbatims | Send local extraction results + page text; LLM fills gaps only |
| Use LLM as primary extraction engine | LLM paraphrases verbatims → fails verification; 0.1% pass rate without repair | Use deterministic readers first; LLM is enrichment only |
| Default to TP4 for volume extraction | TP4 has 8-request ceiling; starves all other lanes | TP4 for adjudication; batch for volume |
| Send bare images to vision without context | Vision model doesn't know what it's looking at; poor accuracy | Always send page text context alongside the image |
| Use qwen3.8-flash for extraction | Not the bar; DSV1 with context produces better results | DSV1 is THE model; qwen3.8 only if proven equivalent |
| Submit one giant batch to OpenRouter | Batches fail above ~400 requests | Chunks of 400 (proven: 23,318 docs as 58 chunks) |
| Use 2+ concurrent QwenCloud processes | Concurrency=1; 2+ = all fail with 5-min cooldown | 1 QwenCloud process max |
| Skip Stage 3 verification | Unverified claims have no provenance; can't trust source-click | Every claim passes L2 or is HELD |
| Extract only top-N pages instead of full document | Misses specs on later pages; the 40K-char limit was wrong | Read the ENTIRE document in Stage 1 |

---

## 4. Engine Selection Matrix

| stage | first choice | overflow | never |
|-------|-------------|----------|-------|
| Stage 1 (local) | pymupdf + deterministic readers | N/A (always available) | LLM |
| Stage 2 (LLM) | TP4 (free, <4h queue) | OpenRouter batch ($0.0026/doc) | qwen3.8-flash |
| Stage 3 (verify) | Local CPU (deterministic) | N/A | LLM |
| Stage 4 (vision) | MiMo (free subscription) | N/A | Bare images without context |

**Concurrency limits (measured 2026-10-21, do not re-litigate):**
- QwenCloud: 1 process ONLY
- TP4: 8 connections
- OpenRouter batch: unlimited (async, chunks of 400)
- MiMo: 4-8 workers

---

## 5. Data Flow Between Stages

```
PDF
 └→ Stage 1: page text index + deterministic claims (exact verbatims)
      └→ Stage 2 input: page text + "we already found X, Y, Z"
           └→ Stage 2 output: candidate claims (paraphrased verbatims)
                └→ Stage 3 input: candidates + source PDF
                     └→ Stage 3 output: verified claims (exact verbatims)
                          └→ SPINE: normalize → identity → compare → SERVE
      └→ Stage 4 input: images + text context from Stages 1-3
           └→ Stage 4 output: image-extracted data
                └→ Stage 3 input: image claims + source PDF
                     └→ Stage 3 output: verified image claims
                          └→ SPINE
```

---

## 6. New Document Processing (the ongoing pipeline)

When a new PDF enters the system (factory delivery, intake, manual upload):

1. **Immediately**: Stage 1 (local extraction — seconds per doc)
2. **In batch**: Stage 2 (LLM enrichment — grouped into chunks of 400 for OpenRouter)
3. **Immediately after Stage 2**: Stage 3 (verification — seconds per claim)
4. **Only for image pages**: Stage 4 (vision — 80s per image)

The total pipeline latency per document:
- Stage 1: < 1 second
- Stage 2: minutes (batch, async)
- Stage 3: < 1 second
- Stage 4: ~80 seconds per image page (only if needed)

---

## 7. Metrics and Telemetry

Each stage publishes to `extraction-telemetry/spine.json`:

| stage | metric |
|-------|--------|
| Stage 1 | docs processed, claims extracted, pages indexed |
| Stage 2 | candidates produced, fields covered, cost per batch |
| Stage 3 | verified count, held count, repair rate, pass rate |
| Stage 4 | images processed, claims from vision |

Seven consumes this telemetry and flags:
- Any box < 50% utilization with queued work
- Any stage using the wrong engine (TP4 for volume, bare images to vision)
- Pipeline stalls (Stage 1 producing but Stage 2 not consuming)

---

## 8. File Locations

| artifact | path |
|----------|------|
| This spec | ~/Harnessv1/EXTRACTION_PIPELINE_SPEC.md |
| Agent instructions | ~/.claude/CLAUDE.md |
| Engine routing law | ~/Harnessv1/AGENT_ENGINE_LAW.md |
| Model settings | ~/Harnessv1/MODEL_SETTINGS.md |
| Deterministic readers | ~/Harnessv1/scripts/burn_pass.py |
| LLM enrichment | ~/Harnessv1/scripts/batch_extract.py |
| Verification/repair | ~/Harnessv1/scripts/verbatim_repair.py |
| Vision runner | ~/Harnessv1/scripts/burn_vision.py |
| Spine (L1-L5) | ~/Harnessv1/scripts/spine.py |
| Telemetry | ~/extraction-telemetry/spine.json |

---

## 9. Version History

| version | date | change |
|---------|------|--------|
| 1.0 | 2026-10-22 | Initial spec. Codifies the architecture Sam designed: local extraction first (full document, free), LLM enrichment second (with context), deterministic verification third, vision last (with context). Supersedes all Reader-v3/cloud_extract approaches that put LLM before local extraction. |
