# DesignWins — Product doctrine: from intent to defensible design wins

**Status:** operator vision for review · **Date:** 2026-10-21 · **Scope:** DesignWins / CategoryRank · **No release or implementation authorization.**

This document binds together the existing [CONTRACTS](CONTRACTS.md) (the `Build`, cohort and D7), [PROVENANCE](PROVENANCE.md) (accountability), [PRD-PHRASE](PRD-PHRASE.md) (phrase ↔ axis registry), and [DECISIONS](DECISIONS.md) (ratified laws). It does **not** silently supersede their existing implementation contracts. Where this document proposes a new domain model or view, it specifies intended behavior rather than claiming it is shipped.

## North star

**DesignWins begins above the orderable part number.** A person expresses an engineering need in ordinary language, images, diagrams, a BOM, or a partially specified circuit. The system interprets that intent into transparent, confirmable requirements; determines the relevant category/family cohort; uses evidence-backed elimination knives to narrow genuine candidate parts; compares survivors **at the engineer's conditions**; and explains why a part passes, fails, nearly fits, or remains unknown. Every meaningful statement opens to its exact source and can be replayed.

The product is not a PDF collection, a parametric-table clone, a one-time LLM recommendation, or a collection of independently maintained category pages. It is a **source-grounded engineering knowledge and decision system**.

## The canonical knowledge object: the part dossier

A part ceases to be merely a catalog row. Its **dossier** is the release-pinned aggregation of what all applicable evidence says about it.

A dossier represents:
- exact manufacturer / OPN / package and explicit family/series applicability; aliases and document stems are not identity proof;
- manufacturer datasheets, reference manuals, errata, PCNs, app notes, reference designs and evidence-bearing distributor observations, with source kind, document revision, content SHA, timestamps and applicability;
- factual claims, conditions, units, min/typ/max and guarantees, verified pin tables, figures/curves, associations and lifecycle context;
- the source SHA, page/region/quote or figure/crop, verification state, rule/method version, release ID and any uncertainty;
- absent, held, stale or contradictory evidence **without hiding it**.

The dossier should be a logical, queryable composition over immutable evidence and approved releases; it need not be one giant physical table or JSON document. **Category, manufacturer, family and aisle screens are aggregations/projections over dossiers, not rival truths maintained separately.** Denominators and identity grain must always be stated (part vs family vs row vs document). A distributor-sourced availability assertion must never masquerade as a manufacturer electrical specification.

## Three evidence states (plus validity)

1. **STATED:** printed explicitly in an applicable source. Preserve the precise text, page, units, conditions, document revision and identity.
2. **DERIVED:** computed or read from evidence (e.g. digitizing a curve at a user operating point). Record the underlying source figure/crop, digitization calibration, interpolation algorithm and version, input conditions, output value and uncertainty, and pinned release. It must be replayable and diffable. A model assertion alone is not a verified derivation.
3. **ABSENT/UNKNOWN:** no sufficient evidence for this condition or applicability, or the source cannot be resolved. The candidate is **held for this knife, not eliminated**.

Additionally, STATED or DERIVED evidence can be **HELD/REJECTED** for failed verification, conflicting identities, wrong conditions, out-of-range derivation, or unsound source joins. An entry's presence is not permission to use it in an elimination verdict.

**No extrapolation dressed as evidence.** A plotted curve can support an interpolated point only inside its verified domain, with compatible fixed conditions and an explicit method/error policy. Outside domain: UNKNOWN. Even inside the plot, unresolved axes, mode, Vin, load, temperature, package or rating semantics prevent a comparable judgment.

## The end-to-end product sequence

`intent (language / image / diagram / BOM)`
→ **semantic interpretation** (declared constraints separate from inferred, confirmable assumptions)
→ **category / aisle / family discovery** (the right cohort of DISTINCT purchasable candidates)
→ **knife binding** (phrases map to typed, evidence-backed axes with measured coverage; unbound phrases are MissingAxis)
→ **conditional elimination** (PASS / NEAR_MISS / FAIL / UNKNOWN with source evidence and reasons)
→ **survivor comparison** (applicable normalized claims and curves evaluated at the engineer's operating point)
→ **dossier drill-down** (all applicable documents, cited claims, pinouts, curves, errata, design associations and uncertainty)
→ **next question** (ranked by how much it would narrow the cohort)
→ **source click / replay** (real manufacturer document, page/figure/crop and derivation).

**The experience should visually expose the knowledge structure.** An engineer should be able to see interpreted needs, why candidates were kept or cut, which axes are measured, which claims are missing, and what deeper dossiers contain. An interactive knowledge graph can be a UI projection of the same typed entities/edges; it is **not** an independently trusted database or a prerequisite for shipping the first journey.

## The elimination knife law

A knife is a typed engineering question with semantics, operating conditions, applicability, evidence coverage and a decision rule. A semantic phrase can invoke a knife **only if** the axis exists and has measured, trusted substrate. A knife without evidence cannot cut. A missing measurement is UNKNOWN, never FAIL.

As a cohort narrows, its useful comparison dimensions may become richer. At broad category level, a few universal filters determine which families are in play; among three genuinely competing OPNs, precise conditions, curves, pinout details, package and BOM applicability decide the design. Do not force a giant same-axis comparison across incompatible device classes.

Examples of conditional knives: MCU sleep current at (temperature, supply, mode); wake time by clock source; regulator efficiency at (Vin, Vout, Iout, switching frequency, BOM); ADC effective resolution by sample rate; MOSFET on-resistance by junction temperature. A single printed typical point is not a substitute for a curve-derived value at the user's point.

## Provenance and replay are release contracts

For each accepted decision, preserve:
- unique exact part/claim identity and applicable family/package coverage;
- source document content SHA, revision, page/quote/region or figure/crop and source-kind;
- measured/stated vs derived distinction;
- unit/field normalization rule, min/typ/max and condition coverage;
- derivation method/version, plotted domain, uncertainty and interpolation evidence if derived;
- release manifest and explicit verdict (including UNKNOWN and blocked reasons).

Every derivative value can be recomputed after a method upgrade, compared to the previous output and admitted only through reviewed truth gates. A resolver must verify the source and permission; no fabricated document URL or stale-release fallback.

## How engineering systems map to the vision

- **PDF factory:** discover and deliver content-addressed evidence; distributor taxonomies are discovery inputs, not the truth model.
- **M5 extraction / Reader-v2:** source-local claims, table/cell/figure evidence, reconciliation and held classes.
- **L1–L5 spine:** verify → normalize → exact identity → condition-aware comparability; no identity guesses, no implicit promotion.
- **cr-core / release + facade / dw-api:** immutable evidence-backed dossiers and typed views over the same release, authenticated source resolution and conditions; demo content may not impersonate a released part.
- **M4 FE:** intent composer, category drill-down, elimination board, dossier viewer, conditional compare, source click, explicit unknowns. Start with an honest private journey, then expand.
- **Seven:** watch all registered producers, releases, interfaces and tests; coordinate ownership and resolve the highest downstream blocker. Monitor real product acceptance, not just rows/compute.
- **Onsite CPU/GPU + solar:** strategic local-first extraction, verification and reasoning economics; cloud is bounded residual/overflow on measured need, not the default.

## Alignment acceptance: one engineer journey, one truth

A tester enters an MCU or POWER design brief. They can inspect interpreted requirements, the chosen cohort and elimination history, select an OPN dossier, compare a verified claim at the requested conditions, see STATED vs DERIVED vs UNKNOWN, and open or explicitly fail to resolve its real source. Every screen and API response identifies the same release. Cache warmth, release swaps and missing evidence must not silently change the truth.

**Measure**: source-resolvable claim coverage (by exact denominator), conditional answerability, correct eliminations/held unknowns, fraction of user journeys completed, and time from new verified manufacturer evidence to approved usable claim. Raw PDF, extracted-row and candidate-pair counts are supporting operational metrics, **not product success**.

## Instructions to every agent / new PRD

Before building, state (1) which dossier/evidence entities this changes, (2) which semantic intent/knife/decision or UI behavior it enables, (3) exact source+release proof, (4) handling for held/absent/derived, (5) the customer journey it improves, (6) the real owner and test gate. Do not create a parallel catalogue, invent ungrounded evidence, override release truth, or mark unknown as eliminated.

This is the **shared product direction**. Existing ratified contracts stay authoritative for implementation mechanics until separately reviewed amendments align them with this doctrine.
