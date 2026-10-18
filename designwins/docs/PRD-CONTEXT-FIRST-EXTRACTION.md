# PRD — CONTEXT-FIRST EXTRACTION (the Unfair Advantage Doctrine)

**Status:** PROPOSED SPEC · **Date:** 2026-10-18 · **Origin:** Sam (direct),
power-data-01 drafting · **Scope:** all extraction lanes (text, vision,
frontier). Supersedes nothing; composes with the L0-first law, the routing
law, and the "the model never grades it" law.

## 0. The doctrine in one sentence

**Now that extraction is free, our job is to extract so much context that
every model call — vision especially — walks in with an unfair advantage:
the page plus everything our deterministic stack already knows about it.**
The model's job shrinks from "find and understand" to "confirm and read
what the map points at."

## 1. Why (measured, not philosophy)

| evidence | number |
|---|---|
| vision WITH context vs text blind (pin/name/type F1) | **0.82 / 0.77 / 0.43 vs 0.70 / 0.59 / 0.43** |
| blind-prompt claim-return flips on identical docs (cliff replays) | **1/5 → 5/5** per invocation |
| blind bulk lane claim-doc rate vs structured lanes | 2.35–21% vs context-pipeline grade |
| cost of a deterministic extraction (mupdf text, word-columns, regions, vector curves) | **$0, local, instant** |
| cost of a wasted paid call with no envelope | full input tokens, noise output |

The free layer is cheap and accurate. The paid layer is expensive and
noisy **when it has to search**. Every context field we hand the model
removes a search problem and converts it into a reading problem.

## 2. The Context Envelope (free, precomputed, mandatory)

Produced BEFORE any model call, by the deterministic stack only
(mupdf / word-columns / regions / L0 / registries). A model call may only
be issued with an envelope attached:

1. **WHERE** — the map: word-column geometry, structural regions
   (parametric / pin / text), **bboxes of the exact target**, page index.
   ("That crap map that provides the vision for approximation of where
   things are" — quoted doctrine; it is `page_evidence` +
   `focused_page_context` in code.)
2. **WHAT** — capability scope + response schema
   (`pin_or_ball` / `parametrics` / `opn_decoder` / `series_summary` /
   `pin_semantics`), never "extract whatever you see."
3. **WHICH** — identity hints: stem/OPN, family, package, pin count,
   ball-out, `work_id`, part markings — the model is told which device it
   is reading about.
4. **BINDING** — table structure: row headers, column headers
   (Min/Typ/Max), symbol vocabulary, axis registry keys. The answer must
   land in a cell we already named.
5. **TRUTH** — quote constraints: every value must carry a verbatim span
   from the substrate text; the deterministic gate (find_quote /
   value_in_quote / column_position_binds) will P0 anything that cannot.
   The model is told this gate exists.
6. **PROVENANCE** — the substrate chain (text layer, page spans, doc
   sha) is attached; the model never supplies provenance, it supplies
   answers that inherit ours.

## 3. The law (enforceable)

1. **No vision without the substrate and the map.** A vision/LLM call
   issued without (a) the first-pass text extraction and (b) the
   region/bbox map is a violation. The vision lane consumes the map; it
   never replaces it. (`L0 → text → map → vision → frontier` is the
   ladder and stays the ladder.)
2. **No blind prompts outside the triage lane.** The bulk burn prompt
   ("dump 28k chars") is a documented, lower-grade exception: source-gated
   triage only, tier-marked (`local-v41`/`cloud-v41`), never the source of
   customer-facing claims. Its outputs may seed the context pipeline;
   they may not graduate directly.
3. **Every model call records envelope completeness.** The call record
   carries which envelope fields were attached (WHERE/WHAT/WHICH/BINDING/
   TRUTH/PROVENANCE). A missing field is a named gap, not a silent loss.
4. **Prefer the free layer always.** If a deterministic extractor can
   answer (vector curves, word-column pin tables, registry axes), no
   model call is issued. Model calls are for what the free layer cannot
   decide — and even then, the free layer's output goes in the envelope.
5. **Reruns must enrich, never repeat.** A doc that returns empty at the
   cliff is NOT re-asked the same blind question; it is routed to the
   envelope path (context pipeline / region vision with the map).
   Paying twice for the same noise is prohibited.

## 4. The grading ladder (context richness = evidence grade)

| grade | what the model saw | where it lands |
|---|---|---|
| G0 | nothing (deterministic extractors only) | authoritative where it works |
| G1 | blind text (triage lane only) | source-gated claims, `tier`-marked, seeding only |
| G2 | envelope + full-page text | structured claims, page-linked |
| G3 | envelope + region render (vision at bbox) | pin/ball/name reads, curve digitization |
| G4 | frontier/approved cloud vision, envelope mandatory | held for adjudication |

Customer-facing data requires G2+ and the existing human adjudication
gates. Gold promotion unchanged.

## 5. Acceptance / enforcement (buildable from existing stack)

1. `local_prompt` / `focused_page_context` already implement WHO/WHERE/
   WHAT — extend the call record to persist envelope-completeness flags
   (small, additive).
2. A lint/gate on the call sites: no model invocation without a capability
   + evidence blob (the seams already centralize calls — verify_provider /
   shim / structural worker).
3. Metric per batch: `envelope_completeness` alongside quote/page/sha
   percentages; blind-call count must be non-increasing.
4. Negative controls: a vision call without map fields must be refused by
   the gate test; a blind prompt outside the triage lane must trip the
   same.
5. Reuse only: L0, regions, word_columns, page_evidence, local_prompt,
   registry axes. **No new extraction architecture.**

## 6. Relationship to existing laws

- **L0-first** — kept, strictly enforced (this spec's substrate law).
- **Routing law (text local-only)** — kept; the free layer is local by
  definition.
- **Model never grades it** — kept; the envelope improves proposals, the
  deterministic gates + humans dispose.
- **Provenance on everything** — kept; the envelope carries ours.
- **FAIL CLOSED / Unknown never eliminates** — kept.

## 7. One-line summary for the wall

> **Extract context for free until vision is just reading.**
