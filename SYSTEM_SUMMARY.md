# The Datasheet Intelligence System — Summary & Architecture

**October 2026 · Harnessv1 · one extraction system, every agent, measured truth**

---

## Page 1 — What This Is

A fully-automated factory that turns the world's electronics datasheets into
**verified, provenance-stamped structured data** — descriptions, specs, OPNs,
pinouts, ball maps, and operating conditions — at industrial scale, on our own
hardware, for the cost of electricity.

**The scale it runs at today:**

- **65,633 PDFs** under management; the PDF factory ingests **770 new docs/hour**
- 6,000+ documents already through the text substrate; three extraction lanes
  plus a vision lane, all self-healing
- Frontend locked behind login while we prepare the offering

**The five things that make it unique:**

**1. Provenance is law, not metadata.** Every extracted value must carry a
verbatim quote and its table title, copied character-for-character from the
source. A value that cannot be quoted cannot be asserted. Verification is
therefore *matching*, not judgment — and matching is free, instant, and
perfect. No competitor's pipeline does this at the claim level.

**2. Measured frontier parity — and beyond.** On the hardest adversarial test
we possess (deliberately corrupted gold data), our local, solar-powered rack
produces **fewer fabrications than Claude Sonnet 5.5** (9 vs 12). Frontier
APIs are arbitration-only — a measurement, not a hope. Data quality does not
depend on anyone's API bill.

**3. Hallucination elimination is a control system.** Every failure gets a
class and a ledger entry; caught classes graduate from model-judgment to free
deterministic validators, permanently. Training data is purged of caught
classes each generation, so the student models never learn a mistake the
system has already seen. Fabrications went 48 → 9 in one night of this loop.

**4. No single point of failure, no silent failure.** Seven compute nodes
watched hourly by an automated probe that mails every agent when anything
changes. Queues have real semantics (claim/ack/retry/dead-letter). A node can
wedge — as one did — and the system loses 1/6th throughput, not the lane.
Nothing ever stops the line except truth-corruption at scale.

**5. One system, governed.** Every agent — across three machines and two
teams — submits to a single extraction API and a single priority council.
Nothing gets extracted that isn't on the pillar list. The owner decides;
evidence (section census across the corpus) informs; the ledger audits.

**The business consequence:** the layers nobody else will pay to extract —
errata, footnotes, operating-condition gradients vs. static min/max — become
affordable because a pass costs electricity, not per-token API spend. That is
the moat: the negative-knowledge layer competitors can't economically build.

---

## Page 2 — The Architecture

```
                         ┌──────────────────────────────┐
                         │        PDF FACTORY           │
                         │  crawlers · 770 PDFs/hr      │
                         │  vault/CAS (55K+ assets)     │
                         └──────────────┬───────────────┘
                                        │
                         ┌──────────────▼───────────────┐
                         │  LAYER 0 · SUBSTRATE (free)  │
                         │  every PDF -> per-page text  │
                         │  + printed page labels       │
                         │  + needs_ocr + text quality  │
                         │  versioned (sha, version)    │
                         │  workers: M5 · asus4 · asus2 │
                         └──────────────┬───────────────┘
                                        │
                         ┌──────────────▼───────────────┐
                         │  PILLAR ROUTER               │
                         │  census: absmax 90% ·        │
                         │  pinout 81% · OPN 46%        │
                         │  -> which pages, which tier  │
                         └──────┬───────────────┬───────┘
                                │               │
              ┌─────────────────▼──┐         ┌──▼──────────────────────┐
              │ STUDENT (LoRA 8B)  │         │ VISION LANE             │
              │ routine tables     │         │ render -> V4.1 Flash    │
              │ FP8 · trained on   │         │ pinouts · balls ·       │
              │ VERIFIED pairs only│         │ packages · scans        │
              └─────────┬──────────┘         └──┬──────────────────────┘
                        │                       │
                        └───────────┬───────────┘
                                    │  every claim carries
                                    │  {value, quote, table}
                         ┌──────────▼───────────────────┐
                         │ DETERMINISTIC VERIFIER       │
                         │ L0 exact -> L1 char-class    │
                         │ -> L2 token-window           │
                         │ fabrications = P0, instantly │
                         │ ZERO model tokens            │
                         └──────────┬───────────────────┘
                                    │ residue only
                         ┌──────────▼───────────────────┐
                         │ REPAIR LOOP (value-locked)   │
                         │ verifier names failures ->   │
                         │ proposer fixes quotes or     │
                         │ nulls — never mutates values │
                         └──────────┬───────────────────┘
                                    │
                         ┌──────────▼───────────────────┐
                         │ ADJUDICATION LADDER          │
                         │ canon 27B dense: REJECT only │
                         │ P0 quarantine · P1 hold      │
                         │ batch thresholds — nothing   │
                         │ stops the line               │
                         │ frontier = arbitration only  │
                         └──────────┬───────────────────┘
                                    │
              ┌─────────────────────▼──────────────────────┐
              │ HALLUCINATION LEDGER -> validators         │
              │ caught classes graduate to FREE checks      │
              │ verified rows -> training pairs -> student │
              │ v2 … QUALITY IS GENERATIONAL                │
              └─────────────────────────────────────────────┘
```

**The hardware (all ours, solar-powered):**

| Node | Role |
|---|---|
| dgx2 + asus1 + dgx3 + asus3 | TP4 fabric — DeepSeek V4.1-Flash: 1M ctx, native vision, the extraction engine |
| asus2 | LoRA training → student serving + extraction worker |
| asus4 | Dense canon (adjudication) + extraction worker |
| e10b | Protected embedder (semantic search) |
| M5 Max | Dispatcher, queue, API, watchers — the conductor |

**The guarantees, each with a number behind it:**

- Substrate determinism: re-runs byte-identical (15/15)
- Chaos: 6/6 failure-injection scenarios pass — no job loss, no junk verified
- Frontier spend at nominal load: **$0.00/day** (soak-verified hourly)
- Fabrication rate on adversarial gold: **9 P0s, down from 48** — and falling
- Local vs frontier: local wins the metric that gates training data

**Governance:** PILLAR_COUNCIL.md (priority list, evidence-gated) · fleet
probe (hourly, change-mail to all agents) · severity ladder · runbook ·
eight-mail decision trail. Burn order: **MCU error-finding first**, then
power, connectors, then factory coverage.

*One system. Every claim located. Every failure classified. Every tier
measured. The data you sell is the data you can prove.*
