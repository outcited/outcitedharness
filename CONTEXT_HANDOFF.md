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
| Embedder + GCI | Both on dgx1/e10b, both single-copy *services*, both **must-have**. `bge-m3-embed.service` :8800 serves the FAE **v4** fine-tune under the legacy label `bge-m3-cr-tapes-v1`; `harness-gci.service` :8810 is its only continuous consumer (300 s refresh) and is pinned to it by policy (`harness/gci/encoder.py:28` allows only `127.0.0.1:8800`). **2026-10-09: all four weight generations (v1/v2/v3/v31/v4) are now backed up to the NAS and verified** (file count + sha256 + clean `rsync -an`); see `BACKUP-bge-m3-fae-search-v4.md`. GCI fails soft — `orch.py:1153` catches and logs. |
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
| Canon (judge) | **asus4:8900 only** (100.100.116.82), `qwen3.8-27b-fp8`, `chat_template_kwargs {"enable_thinking":false}`. Corrected 2026-10-09: it was never :8901, and dgx1's replica is stopped. |
| Student v2 | **down** as of 2026-10-09 — nothing on :8950. Weights: `asus2:~/harness-training/models/power-tables-student-v2`. Serving path is `scripts/qualify_student_v2.py` (asus2 trains → adapter synced to dgx1 → dgx1 serves :8950 as `power-tables-student-v1`); it is a manual script, not a watcher. |
| Production embedder | 100.81.201.24:8800 (dgx1/e10b), label `bge-m3-cr-tapes-v1`, **actual weights `bge-m3-fae-search-v4/checkpoints_20260822T045420Z`** via the `latest` symlink. Health: `GET /healthz` (not `/v1/models`, which 404s). |
| GCI | 100.81.201.24:8810 (dgx1/e10b), `GET /health` → 401 = alive (bearer-guarded). Refresh loop on the Mac: launchd `com.samkim.harness-gci-refresh`, 300 s. |
| Substrate DB | /Volumes/M5_4TB/extract-results/pipeline.db (contract for M4 — they join by sha) |
| Catalog DB | /Volumes/M5_4TB/extract-results/catalog.db |
| M5 volume | /Volumes/M5_4TB — vault/cas (source of record), extract-results, agent-inbox, canon-gate |

## 6. Open issues (ordered by cost of leaving them)

1. **Student v2 does not exist — CLOSED as asked, reopened as a training request (2026-10-09).** The old "gate verdict UNRESOLVED" item assumed a v2 candidate was waiting on a verdict. There is none: the directory named v2 holds the v1 adapter merge (proof in `results/lora-v2-pairs/GATE-BLOCKED-no-v2-model.md`). The scoring bug (`param` vs `symbol`) is moot for v2 — re-running the gate on a v1 model against the v2 contract can only fail. Real next step is to build v2 pairs from catalog (quote-native; the 87K persisted burn claims are the source), train on asus2 (the only liger-free qualified box), then merge and gate. Budget accordingly: v1 was 2,713 pairs × 3 epochs = 1,020 steps in ~44 h, so a v2 corpus off 87K claims is multi-day. Note `scripts/qualify_student_v2.py` still watches `/training/checkpoints/power-tables-qwen3vl-8b-v2`, a path that has never existed, and `adapters/current` is empty so `serve_student_v2.sh` silently serves the **base** model with no adapter.
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
- dgx1 (spark-e10b) is an ACTIVE fleet node (CategoryRank retired) and **must-have**: it is the sole home of the production embedder (:8800) and GCI (:8810), which are welded together by `harness/gci/encoder.py:28`. Corrected 2026-10-09: it is *not* running a canon replica or a swap-watcher — `canon-replica` is stopped (exit 137) and `student-serve` exited 0. :8804 is retired and not listening. Training-ineligible per the 2026-10-07 liger verdict.
- asus4 runs Tailscale-only (gave its fabric port to dgx1). Probe and ssh it at `100.100.116.82`; `10.77.0.6` is unroutable and produced four days of false `SSH_FAIL`. asus4 is **must-have**: the only live canon/judge.
- asus2 is capacity, not must-have: extraction worker + the qualified liger-free training rig + the fleet's only copy of `MiniMax-M2.5-REAP-139B-A10B-NVFP4-GB10` (75G), `power-tables-student-v1`/`-v2` (34G) and `Qwen3-VL-8B-Instruct` (17G). It also runs an unrelated vanilla `BAAI/bge-m3` on :8800 (`~/embedder`, `@reboot` cron, added 2026-10-09) that is **not** our fine-tune and is referenced by no config — do not point `spark_embed` or GCI at it. asus2 and asus4 hold **567G of byte-identical duplicate weights** (checksum-verified 2026-10-09; evidence in `/Volumes/M5_4TB/fleet-state/disk-reclaim-20261009/`), both boxes are >94% full, and the ranked reclaim plan is in RUNBOOK.md.
- The 8B student training data (2,713 pairs) is pre-contract (no quotes). v2 pairs must come from catalog (quote-native). The 87K persisted burn claims ARE quote-native — that's v2's dataset.

## 8. Mail protocol (agent network)

/volumes/M5_4TB/agent-inbox/<recipient>/ — frontmatter mandatory. Boxes: cursor-cr,
m4opencode, mac-mini, m5-opencode (ours), claude-code-browser(-m4), whitepine,
dgx-spark, m5-cursor, m5. AGENT_NETWORK.md is the who's-who. Weekly status
mail both ways (extraction lane ↔ mac-mini ↔ cr-core) + 48h stall alerts.

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
