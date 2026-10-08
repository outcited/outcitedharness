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
| Student v2 (8B LoRA) | Trained 1020/1020, merged, serving asus2:8950. **Gate verdict pending** — see issues. |
| Canon (judge) | Two replicas: asus4:8901 + dgx1:8901 — Qwen3.8-27B-FP8 dense, REJECT-authority only. Determinism proven (5/5). |
| V4.1 TP4 | Live 100.116.221.82:8888 — vision + 1M ctx + hard pages + optional coding. |
| Vision lane | vision_worker_v2 running (crop-don't-page + per-cell bbox + OCR verify). MiMo vision tested — **loses to DeepSeek**, dropped. |
| DR backup | mac-mini seeds extract-results/agent-inbox/canon-gate to Tapes_4TB nightly 03:33. The vault survived the Oct-4 16TB death BECAUSE of this. |
| Watchers | fleet-state (7 nodes, hourly, change-mail) · soak (hourly vitals) · sentinel (hourly claims re-verify + nightly gold drift). |
| Training rig | asus2+dgx1 fabric link (200Gb via switch) ready for 27B TP2 when corpus justifies it. |

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
| Canon (judge) | asus4:8901 + dgx1:8901, `qwen3.8-27b-fp8`, `chat_template_kwargs {"enable_thinking":false}` |
| Student v2 | asus2:8950, `power-tables-student-v1` (name legacy; weights are v2 merge) |
| Substrate DB | /Volumes/M5_4TB/extract-results/pipeline.db (contract for M4 — they join by sha) |
| Catalog DB | /Volumes/M5_4TB/extract-results/catalog.db |
| M5 volume | /Volumes/M5_4TB — vault/cas (source of record), extract-results, agent-inbox, canon-gate |

## 6. Open issues (ordered by cost of leaving them)

1. **Student v2 gate verdict UNRESOLVED** — a scoring bug (`param` vs `symbol` key) made recall read 9% falsely; fixed, re-run in flight at handoff. If it passes → dgx1 auto-swaps to student replica (watcher armed). If it fails → 27B TP2 training is next (rig ready).
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
- dgx1 (spark-e10b) is an ACTIVE fleet node (CategoryRank retired) — canon replica + swap-watcher. e10b embedders (:8800/:8804) legacy-retained.
- asus4 runs Tailscale-only (gave its fabric port to dgx1).
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
