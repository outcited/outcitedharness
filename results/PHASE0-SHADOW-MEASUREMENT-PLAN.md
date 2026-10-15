# Phase 0 Shadow Controller — 7-Day Measurement Plan

**prerequisite:** Sam approves the operating window
**tool:** `dw-shadow` (built, clippy-clean, read-only)
**output:** `results/shadow-factory.sqlite3` (separate from production)

## What the shadow controller does

Runs `dw-controller::discover_pdfs` on the same PDF directories Python's
`burn_aisle.py` watches, at the same stability window. Records discovery,
deduplication, stability, and quarantine decisions in a SEPARATE shadow
database. Never claims chunks, never modifies Python's work queues, never
touches the production database.

## Daily comparison (the acceptance metric)

Each day, diff the shadow event ledger against Python's implicit outcomes:

| metric | how to compare | pass bar |
|---|---|---|
| docs discovered | shadow `discovered_paths` vs Python's `len(pdfs)` | exact match |
| new observations | shadow `new_observations` vs Python's skip-reason count | exact match |
| ready (eligible) | shadow `ready` count vs Python's substrate-hit count | exact match |
| duplicates | shadow `duplicates` vs Python's skip-reason "duplicate" | exact match |
| quarantined | shadow `quarantined` vs Python's skip-reason "bad_pdf" | exact match |
| unchanged (re-seen) | shadow `unchanged` vs Python's already-done count | exact match |

**Any divergence is a bug** — the two engines should make identical
decisions on the same documents. The shadow database's immutable event
ledger is the evidence trail.

## Launch command

```bash
# Shadow the vault aisle (where Python's burn runs):
cargo run -p dw-controller --bin dw-shadow -- \
  --roots /Users/samkim/Harnessv1/results/pdfs-incoming \
  --stability 120 \
  --db /Users/samkim/Harnessv1/results/shadow-factory.sqlite3 \
  --interval 300
```

## Exit criteria (7 days)

| day | gate |
|---|---|
| 1 | Shadow discovers the same doc count as Python's latest run |
| 2 | Zero new-observation divergence |
| 3 | Zero dedupe/quarantine divergence |
| 4-5 | Stability-window timing matches (docs become ready at the same round) |
| 6 | Event ledger count matches Python's implicit decision count |
| 7 | **7 consecutive days of zero divergence → Phase 1 approved** |

## Rollback

The shadow controller is read-only. Rollback = stop the process and delete
`shadow-factory.sqlite3`. Zero production impact.

## Comparison script (to run daily)

```bash
# Count shadow decisions
sqlite3 results/shadow-factory.sqlite3 \
  "SELECT status, COUNT(*) FROM factory_source_observations GROUP BY status"

# Compare against Python's burn output
python3 -c "
import json; from pathlib import Path; from collections import Counter
skips = Counter()
for f in Path('results/soak-m3-vault-local').glob('*.json'):
    r = json.load(open(f))
    if 'skip' in r: skips[r['skip'][:30]] += 1
    elif 'tier' in r: skips[f'tier:{r[\"tier\"]}'] += 1
for k, v in skips.most_common(): print(f'{v:5d} {k}')
"
```
