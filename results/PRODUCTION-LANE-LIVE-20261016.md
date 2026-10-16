# PRODUCTION POWER LANE — LIVE

**date:** 2026-10-16T14:30:00Z
**approved by:** Sam ("Go")
**controller:** Rust dw-controller (first production ownership)

## Status at go-live

| metric | value |
|---|---|
| documents discovered | 9,201 (full power aisle) |
| ready for processing | 3,490 |
| with local substrates | 2,212 |
| chunks registered | 89 (25 docs each) |
| worker node | prod-power-1 |
| model lane | TP4-local (fail-closed, $0 cloud) |
| chunks claimed | 1 (processing) |
| chunks queued | 88 (available) |
| events recorded | 90 |

## What Rust owns (production)

- ✅ Document discovery (9,201 PDFs discovered, classified, deduped)
- ✅ Stability windows (0s — immediate eligibility for known-good docs)
- ✅ Queue registration (89 chunks, 2,212 work items)
- ✅ Chunk leasing (worker claimed chunk 1, attempt 1)
- ✅ Event ledger (90 events, immutable)
- ✅ Recovery path (proven in canary: kill → expiry → requeue)

## What Python owns (unchanged)

- ✅ Model calls (TP4-local via soak_model_shim.py)
- ✅ Text extraction (burn_aisle.py's propose/repair protocol)

## Rollback

Flip the launcher: stop the Rust worker, restart `burn_aisle.py`.
The Python path is untouched and functional.

## Monitoring

```bash
# Check production status:
cd /Users/samkim/Harnessv1-powerdata01/designwins
target/debug/dw-soak status --state /Users/samkim/Harnessv1/results/production-power-lane/state-rust

# Check worker log:
tail -5 /Users/samkim/Harnessv1/results/production-power-lane/worker-1.log

# Count output documents:
find /Users/samkim/Harnessv1/results/production-power-lane/output -name "*.json" -not -name "_*" | wc -l
```
