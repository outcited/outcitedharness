# ACTIVE-WORK-RECON — DIRECTOR-RECON-01

**date:** 2026-10-16 (late evening session) · snapshot of in-flight work, owners, blockers, stale state.

## Live right now

| work | owner/machine | state | evidence |
|---|---|---|---|
| **Production POWER lane (Rust)** | m5-opencode + power-data-01 | LIVE — Rust controller owns discovery/queue/lease/recovery; 2,212 docs queued, 89 chunks at commit; factory_events growing (1,490) | commit `d09d7d6e`; `ra03-state/factory.sqlite3` |
| Facet waves (thermal/mode/aec/errata/pillars/paxes/conn/ti) | MBP (front) via `facet_wave_orchestrator.sh` | running, 200-doc waves; ~5,200 docs banked pre-refactor; per-facet ledgers | `results/mbp-worker-20261014/*.log`, `done-*.txt` |
| asus2 facet runner | — | **STOPPED tonight** (box repurposed to ops brain) | kill trail; GPU 0% |
| M5 local lane runner | — | **STOPPED per Sam's directive** — "M5 out of working extractions" | session directive |
| extract_dispatch (substrate queue) | M5 launchd | running; churning `._` junk dead letters (176,709 dead) awaiting factory fix | pid 73749; `pipeline-live.db` |
| Ops brain NVFP4 | asus2 `canon-ops-nvfp4` | **deployed + acceptance-passed tonight** (24.7 tok/s, tools 5/5) | `config/models.yaml:asus2_ops_brain`; `/tmp/asus2_suite.py` results |
| dgx1 overflow replica | dgx1 `canon-replica` | **up tonight** after 3 days down (intentional stop, never restarted); 3/3 verdict parity | docker ps; replay test |
| TP4 DeepSeek shard | dgx2+asus1+dgx3+asus3 | 85–95% GPU | nvidia-smi probes |
| PDF intake | mac-mini → M5 | healthy, 2h cadence; 0 new PDFs since 01:24Z | `pdf-intake-20261015.log` |
| Pin batch (done) | m5-opencode | COMPLETE 23,318/23,318; promotion packet AWAITING RELEASE OWNER | `evidence/pin-batch-20261014/PROMOTION_PACKET.md` |

## Agents & branches

- `agent/power-data-01` — sibling checkout `/Users/samkim/Harnessv1-powerdata01`, HEAD `dd9afa12` (PINOUT-CATALOG-04 + RA-03). Active tonight (soak workers).
- `curve/*` (7 branches), `discovery/*`, `facet/*` — lane branches; current checkout on `curve/07e-evidence-supply`.
- **Uncommitted on this checkout:** tonight's ops work (`config/models.yaml`, `extract_dispatch.py`, 10 new scripts: facet orchestrators, wave_select/merge, mbp lanes, power batch/cloud workers). Needs commit + push to both remotes.
- Mail-driven threads open: capacitor substrate gap (mac-mini question), storage-law directives, cutover reconciliation with power-data-01 (answer sent 2026-10-16T10:30Z).

## Blockers & unresolved

1. **`._` AppleDouble junk** — 172,932 dead jobs TODAY; factory (PDF side) "working on a fix" per Sam. Intake hygiene gate belongs at enqueue (`extract_dispatch.py:scan_and_enqueue` skips `._*` when fixed).
2. **Capacitor substrate gap** — 0 capacitor PDFs collected vs 42,011 target; mac-mini notified (`1b8bfa1a`, question mail 20261015T063000Z). Blocks the next aisle entirely.
3. **NAS nightly backup FAILING** — destination unreachable 2× this morning (`backup-20261015.log`); Public export mount not visible from M5 tonight. Single-NIC since 2026-10-13 (ARP flap history).
4. **Pin promotion** — packet complete, board delta huge (MCU pinout 4.4%→25.6%), waiting on human release owner.
5. **Sam's open calls** — additive-kinds DECISIONS ratification (one closed 2026-10-12; QG/CISS/TF pending), cloud expense approvals.
6. **RA-03 scratch location** — production lane state lives in a *scratch* dir (`scratch/m5-soak/ra03-state`); needs a blessed home before anything else depends on its path.

## Stale / uncertainty flags

- `results/mbp-worker-20261014/done.txt` (old flat ledger) superseded by per-facet `done-<facet>.txt` — do not read the old one.
- `attempts.json` / `attempts-m5.json` / `attempts-asus2.json` — split-brain from tonight's refactor; harmless but consolidate.
- Retired model keys in `models.yaml` (`asus2_qwen`, `deepseek_flash_tp2_shadow`, `dgx3_qwen`...) — enabled:false, kept for bakeoff history.
- `pipeline-live.db` 2029-dated cohort (3,950 rows) — known corrupt timestamps, latent only (`QUEUE_RECOVERY.md` Defect 2, fixed by rowid ordering).
- dgx1 stability — 2 reboots Oct 14; overflow-role only; container `restart=unless-stopped` now applied.
- 3× Ryzen boxes arriving "tomorrow" — extraction workers; corpus push + venv + env-line enrollment is the known pattern (`facet_wave_orchestrator.sh` env contract).
