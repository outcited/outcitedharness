# CURRENT-SYSTEM-MAP — DIRECTOR-RECON-01

**date:** 2026-10-16 · **mode:** read-only recon · **author:** m5-opencode
**scope:** actual running system as verified tonight, with exact evidence refs.

## 1. Machines and control planes

| machine | hardware | role | evidence |
|---|---|---|---|
| `m5max-ai` (M5) | Apple M5 Max, 18c/128G, macOS | **Control plane.** All launchd services, dispatch, facet-wave orchestrators, ledgers, repos | `launchctl list` → ~30 `com.harness.*` jobs; `results/pipeline-live.db` |
| `asus2` (gx10-26b6) | GB10 20c/121G | **Ops brain** — Qwen3.8-27B-NVFP4 `:8900` (since tonight; was extraction worker) | `config/models.yaml:asus2_ops_brain`; docker `canon-ops-nvfp4` |
| `asus4` (gx10-33af) | GB10 20c/121G | **Services box** — canon adjudicator FP8 `:8900`, embedder `:8800`, GCI `:8810`. extract_worker removed tonight | `MODEL_SETTINGS.md` §Local canon; `RUNBOOK.md` |
| `dgx1` / `spark` (spark-e10b) | GB10 120G | **Overflow/backup** — canon-replica FP8 `:8901` (unstable box, containment-only role) | `config/models.yaml:dgx1_overflow`; 2 reboots Oct 14 (`last reboot`) |
| `dgx2` (spark-49af) | GB10 | TP4 shard node + NFS server `/srv/models` — protected, no lanes | `RUNBOOK.md` "Do not put lanes on dgx2" |
| `dgx3`, `asus1`, `asus3` | GB10 | TP4 DeepSeek V4.1 shard (85–95% GPU) | live `nvidia-smi` probe tonight |
| old MBP (`Samsons-MacBook-Pro-2`) | Intel i9-9880H 8c/16G | **Facet extraction worker** (front waves) | `results/mbp-worker-20261014/orchestrator.log` |
| mac-mini | — | **Crawler / PDF factory intake** (spool `MFG_DOSIER_FACTORY` on T7) | `results/pdf-intake-20261015.log`; `shared-docs-queue/outbound/mac-mini/` |
| M4 MacBook Pro | M4 Max 16c/48G | Sam's laptop; 3 opencode agents (CR core, QA, crawler per registry) | `/Volumes/M5_4TB/AGENT_NETWORK.md` |
| whitepine | N100 | Crawler (Tailscale 100.88.22.35) | `AGENT_NETWORK.md` |
| **incoming** | 3× Ryzen 32G NVMe | extraction workers (tomorrow); 1× 9955HX 32G Ubuntu = **Engineering Director target** | user directive |

## 2. Agent identities and mail (verified)

- **Registry:** `/Volumes/M5_4TB/AGENT_NETWORK.md` — identities `m5-cursor` (orchestrator), `m5` (execution), `m5-opencode` (extraction/click lanes), `cursor-cr` (CR core, M4), `m4opencode` (QA), `claude-code-browser(-m4)` (crawlers), `dgx-spark`, `whitepine`. Law: "Identify by box, never by tool."
- **Canonical mailboxes:** `/Volumes/M5_4TB/agent-inbox/{m5,cursor-cr,dgx-spark,...}` — NAS-hosted per Sam directive 2026-10-08 (`scripts/fleet_state.py:NAS_INBOX`; M5 tree frozen, `MOVED.md`). Fleet-state deltas mail to `cursor-cr`, `m5-opencode`, `m4opencode` (`fleet_state.py:MAIL_BOXES`).
- **Repo-committed mail:** `shared-docs-queue/outbound/<to>/YYYYMMDDTHHMMSSZ_<from>_<to>_<type>_<subject>.md` — 13 messages; types observed: `status, question, answer, directive, handoff, work-brief`. Recipients: mac-mini, frontend, power-data-01, dw-api, cursor-tapes. Mail actions are also committed to git (`git log`: `1b8bfa1a mail(mac-mini): capacitor substrate gap`).
- **Broadcast:** `/Volumes/M5_4TB/BROADCAST.md` exists as fleet-wide notice board.

## 3. Change detection & monitoring (all running)

| mechanism | what it detects | output | evidence |
|---|---|---|---|
| `com.harness.fleetstate` → `scripts/fleet_state.py` | infra truth (re-probes `/healthz`, `/health`, `/v1/models`, `docker ps` every run — "truth is derived, never declared") | snapshots + `CHANGES.md` deltas + mailbox mail | `/Volumes/M5_4TB/fleet-state/{CHANGES.md,snapshots/}` |
| `com.harness.sentinel.hourly` / `.nightly` → `scripts/sentinel.py` | **meaning health**: hourly re-verify 5 random claims (must be 100%) + canon 5×5 determinism probe; nightly frozen gold through LIVE chain, drift >2% alarms | `/Volumes/M5_4TB/extract-results/sentinel/ALERTS.md` + `last-known-good.json` + mail | `sentinel.py` docstring: "Infra health = soak; MEANING health = sentinel" |
| `com.harness.soak` | pipeline soak health | soak reports | launchctl |
| launchd exit codes | service crashes | `launchctl list` column 2 (e.g. `pin-lane` = -9 tonight) | live |
| `factory_events` ledger | every Rust-controller decision, immutable (trigger-enforced) | sqlite table | §4 |
| `com.harness.nightly-backup` | results archive to NAS | currently FAILING (destination unreachable) | `results/backup-20261015.log` |

## 4. Rust extraction orchestration (LIVE in production)

- **Workspace:** `designwins/crates/` — 23 crates. Orchestration core: `dw-controller` (discovery/stability/queue/lease/recovery/events), `dw-queue`, `dw-truth` (verifier, 40/40 parity), `dw-normalize` (10,270/10,270 parity), `dw-decide`/`dw-knives` (13 axes), `dw-extract` (30 tests + 5 gold gates), `dw-release` (immutable ReleaseId lineage — §16.2 no duplicate truth, §16.4 no lost lineage), `dw-recon`, `dw-api`.
- **Production state DB:** `/Users/samkim/Harnessv1-powerdata01/scratch/m5-soak/ra03-state/factory.sqlite3` — tables `factory_source_observations, factory_source_batches, factory_chunks (369), factory_frontier_runs, factory_events (1,490)`. Events are append-only via DB triggers (tamper → `Error: factory events are immutable (19)`).
- **Status:** **PRODUCTION POWER LANE LIVE** — commit `d09d7d6e` "Rust controller owns discovery/queue/lease/recovery/events; Python owns model calls; 2,212 docs queued, 89 chunks, worker running". Gate trail: `CANARY-ACCEPTANCE-REPORT-20261016.md` (PASS), `CUTOVER-PLAN-20261015.md`, `CUTOVER-EVIDENCE-EVALUATION-20261016.md`. Kill-recovery proven ×3, zero loss.
- **Python side (still the volume lane):** `scripts/extract_dispatch.py` (launchd `com.harness.extractdispatch`, pid alive) + facet-wave system built tonight: `scripts/facet_wave_orchestrator.sh` + `wave_select.py` + `wave_merge.py` + `mbp_run_lanes.sh` (8 CPU facets: thermal, mode, aec, errata, pillars, paxes, conn, ti) with per-facet ledgers `results/mbp-worker-20261014/done-<facet>.txt`, `parked.txt`, `missing-docs.log`, `attempts.json`.

## 5. Task tracking / law / handoffs

- **Law:** `designwins/docs/DECISIONS.md` — 12 ratified entries (D1–D12), append-only, supersede-by-new-entry ("Never re-litigate"). `PLAN.md` (revision-gated), `PRD-NORMALIZE.md`, `.harness.toml` `[verification] required=["unit"]` (pytest gate on every run).
- **Handoffs:** `HANDOFF-m5-20261012.md` (extraction lead, ladder, Sam's-calls-only list), `HANDOFF-AGENT-ONBOARDING.md` (fleet), `HANDOFF-2026091*` series, `CONTEXT_HANDOFF.md`, `RUNBOOK.md` (29K ops bible).
- **Approval boundary (human-only calls):** ratify DECISIONS entries; cloud expense approval; freeze changes; alias folds crossing physical quantities — `HANDOFF-m5-20261012.md` §"Sam's calls, only".
- **Escalation queue (deterministic):** `scripts/build_datasheet_escalation_queue.py` — mechanical flags only (`cross_source_disagreement`, `external_model_failure`, `terminal_local_attempt`, `package_undercount`) → `queue.json` + sealed candidates. "Only prepares evidence; never submits."

## 6. GitHub / branches

- Remotes: `origin` = `github.com/outcited/outcitedharness`, `cursor` mirror (pushed in lockstep).
- **Branch-per-lane:** `agent/power-data-01`, `curve/*` (7+), `discovery/*`, `facet/*` — branches ARE the task tracker for agent work. Commit messages are status reports (e.g. `1ef7fc44 FACTORY-ORCHESTRATION-03: ACCEPTANCE REPORT — PASS`).
- Sibling agent checkout: `/Users/samkim/Harnessv1-powerdata01` on branch `agent/power-data-01` (HEAD `dd9afa12 PINOUT-CATALOG-04 + RUST-ACTIVATION-03`).
- Uncommitted on this checkout (tonight, mine): `config/models.yaml`, `scripts/extract_dispatch.py`, + 10 new facet/ops scripts (`git status`).

## 7. PDF factory & acquisition

- Intake: launchd `com.harness.pdf-intake` → rsync mac-mini spool every 2h (`results/pdf-intake-20261015.log`: 66,336→67,022 files, ~9GB; delivery 4,980 docs/day = 1.4× need). `._` AppleDouble junk flood = 176,709 dead jobs in `pipeline-live.db` (factory fix pending per Sam).
- Acquisition priorities: `factory_targets.json` (NAS export) — 111,784 assets, 211,130 delivery rows; priority order capacitor 42,011 → diode 9,520 → connector 1,893 (`HANDOFF-m5-20261012.md` §2; `25K-WEEK-READINESS-20261016.md`).
- Corpus: vault `/Volumes/M5_4TB/vault/{cas,landing}` (121,495 wave PDFs / 150GB), L0 census 198,240 docs (`results/l0-census-*`), substrate layer 81K (`/Volumes/M5_4TB/extract-results/{collected,runs}`).

## 8. Release / provenance / publish

- **Export store (NAS):** `yosemiteraid5:Public/m5-opencode/api-store-export/` — `parts_catalog.jsonl` (1,881 parts/86,736 claims), `parts_family.jsonl`, `cube.json`, `nodes_filled.jsonl`, `render_receipts.jsonl` (3,040 doc+page), `typed_axes.jsonl`, `compare_matrix.json`, `coverage.json` (per `HANDOFF-m5-20261012.md` §2). ⚠️ NAS Public mount not currently visible from M5; nightly backup failing (`backup-20261015.log`).
- **Promotion pattern:** `evidence/pin-batch-20261014/` → `PROMOTION_PACKET.md` + `BOARD.md` + `board-snapshot-{pre,post}.json` + `PIN_BATCH_RECEIPT.md` + `qa-stratified-60.json` (sha-verified QA). Promotion is packet-gated, human release owner.
- **Provenance law:** quote+row+col+sha per claim; unquoted = P0 quarantined, never stored; `dw-release` ReleaseId append-only lineage; `EVIDENCE_SNAPSHOTS.md`.

## 9. Inference endpoints & routing (all live-verified tonight)

| endpoint | model | measured | role |
|---|---|---|---|
| asus4 `:8900` | Qwen3.8-27B-**FP8** | 8.3 tok/s; verdict-deterministic 5/5 | **Adjudicator (canon)** |
| asus2 `:8900` | Qwen3.8-27B-**NVFP4** + EAGLE/NEXTN 3/1/4 + qwen3_coder parser | **24.7 tok/s (3.0×)**; tools 5/5 clean | **Ops brain (orchestrator)** |
| dgx1 `:8901` | Qwen3.8-27B-FP8 replica | 3/3 byte-identical vs canon; 3.1s failover | **Overflow/backup (never primary)** |
| dgx2 `:8888` | DeepSeek V4.1 TP4 (4 boxes) | ~30s/doc | extraction engine (1M ctx) |
| OpenRouter | DeepSeek V4.1 batch | $0.004/doc; pin batch 23,318 docs ≈ $10 | approved batch lane |
| MiMo cloud | v2.6 flash/pro | free, channel flaky | volume vision |

Routing config: `config/models.yaml` (keys `asus2_ops_brain`, `dgx1_overflow` added tonight with receipts in comments). Settings law: `MODEL_SETTINGS.md` ("every endpoint gets a settings row after first live verification").

## 10. M2M / session state

- OpenCode agents identified by box (AGENT_NETWORK.md); shared key law ("All 0600, all on m5max-ai; coder key also on spark and M4" — `HANDOFF-AGENT-ONBOARDING.md`).
- Shadow-learning config: `.harness-shadow.json` (teacher gpt-5.6-sol, scoped paths).
- Gateway (exists, worker-facing): `harness/gateway/server.py` — `/healthz`, `/v1/models`, `/v1/chat/completions` → `_complete_orch`/`_stream_orch` with `_authorized`/`_fleet_visible`/`_loopback` gates; tool parsing via `qwen_tools.py:parse_qwen_tool_text`.
