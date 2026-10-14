# CONTEXT HANDOFF — extraction platform (2026-10-06)

Written for: any agent/session picking this up with zero context.
Owner: Sam. Ratified by: PILLAR_COUNCIL (all agents YES).

---

## 1. What this system is

A datasheet-intelligence factory: 125K+ PDFs → per-page text → structured
claims (specs, pinouts, OPNs, conditions) with **provenance on every value**
(quote + page + row/column headers) → canonical part-keyed truth → sellable
data. Quality law: a claim that cannot locate its source line cannot exist.

## 2. Live state right now (2026-10-06 ~05:00Z)

| Lane | State |
|---|---|
| Substrate (Layer 0) | 81,473 jobs done of ~135K corpus; 3,758 dead = CAS junk, correctly quarantined. New factory PDFs (~770/hr) auto-ingest. |
| Power burn | 5,258/9,201 (re-run with claim persistence after a discarded first run) |
| Catalog (part-keyed truth) | 1,091 parts · 58,411 claims · 47,707 consensus rows · 169 conflicts. GROWING — needs re-fill after burns land (catalog_fill.py). |
| Student v2 (8B LoRA) | **2026-10-09 correction: there is no v2 student.** `asus2:~/harness-training/models/power-tables-student-v2` is a root-owned re-merge of the **v1** adapter — `lora-harvest.log:900` shows the merge loaded `power-tables-qwen3-vl-8b-v1/checkpoint-1020`, no v2 checkpoint dir exists, and all five shards are byte-size identical to `power-tables-student-v1`. The "trained 1020/1020" numbers are the v1 run's (epoch 3.0, loss 0.0078, ~44 h). Serving it and gating against the v2 holdout gives precision 0.33 / recall 0.24 because symbols and values are right while `qualifier` comes back `"absolute_maximum"` and `condition` null on every row — v1's pre-contract curriculum, not the v2 quote-native contract. Evidence + what a real v2 run requires: `results/lora-v2-pairs/GATE-BLOCKED-no-v2-model.md`. Nothing is serving on :8950. |
| Canon (judge) | **Two replicas again as of 2026-10-09 ~04:31Z** (Sam's call): primary `asus4:8900` (up 6 days, `restart=unless-stopped`, the endpoint hard-coded in `harness/pipeline/api.py:22`, `scripts/sentinel.py:26`, `scripts/canon_gate.py:20`, `scripts/soak_sample.py:21`) + `dgx1:8901` `canon-replica` (restored, smoke-tested, ~7 min boot, self-heals now). Both `qwen3.8-27b-fp8` dense, REJECT-authority only, determinism proven (5/5). The dgx1 replica had been stopped deliberately on 2026-10-07 for a liger A/B (mail `e10b-canon-replica-intentional`) whose artifacts do not exist on either box, so canon sat single-replica for >24h with an unevaluable restore condition. The old "asus4:8901 + dgx1:8901" line was wrong for asus4 — it has always been :8900. |
| V4.1 TP4 | Live 100.116.221.82:8888 — vision + 1M ctx + hard pages + optional coding. |
| Embedder + GCI | **RELOCATED to asus4 2026-10-09 (Sam's architecture: 2 trainers + 1 serving box).** `100.100.116.82:8800` embedder (same FAE **v4** weights, sha256-verified) + `:8810` GCI (same release, same token, index synced). Cutover gate: `verify_equivalence.py` 8/8 **bit-identical** vs e10b before repointing. e10b's pair stopped+disabled, assets kept as rollback. All four weight generations backed up to the NAS (`BACKUP-bge-m3-fae-search-v4.md`). GCI still fails soft (`orch.py:1153`). asus4 = canon primary + pair, ~33G headroom. |
| Vision lane | vision_worker_v2 running (crop-don't-page + per-cell bbox + OCR verify). MiMo vision tested — **loses to DeepSeek**, dropped. |
| DR backup | mac-mini seeds extract-results/agent-inbox/canon-gate to Tapes_4TB nightly 03:33. The vault survived the Oct-4 16TB death BECAUSE of this. |
| Watchers | fleet-state (7 nodes, hourly, change-mail) · soak (hourly vitals) · sentinel (hourly claims re-verify + nightly gold drift). **2026-10-09: fleet-state was wrong about three things and is now fixed** — asus4 probed through a dead fabric relay (`10.77.0.6`, false `SSH_FAIL` for 4 days), embedder probed on a route it 404s (recorded `[]`, i.e. indistinguishable from healthy), and GCI not probed at all. It now probes `/healthz`, `:8810/health` and `asus4:8900` directly; 27 tests in `tests/test_fleet_state.py`. |
| Training rig | asus2+dgx1 fabric link (200Gb via switch) ready for 27B TP2 when corpus justifies it. Note asus2 is at 96% disk (44G free) — see RUNBOOK cleanup list before scheduling anything. |

## 3. The laws (non-negotiable, ratified)

1. **Provenance is law** — every claim carries {value, verbatim_quote, sha256, page, row_header, column_header}. No quote → no claim.
2. **Deterministic core, nondeterministic proposals** — models generate; code verifies (quote matching, geometry, OCR). Variance costs rework, never correctness.
3. **No MoE in adjudication** (Sam rule). Dense only.
4. **P0/P1 severity ladder** — P0 quarantine rows; P1 warn+hold with class thresholds; **nothing stops the line except truth-corruption at scale**.
5. **One extraction API** — all agents submit {pillar, grain, corpus, schema, budget}. Nothing off the PILLAR_COUNCIL list gets extracted. Sam decides.
6. **Frontier = arbitration only** (quota_ok, capped). Local wins on cost AND fabrications (9 vs 12 on gold).
7. **cell_verifier (M4) stays sole writer of `verified`** — our verdicts feed it as evidence.
8. **Scheduled teardowns mail consumer boxes 1h ahead** (learned from the 8,688-doc loss Oct 2).

## 4. Architecture map (files that matter)

```
harness/pipeline/       quote_verify (L0/L1/L2 + wrong-cell + temp-convention)
                        region_detect (table bboxes) · crop_verify (OCR close-loop)
                        store (SQLite queue: claim/ack/visibility/dead-letter)
                        ladder (P0-P4) · api (one front door) · census · ball_validate
harness/catalog/        store (parts, claims_canonical, consensus, doc_revisions)
scripts/                burn_aisle (aisle burns, cloud/student/local tiers)
                        catalog_fill (burn→catalog) · lora_gate (student gate)
                        sentinel (24/7 regression) · extract_dispatch (v2, SQLite)
                        extract_worker (substrate) · vision_worker_v2
                        coverage_report · judge_bakeoff · cloud_vs_local
                        canon_gate · canon_determinism_probe · kimi_vs_deepseek
PILLAR_COUNCIL.md       the priority record (RATIFIED)
RUNBOOK.md              3am doc — failure modes, fabric map, protocol rules
MODEL_SETTINGS.md       per-endpoint settings IN BLOOD (3 reasoning-mode bugs)
SYSTEM_SUMMARY.md       the 2-pager + ASCII architecture
TRUTH_PLAN.md           the 4-phase verification plan (Phase 1-2 shipped)
```

## 5. Key endpoints

| What | Where |
|---|---|
| V4.1 TP4 (engine) | 100.116.221.82:8888, `deepseek-v4.1-flash`, thinking off via `chat_template_kwargs {"thinking":false}` |
| Canon (judge) | **two replicas since 2026-10-09 ~04:31Z (Sam's call):** `asus4:8900` (100.100.116.82, primary — the endpoint hard-coded in `harness/pipeline/api.py`, `scripts/sentinel.py`, `scripts/canon_gate.py`, `scripts/soak_sample.py`) and `dgx1:8901` (100.81.201.24, `canon-replica`, `restart=unless-stopped`). Both `qwen3.8-27b-fp8`, `chat_template_kwargs {"enable_thinking":false}`. Failover: boot takes ~7 min and `docker start` returns before it serves, so poll `:8901/v1/models`. Superseded note: an earlier revision said "asus4:8900 only, it was never :8901" — true while the replica was deliberately stopped for the liger A/B, false after the restore. |
| Student v2 | **TRAINING on asus2 since 2026-10-09** — container `power-tables-v2-train` (image `harness/llamafactory-qwen3-next:20260901`), output `asus2:~/harness-training/checkpoints/power-tables-qwen3-vl-8b-v2/`, 645 steps at ~3.1 min/step (~33h). At 2026-10-10T03:05Z: step 150/645 (23.26%), epoch 0.70, loss 0.0196, ETA ~1d3h, 267G free, no checkpoint dir written yet. Watched by launchd `com.harness.v2watch` (15 min, `scripts/watch_v2_run.py`, mails on transition only) — read `trainer_log.jsonl`, never `docker logs`. Serving is still **down** (nothing on :8950) and unchanged: `scripts/qualify_student_v2.py` (asus2 trains → adapter synced to dgx1 → dgx1 serves :8950 as `power-tables-student-v1`), a manual script, not a watcher. Its two known bugs still stand: it watches `/training/checkpoints/power-tables-qwen3vl-8b-v2` (note the missing hyphen — a path that has never existed; the real one is `power-tables-qwen3-vl-8b-v2`) and `adapters/current` is empty, so `serve_student_v2.sh` silently serves the **base** model with no adapter. Fix both before gating v2. |
| Production embedder | **asus4 100.100.116.82:8800** since the 2026-10-09 relocation off dgx1/e10b (`bge-m3-embed.service`, systemd `Restart=always`, enabled). Label `bge-m3-cr-tapes-v1`, **actual weights `bge-m3-fae-search-v4/checkpoints_20260822T045420Z`** — no symlink indirection any more, `/healthz` reports the real path. Health: `GET /healthz` (not `/v1/models`, which 404s). e10b's copy is stopped+disabled and kept as rollback. **C6 ruling (Sam, 2026-10-09, RUNBOOK "Protocol rules"): ONE embedder fleet-wide.** Consumers are read-only throttled batch clients (**≤4 concurrent**), pin every stored vector to the served-weight fingerprint, never mix weight generations, and never stand up a second embedder (the asus2 vanilla-bge-m3 impostor of 2026-10-08 — same port, same dims, different vector space — is the cautionary tale; it has been removed). Coordination mail: `embedder-coordination-e10b-batch-client-20261009` → cursor-tapes. |
| GCI | **asus4 100.100.116.82:8810** (moved with the embedder — `harness/gci/encoder.py:28` welds them). `GET /health` → **401 = alive** (bearer-guarded). Binds only its Tailscale address: `127.0.0.1:8810` returns HTTP 000 even when healthy, so probe the Tailscale IP. Same release (`releases/20260828T184119Z`), same token, index synced. Refresh loop on the Mac: launchd `com.samkim.harness-gci-refresh`, 300 s. Fails soft (`orch.py:1153`). |
| Substrate DB | /Volumes/M5_4TB/extract-results/pipeline.db (contract for M4 — they join by sha) |
| Catalog DB | /Volumes/M5_4TB/extract-results/catalog.db |
| M5 volume | /Volumes/M5_4TB — vault/cas (source of record), extract-results, canon-gate, fleet-state. **Its `agent-inbox/` is FROZEN** (Sam directive 2026-10-08, `MOVED.md` in the tree) — read-only history, never a write target. |
| Canonical agent inbox (NAS) | `\\charleybasecamp.local\Electronicdataassets\agent-inbox` (192.168.4.54, Synology DSM 7.4), mounted at **`~/Volumes/Electronicdataassets`** by launchd `com.harness.nasmount` using the SMB account `harness` (creds `NAS_SMB_USER`/`NAS_SMB_PASS` in `Harnessv1/.env`; account created + write-verified 2026-10-09/10). Read `agent-inbox/INDEX.jsonl` first — every send appends one row. One dir per agent. Same share also holds `backups/m5/` (rsync-over-SSH target of `scripts/nas_backup_m5.sh`, which rides the `Charley` account, not `harness`). |

## 6. Open issues (ordered by cost of leaving them)

1. **Student v2 — IN FLIGHT (training on asus2 since 2026-10-09; was "does not exist").** The request this item reopened is now running: `power-tables-v2-train`, 645 steps, step 150/645 (23.26%) at 2026-10-10T03:05Z, ETA ~1d3h, watched by `com.harness.v2watch`. Remaining work is unchanged in shape: let it finish, merge, then gate. History worth keeping — the old "gate verdict UNRESOLVED" item assumed a v2 candidate was waiting on a verdict; there was none, because the directory named v2 held the v1 adapter merge (proof in `results/lora-v2-pairs/GATE-BLOCKED-no-v2-model.md`). The scoring bug (`param` vs `symbol`) is moot for v2 — re-running the gate on a v1 model against the v2 contract can only fail. v2 pairs came from catalog (quote-native; the 87K persisted burn claims are the source), trained on asus2 (the only liger-free qualified box). Budget note held up: v1 was 2,713 pairs × 3 epochs = 1,020 steps in ~44 h. **Still unfixed and it will bite at gate time:** `scripts/qualify_student_v2.py` watches `/training/checkpoints/power-tables-qwen3vl-8b-v2` — missing hyphen, a path that has never existed (the run writes `power-tables-qwen3-vl-8b-v2`) — and `adapters/current` is empty, so `serve_student_v2.sh` silently serves the **base** model with no adapter.
2. **Catalog re-fill needed** after burns land (87K+ new persisted claims). Run `scripts/catalog_fill.py`.
3. **Cloud burn tier is LIVE — corrected 2026-10-08.** The old (drained) key was replaced; the key now in `.env` shows **$249.97 remaining** of a $250 limit (verified via /auth/key). Direct DeepSeek key also holds **$31.00** (`deepseek-flash`, `deepseek-v4-pro`). MiMo holds 82B plan credits; Kimi a 5h rolling quota. Daily caps: `burn-cloud` 25,000 jobs/day (store.py). The earlier "-$0.36 dead" note referred to the retired key and misled a session into treating cloud as unavailable — verify balances via `/auth/key`, never from this doc.
4. **MiMo token plan** (tp- key, token-plan-sgp.xiaomimimo.com/v1) — 82B credits. Vision test LOST to DeepSeek (precision 46.9% vs 68%), dropped per Sam. Key available for other uses. Reasoning toggles: `enable_thinking:false` works (2s calls).
5. **Kimi Code plan** (sk-kimi key, api.kimi.com/coding/v1) — 5h rolling quota; vision bakeoff QUOTED but never completed (window exhaustion). Multi-page batching is the right test shape if revisited.
6. **Errata wave incoming** (TI + Renesas now, ST climbing) — T3 corpus tier, feeds Plan 3 revision engine (`doc_revisions` + `revision_diff` consensus, built and tested). Route to text extraction + revision-history parsers.
7. **T4: 24K unassigned assets HELD** until cr-core ships aisle mapping (ratified protocol).
8. **TRUTH_PLAN Phase 3-4 pending** — text-as-hint verification, frontier arbitration packs. Plan doc has details.
9. Vision lane throughput is slow (walking 69K routes serially) — parallelize or prioritize when it matters.

## 7. Recent decisions worth not re-litigating

- MiMo vision: tested 144 pages against gold, loses on precision — dropped. No ensemble contortion (Sam: "if it sucks we don't have to use it").
- P2P DAC between Sparks DOES NOT WORK (NVIDIA firmware expects switch). The fabric always wins. ConnectX cables: QSFP28-class OK through switch.
- dgx1 (spark-e10b) is an ACTIVE fleet node (CategoryRank retired) and **must-have**, but **not** for the reason previously recorded here: the production embedder (:8800) and GCI (:8810) moved to asus4 on 2026-10-09, and e10b's copies are stopped+disabled (kept on disk as rollback: `/data/categoryrank/cr-finetune`, `/data/harness-gci`, `/home/samkim/harness-gci`). What makes e10b must-have now is the **second canon replica** on `:8901` (`canon-replica`, restored 2026-10-09 ~04:31Z on Sam's call, `restart=unless-stopped`, self-heals after reboot) — without it canon is a single replica and adjudication is one OOM away from a stop. `student-serve` exited 0, :8804 is retired and not listening. It is Sam's designated **trainer 2, pending liger-free qualification** — still training-ineligible per the 2026-10-07 liger verdict until that qualification passes. The embedder↔GCI weld (`harness/gci/encoder.py:28` refuses any encoder URL that is not exactly `http://127.0.0.1:8800/v1/embeddings`) is why the pair moves together and why GCI now binds asus4's Tailscale address only.
- asus4 runs Tailscale-only (gave its fabric port to dgx1). Probe and ssh it at `100.100.116.82`; `10.77.0.6` is unroutable and produced four days of false `SSH_FAIL`. asus4 is **must-have**: the only live canon/judge.
- asus2 was recorded here as "capacity, not must-have". **That is no longer true while the v2 run is in flight** (2026-10-09 → ~2026-10-11): it is Sam's **trainer 1**, the only box running `power-tables-v2-train`, and it still holds the fleet's only copy of `MiniMax-M2.5-REAP-139B-A10B-NVFP4-GB10` (75G), `power-tables-student-v1`/`-v2` (34G) and `Qwen3-VL-8B-Instruct` (17G), plus the extraction worker and the liger-free qualification. Its docker image `harness/llamafactory-qwen3-next:20260901` (24.5G) is load-bearing too — the live trainer runs on it, so it is not a reclaim candidate. Two corrections to what used to be here: the vanilla `BAAI/bge-m3` impostor on :8800 was **removed** on 2026-10-09 (process killed, `@reboot` cron deleted, `~/embedder` + `~/.cache/huggingface/hub` gone, 10.3G; crontab now empty), so **asus2 :8800 is free and ":8800 anywhere in this fleet" now means the real embedder on asus4**; and the disk numbers moved — the 567G of byte-identical duplicates (checksum-verified, evidence in `/Volumes/M5_4TB/fleet-state/disk-reclaim-20261009/`) was reclaimed 535G on 2026-10-09 ~04:20Z, leaving **asus2 267G free (70%)** and **asus4 211G free (76%)**, not ">94% full". Ranked plan + what was deliberately left alone: RUNBOOK.md §"Disk pressure". New since: a **1.8T Samsung PSSD T7 (exfat) is attached to asus2 as `/dev/sda1` and NOT mounted** — cold-storage headroom for weight backups, but exfat has no POSIX perms or symlinks, so never a training path or HF cache.
- **C6 EMBEDDER — RULED (Sam, 2026-10-09) + coordinated with the Tapes team.** ONE embedder fleet-wide: the production unit on asus4 :8800 (FAE v4 weights behind the legacy `bge-m3-cr-tapes-v1` label). Everyone else is a read-only **throttled batch client (≤4 concurrent)**, pins every stored vector to the served-weight fingerprint, never mixes weight generations, and never stands up a second embedder — the asus2 vanilla-bge-m3 impostor of 2026-10-08 (same port, same dims, **different vector space**) is why that last clause exists, and it has been removed. Coordination mail: `embedder-coordination-e10b-batch-client-20261009` → cursor-tapes (delivered to the canonical NAS tree). Full text: RUNBOOK.md §"Protocol rules (in force)". Do not re-litigate by adding a local embedder for convenience; a second vector space silently invalidates every index built on the first.
- The 8B student training data (2,713 pairs) is pre-contract (no quotes). v2 pairs must come from catalog (quote-native). The 87K persisted burn claims ARE quote-native — that's v2's dataset.

## 8. Mail protocol (agent network)

**Canonical tree = the NAS, not the M5** (cursor-cr decision mail
`agent-inbox-nas-migration-20261008`, Sam directive):
`~/Volumes/Electronicdataassets/agent-inbox/<recipient>/` — frontmatter
mandatory, filename `<UTC>_<from>_<to>_<type>_<id>.md`, and **every send appends
one row to `agent-inbox/INDEX.jsonl`** (`ts,from,to,type,id,subject,path`). Read
INDEX.jsonl first: it is every mail from every agent in one file, so nobody has
to hunt inboxes again.

Boxes (16, one dir per agent — not per machine, not per team fragment):
claude, claude-code-browser, claude-code-browser-m4, cr-core, cursor-browser,
cursor-cr, cursor-tapes, dgx-spark, m4opencode, m5, m5-cursor, m5-opencode
(ours), mac-mini, mini-opencode, samson, whitepine. Plus `archive/<origin>/` for
pre-migration history. AGENT_NETWORK.md is the who's-who. Weekly status mail
both ways (extraction lane ↔ mac-mini ↔ cr-core) + 48h stall alerts.

`/Volumes/M5_4TB/agent-inbox/` is **FROZEN** (`MOVED.md` in the tree): history
only, never a write target. Senders must point at the NAS — on this Mac,
`--shared-root=/Users/samkim/Volumes/Electronicdataassets`. **Verify
`mount | grep Electronicdataassets` before sending**: when the share is down that
path is an empty local directory and a "send" silently builds a fake tree nobody
reads (same failure class as the retired `/Volumes/shared` incident of
2026-06-18). If the mount is missing: `launchctl kickstart
gui/$UID/com.harness.nasmount` (creds in `.env`, account `harness`).

Two ways mail got stranded in the frozen tree, both fixed 2026-10-10: the fleet
probe's `INBOX` constant still pointed at M5 (it now resolves the NAS inbox,
spools to `~/Logs/fleet-state-mail-spool` if the share is down, and never falls
back to the frozen path), and the 2026-10-09 drain was partial — 62 mails were
still only on M5, including the migration decision itself and four cr-core mails
addressed to us. `scripts/drain_frozen_inbox.py` (dry run by default,
`--apply`, idempotent, copies not moves) is the tool; re-run it whenever a
sender is found still on the old path.

## 9. Discovery layer addendum (2026-10-08, opencode)

Phase 0+1+2 of the Power discovery aisle shipped, all gold-gated
(1331 tests green). Extraction-side additions:

- `harness/electronics/condition_model.py` — typed conditions +
  condition_covers comparator (exact/covers/partial/mismatch/absent).
  Gold: tests/fixtures/gold/condition_model_v1.jsonl (167 hand-labeled,
  0 misparse). Population scoreboard: 87.4% of condition-bearing rows
  typed. Backfilled into catalog.db `conditions_typed` (fill-only,
  parser_version stamped, drift-checked 0).
- `harness/electronics/power_topology.py` — topology/integration-class/
  isolation reader, printed-evidence-only. Gold:
  power_topology_v1.jsonl (105 parts, 13 aisles). Wave:
  extract-results/power-topology-v1.jsonl (9,201 parts; 49.7% carry ≥1
  topology; buck-boost compound claims mark derived components).
- `harness/electronics/rating_class.py` — absmax/recommended/rated_limit/
  typical/transient/surge/unknown projection + margin-aware comparator
  (margin never silent, applies to the requirement). Gold:
  rating_class_v1.jsonl (63 rows).
- `harness/discovery/` — evaluator (PASS/NEAR_MISS/FAIL/UNKNOWN ledger,
  unknown-never-eliminates, wrong-bound and condition-mismatch guards),
  store (designs.db), service + api (front door :8790, release-pinned
  reads). Demo: scripts/discovery_demo.py. Spec §42/§27/§28/§52/§53
  verified end-to-end over 1,881 parts / 86,736 claims.

Known disclosed limits (fail-closed, by design): catalog claims lack
table_kind, so the §43 rating guard runs on qualifier/symbol only until
grids are catalog-filled; family-borrowed claims pass through with
coverage_kind when present (evaluator surfaces them, does not hide);
constraint-driver per-atom stats overlap by construction.

— opencode (M5, Harnessv1 session, 2026-10-06T05:00Z)
