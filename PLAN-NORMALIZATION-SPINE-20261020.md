# ROADMAP — The Normalization Spine (2026-10-20)

> The extraction era is won: 59K-doc corpus dispositioned, ~200K provenance-chained
> claims, every box burning, intake daemon closing the loop. The next mountain is
> **comparability**: turning claims into a number — `comparable_accepted` — that
> the front end can sell. This is the L1–L4 spine, and it is unwired (m5.json
> downstream fields: all null since 2026-10-18).

## The vision (one sentence)

Any two parts, one API call: `/api/compare/v1/{OPN-A}/{OPN-B}` returns the axes
they share, values with provenance, and where they genuinely differ — powered by
fleet-normalized, identity-bound, verification-gated claims.

## Why now

- The fleet is about to idle (winter chain ends, kloey free after intake, happy
  finishing power_v2). Normalization is CPU-deterministic work — exactly what
  the three Ryzen boxes eat.
- Every new facet (AMR, curves, OPN grammar) is worth 3× once the spine exists.
- Nobody else has this: extraction is table stakes; **comparable at scale with
  per-claim provenance is the moat.**

---

## PHASE 0 — Wire the spine (TODAY, machines pumping tonight)

The spine, one stage per service, all deterministic unless marked:

```
L1 raw_rows_unnormalized        every lane-out row, censused
        │
L2 verified_claims              deterministic gates, then adjudicator residue
        │                        (unit parses · value ranges · verbatim↔page recheck)
L3 normalized_claims            canonical units (µ/μ, mA/µA, °C/W) · field synonyms
        │                        (mode Run/RUN/EM1) · typ/min/max roles · sig-figs
L4 identity_bound               slug→OPN (proven join) + bucket-6a catalog intake
        │
comparable_accepted             parts-pairs sharing ≥1 normalized facet axis
        │
SERVED                          /api/compare/v1 + board % + cube cells
```

### Work items (owner · box · effort)

| id | item | owner | box | effort |
|---|---|---|---|---|
| S0 | L1 census: count raw rows across all lane-outs → telemetry | m5 | m5 | 1h |
| S1 | `verify_claims.py` L2 gates: unit parse, range check (sanity tables per field), verbatim-recheck (quote appears on claimed page — pymupdf re-open) | m5 builds | **winter + happy + kloey burn it** | 4h build, then fleet CPU for days |
| S2 | Adjudicator residue: claims failing deterministic gates → canon Qwen (asus4:8900, verdict-deterministic) batch verdicts; REJECT authority stays | m5 | asus4 (exists) | 2h |
| S3 | `normalize_claims.py` L3: unit map, mode-name canon map, sig-fig policy — pure functions, gold-gated | m5 | m5 + fleet | 3h |
| S4 | L4 identity: extend the proven identity-join (16 exact + 363 canonical-alias) to all verified parts; bucket-6a intake (11,195 OPNs) converts slug→gold | m5 | **kloey** (I/O heavy) | 4h |
| S5 | `comparable_accepted` scorer: pair-count per shared axis, per category; board + cube + dw-serve surface | m5 | m5 | 3h |
| S6 | dw-bridge wiring: spine tables → pipeline-live.db downstream fields (kill the nulls in m5.json) | m5 | m5 | 2h |

**Acceptance (Phase 0 done when):** m5.json downstream fields non-null and
growing; board shows comparable_accepted %; one demo pair (e.g. two STM32)
returns a real comparison from served data.

---

## PHASE 1 — Feed the spine (this week, highest comparability-per-CPU)

| id | facet | type | box | why |
|---|---|---|---|---|
| F1 | **Absolute Maximum Ratings lane** (new burn reader) | deterministic | winter+kloey | elimination-grade; every datasheet has one |
| F2 | **Ordering-code grammar** (OPN → temp grade/package/speed/MSL) | deterministic+TP4 assist | kloey | attributes nobody else has; feeds L4 directly |
| F3 | **Canonical maps applied** (modes, units) | deterministic | m5 | L3 quality multiplier |
| F4 | **EC-table full capture** (min/typ/max + conditions as columns) | deterministic | happy | densifies every axis |

## PHASE 2 — Curves through the vision runner (next week)

- V1 vector-curve expansion beyond TI/Vishay (deterministic first, vision on raster)
- V2 **ZthJA transient** (9 rows today → thousands; derating plots via vision)
- V3 SOA + efficiency + dropout-vs-load (greenfield facets)
- V4 block-diagram topology classification (vision; brand-new axis)
- Engine: burn_vision (live) + MiMo volume + TP4 escalation for hard crops

## PHASE 3 — Time dimension (following week)

- R1 revision graphs: same-stem rev A/B/C → spec-drift claims ("VDD tightened")
- R2 errata deltas applied: binding (running now) → overrides in compare results
- R3 PCN event claims → lifecycle flags in compare (discontinuation = negative)

## PHASE 4 — Frontier

- Register maps (frontier lane, TP4; 2,085 refmans)
- Family-matrix borderless decode (TP4; Renesas/ST gaps)
- BOM cross-provenance (refdes→part vouching; refdesign burns are its input — done today)

---

## Machine map — TODAY

| box | assignment |
|---|---|
| **winter** | finish connector/bom → S1 L2 verification gates (shard 0/3) |
| **happy** | power_v2 finish → S1 gates (shard 1/3) |
| **kloey** | intake daemon + S4 identity binding / bucket-6a intake (shard 2/3 later) |
| **TP4** | errata bind (running) → F2 OPN-grammar assist → Ph2 hard crops |
| **m5** | spine coordinator: S0 census → S1 build → S3 → S5/S6; vision batches 2+ |

## Laws that bind this roadmap

- Deterministic first, model second, frontier last — same ladder as extraction.
- Nothing ships unverified: L2 gates or it doesn't exist; verdicts never model-decided alone (canon REJECT authority).
- Every normalized value keeps its verbatim ancestor — one hop from the page.
- comparable_accepted never counts axes whose units didn't canonize — fail closed.

*From: m5-opencode · approved in principle 2026-10-20 (Sam: "get our machines pumping today")*
