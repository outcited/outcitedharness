# CORPUS.md — where the source is, always

> **TL;DR:** every document we hold is a blob named by its sha256 in a `cas/`
> tree. The canonical copy lives on **charleybasecamp (192.168.4.54)** at
> `/volume1/Electronicdataassets/corpus/`. The index of every blob is
> `INDEX.jsonl` next to it and mirrored at `~/Harnessv1/corpus/INDEX.jsonl`
> on m5. Never hardcode a path again — resolve shas through the INDEX.

Status 2026-10-19: **59,369 unique documents · 81.5 GB · verified by lookup
across 4 stores.** See `corpus/MANIFEST.lock` on the NAS for the receipt
(index sha256 `b794b9d4…`).

---

## 1. The law (why we stopped losing things)

1. **Content is canonical by sha256.** The same PDF delivered five times is
   one blob. The vault dedupes by design; queues that don't, lie (the
   "118,795-doc queue" was ~68K documents counted multiple times).
2. **Paths are resolved, never hardcoded.** Absolute paths rot
   (`/Volumes/macbookM4-4TB`, `/mnt/eda`, happy-local …). Consumers carry
   shas; the INDEX + REGISTRY resolve to a live mount.
3. **Lookups, not listings.** The Asustor NFS export enumerates directories
   non-deterministically (the same tree listed 65,360 / 65,617 / 105,460
   entries across runs — `find`/`readdir` walks silently truncate, which cost
   us a full afternoon of "lost" data). `stat`/`open` by sha are reliable.
   Inventory = lookup (`corpus/inventory_lookup.py`), never a walk.
4. **Caches are expendable.** Only the canonical store is authoritative.
   Any cache can be wiped and re-rsynced from canonical.

## 2. Stores (the REGISTRY — machine-readable copy in `corpus/REGISTRY.json`)

| store | box | root | role | state |
|---|---|---|---|---|
| **nas54** | charleybasecamp (192.168.4.54) | `/volume1/Electronicdataassets/corpus` | **CANONICAL** | complete 59,369 |
| m5_4tb view | m5 (+winter via `/mnt/eda/m5-4tb`) | `/Volumes/M5_4TB/vault/cas` | cache (NFS ro) | complete 59,369 |
| macmobile | happy, external disk | `/mnt/macmobile/vault/cas` | cache | complete 59,369 |
| yosemiteraid5 (.44) | NFS `Public/m5-opencode/corpus-mirror/cas` | — | cache (deliveries mirror) | complete 59,369 |
| happy_local | happy | `/home/samsonkim/corpus-vault/cas` | cache | complete 59,369 (repaired 2026-10-19, was −2,256) |

Mount commands live in `corpus/REGISTRY.json`.

## 3. The orchestrator's corpus kit (m5: `~/Harnessv1/corpus/`)

| file | what it does |
|---|---|
| `INDEX.jsonl` | sha → `{size, locations{store: path}}` — THE answer to "where is X" |
| `sha_universe.txt` | every known sha (queues + census + vault walk union) |
| `inventory_lookup.py` | store inventory by lookup (readdir-immune). Run **on** the box holding the store |
| `merge_inventory.py` | merge per-store TSVs → INDEX.jsonl, gap report |
| `backup_disk.sh` | **plug in a disk, back up all raw**: `corpus/backup_disk.sh /mount/point` — rsyncs cas + INDEX + REGISTRY, verifies counts, writes `BACKUP.manifest` receipt |
| `REGISTRY.json` | stores, roles, mount commands |
| `MANIFEST.lock` (on NAS) | index sha256 — tamper-evident |

## 4. How to answer the questions that used to start fights

- **"Where is our source?"** → charleybasecamp `corpus/cas` (canonical,
  complete). Verify any time: count files or re-run the lookup inventory.
- **"Do we have document X?"** → sha it (or look it up in a delivery
  manifest by filename), then `grep '"<sha>"' INDEX.jsonl` → locations.
- **"Is it backed up?"** → run `backup_disk.sh` onto any disk; the
  `BACKUP.manifest` receipt proves it (counts + index sha).
- **"Is a mirror complete?"** → lookup inventory vs INDEX (`merge_inventory`
  prints per-store coverage and gaps). The old "readdir" answer was wrong
  and we chased ghosts because of it.

## 5. Facts settled on 2026-10-19 (do not re-litigate)

- The **macbookM4-4TB corpus is not lost**: 3,947/4,053 shas recovered from
  the L0 substrate results table (`pipeline-live.db`, `results.document_sha256`
  joined to census paths) and verified present in the vault. 116 truly
  missing = passive-component datasheets (resistors/caps), dispositioned
  `missing_source` — if the physical 4TB drive ever surfaces, one
  `--resume` pass eats them.
- The `._`-prefixed AppleDouble entries in old queues are **macOS metadata
  junk, not documents** — drop them, never queue them.
- Queue item counts are not corpus counts. Only the INDEX is.

## 6. Extraction fleet (who burns what)

- **winter** (Ryzen 9955HX, 32T) — main burn worker (`burn_pass.py`,
  shared-parse, 28 workers). Vault queue 2026-10-19.
- **happy** (Ryzen, 32T, 60G) — new-facet lanes (power_v2 axes via feeder)
  + power-axes burn. Main muscle with winter.
- **seven** — orchestration/director ONLY. No extraction.
- **m5** — orchestrator: queues, INDEX, consolidation, small retry tails.
- `burn_pass.py` scheduling law: hung doc → SIGKILL its worker pid,
  `multiprocessing.Pool` respawns the slot; nobody else ever waits on a
  pathological PDF. PyMuPDF is processes-only (threads are forbidden by
  upstream and crash).
