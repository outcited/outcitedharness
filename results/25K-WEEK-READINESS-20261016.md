# 25K/WEEK READINESS ANALYSIS

**date:** 2026-10-16 · **owner:** m5-opencode (FACTORY-ORCHESTRATION-02)

## Target: 25,000 unique documents/week (3,571/day, 149/hour)

## Current measured throughput (by stage)

### S1 INTAKE (PDF delivery from factory)
| metric | value | source |
|---|---|---|
| Factory delivery rate | 4,980 docs/day (last 24h) | mac-mini PDF-DELIVERY-01 closeout |
| Delivery capacity | 177,537 unique docs in corpus | mac-mini |
| Bottleneck | none — factory exceeds our need 1.4× | 4,980/day > 3,571/day target |

### S2 PARSE (substrate building)
| metric | value | source |
|---|---|---|
| Substrate build rate | not yet measured | needs ASUS2 pilot |
| Substrates available | 40,633 (vault + power only) | local results/substrates |
| Capacitor substrates | **0** | blocker — no capacitor PDFs collected |
| Bottleneck | **substrate gap** — no capacitor/PDF coverage for 80% of target corpus | |

### S3 EXTRACT (model calls)
| metric | value | source |
|---|---|---|
| Python burn (TP4-local) | 375 docs/hour | M3 soak (vault) |
| Python burn (cloud mix) | 196 docs/hour | batch 4 (power, 25 workers) |
| Rust controller | +25.6% wall time vs Python | power-data-01 soak |
| Yield (vault, mixed docs) | 2.35% producing docs | M3 soak |
| Yield (power, datasheets) | ~100% producing docs | batches 1-4 |
| Bottleneck | **model latency** — TP4 ~30s/doc text, ~60s/doc with vision | |

### S4 VERIFY (quote/page resolution)
| metric | value | source |
|---|---|---|
| Verification rate | deterministic, fast | M3 soak (not the bottleneck) |
| Verified/surviving | 94.4% | M3 soak |
| Bottleneck | none — S4 is not the constraint | |

### S5 TYPE (axis binding)
| metric | value | source |
|---|---|---|
| Axes available | 13 (incl. Coulomb, Farad) | D10 landed |
| Classified fraction | 32.64% | registry expansion |
| Bottleneck | **document supply** — need datasheets, not vault mixed | |

### S6 EXPORT (serving store)
| metric | value | source |
|---|---|---|
| Fold + board time | minutes | batch 5 fold |
| Bottleneck | none | |

## The throughput equation

```
25,000 docs/week requires:
  3,571 docs/day ÷ 24h = 149 docs/hour sustained

Current S3 capacity:
  TP4-local only:  375 docs/hour  ✅ (2.5× target)
  With cloud mix:  196 docs/hour  ✅ (1.3× target)

But yield matters:
  Vault (mixed):  2.35% producing  → 8.8 producing docs/hour  ❌
  Power (datasheet): ~100% producing → 375 producing docs/hour  ✅
```

## The real bottleneck: DATASHEET SUPPLY, not processing

The machine can process 375 docs/hour. The problem is that 97.65% of
the vault corpus isn't parametric datasheets — they're manuals, BOMs,
reference designs. The power aisle (9,201 docs) is exhausted.

**What we need:** a corpus of datasheet-dominant documents. The capacitor
target (42,011 docs) is the right next aisle — but those documents need
to be CRAWLED first (they're not in the existing corpus).

## Provider capacity

| provider | cost | rate | capacity |
|---|---|---|---|
| TP4 (local, free) | $0 | ~30s/doc | sufficient for 149/h target |
| OpenRouter (cloud) | pay-per-token | faster | constrained by budget |
| Student model | $0 | depends | not yet proven at scale |

**TP4 alone can carry 25k/week** at 375 docs/hour × 24h × 7d = 63,000/week
if the docs are datasheets. The provider is not the constraint.

## Dependencies and decisions

| # | dependency | needed for | status |
|---|---|---|---|
| 1 | Capacitor PDFs crawled | S2 substrate build | ❌ not collected — mac-mini notified |
| 2 | ASUS2 substrate builder | S2 throughput | ⬜ pipeline ready, waiting on #1 |
| 3 | Sam approves Phase 0 window | shadow controller | ⬜ plan written, tool built |
| 4 | Mac-mini storage-design reply | export destination | ⬜ mail sent |
| 5 | Power-data-01 bug fixes merged | Stage 2+ correctness | ✅ done (5451ae71) |

## Recommendation

1. **Short term (this week):** Process the 8,972 T7 PDFs (mixed, but real
   volume). Build substrates on ASUS2, burn on M5. This exercises the full
   pipeline at ~375 docs/hour even at low yield.

2. **Medium term (next 2 weeks):** Mac-mini crawls capacitor datasheets.
   The factory has capacity (4,980/day delivery rate). The capacitor gap
   is a crawl-priority decision, not a technical blocker.

3. **The 25k/week target is achievable** on current infrastructure IF the
   document supply is datasheet-dominant. Processing capacity exceeds the
   target by 2.5×. The constraint is upstream (collection), not downstream
   (extraction).
