# Production POWER Lane — Completion Report
**Date:** 2026-10-17 | **Operator:** m5-opencode | **Controller:** Rust dw-controller

## Summary

First production extraction lane is COMPLETE. 2,212 POWER datasheet documents processed through the full pipeline (discovery → queue → lease → extraction → fold → served store). The lane proves the Rust controller + Python model-call split works at production volume.

## Numbers

| metric | value |
|---|---|
| docs processed | **2,212** (485 TP4 + 1,727 cloud) |
| claims extracted | **70,441** |
| producing docs | 1,256 / 1,727 cloud (72.7% yield) |
| cloud throughput | **1,377 docs/hour** (25 parallel threads) |
| cloud cost | **$25** (OpenRouter DeepSeek flash, `reasoning: effort=low`) |
| cloud time | 75 minutes |
| TP4 throughput | 14 docs/hour (6.6 days equivalent) |
| speedup | **98x** over TP4 |

## Pipeline Architecture (proven)

```
Rust dw-controller          Python (model calls only)
├── discovery (40,633 substrates)    ├── power_cloud_worker.py (OpenRouter)
├── queue (89 chunks)                ├── power_batch_submit.py (batch API)
├── lease/recovery (worker heartbeat) └── soak_model_shim.py (local fallback)
├── events (immutable ledger)
└── fold → api-store-export (15,036 parts served)
```

## Bug fixes during production

1. **`content: None` from OpenRouter** — DeepSeek reasoning model burns all tokens on thinking before producing output. Fix: `max_tokens: 16000` + `reasoning: {"effort": "low"}` (matches burn_aisle.py reference). Without this, every call returns `None`.
2. **Board paths pointed at dead NFS2** (`.40` yosemiteraid5, needs physical reboot). Fix: STAGING → `/Volumes/M5_4TB`, EXPORT → `yosemiteraid5/Public`. Board now reads live mounts.

## Board state (post-production)

- **121,695 addressable parts** from 171,551 extraction records
- **15,036 served parts** in api-store-export
- POWER: 36,636 parts (thermal 2.9%, curves 0.8%)
- MCU: 64,272 parts (pinout 4.4%, pillars 79%)

## Capacitor status (from mac-mini audit)

42k capacitor PDFs (Murata/Kyocera) **already in mac-mini's corpus**. Were not delivered due to drain bug (non-PDF junk flooding substrate queue). Fixed at their end (commit `66bccba`). PDFs arriving next delivery cycle.

## What this proves

1. **Rust controller owns orchestration** — discovery, queue, lease, recovery, events all ran under dw-controller without Python coordination
2. **Python owns model calls** — cloud worker is a pure function: substrate JSON in, claims JSON out
3. **Cloud beats local** — 1,377 docs/hr at $25 vs 14 docs/hr free (TP4). The economics work.
4. **The split holds at volume** — 2,212 docs through the full path without controller/data corruption

## Next cycle (when capacitor PDFs arrive)

1. Build substrates on asus2 (the substrate pipeline is ready)
2. Run capacitor soak through the same Rust controller + cloud worker path
3. Fold into served store
4. Board update
