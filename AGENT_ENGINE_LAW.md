# THE EXTRACTION ARCHITECTURE — ABSOLUTE LAW (Sam, 2026-10-21)

## ORDER OF OPERATIONS (NEVER VIOLATE)

1. LOCAL TEXT EXTRACTION (free, full document) — ALWAYS FIRST
   - Full document, not samples. Use the page-index we already built.
   - The better this gets, the less we need anything else.

2. SECOND PASS for failures: TP4 + context (free, local)
   - Only when queue < 4 hours
   - Overflow: OpenRouter batch DSV1 ($0.0026/doc, chunks of 400)

3. VISION (MiMo) — only after text exhausted, with page text context

## MODEL LAW

- DeepSeek V4.1 Flash is THE model. Period. With context.
- MiMo/QwenCloud ONLY if PROVEN equivalent with evidence.
- Track model provenance per claim.
- Never substitute without proof.

## CONSISTENCY (the problem this law solves)

Agents were defaulting to different engines (cloud first, TP4 first, Qwen first).
This stops now. Every agent follows the same escalation path.
