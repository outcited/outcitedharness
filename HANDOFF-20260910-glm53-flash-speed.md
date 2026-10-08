# Handoff — GLM-5.3-Flash speed cutover + soak (2026-09-10)

For the next agent. Read this whole file before touching anything.

## TL;DR

- **GLM-5.3-Flash** is live on 4 Sparks (DGX2 head + ASUS1 + DGX3 + ASUS3), TP4, 1M ctx,
  native MTP k=4, **CUDA graphs `FULL_AND_PIECEWISE`** + **`--max-num-batched-tokens 16384`**.
  This is the speed profile from Tony's 2026-09-02 correction. Soaked ~8 h under real
  OpenCode load: **~6.6M prompt tokens, ~50K generated, 0 preemptions, 0 engine errors**.
  It is good. Do not retune while Sam is using it.
- **Qwen3.8-Flash-Next** (125B NVFP4) on asus2+asus4 is the other lane. Untouched.
- The only open task is **archiving the 743B flagship to the Mac mini** and reclaiming
  ~406 GB on DGX2. Not urgent; do NOT do it while Sam is actively hitting Flash.

## Repo / git

- Workspace: `/Users/samkim/Harnessv1` (this Mac = `m5max-ai`).
- Branch: `main`. Head: `01c3bf60 Add layout IR and OCR fallback to page evidence`.
- Remotes:
  - `github` → `https://github.com/beargallbladder/outcitedharness.git`
  - `origin` → `https://origin.cursor.com/samkim2dgx/harnessv1.git` (Cursor-hosted)
- **Nothing was committed or pushed this session.** All Flash work is in
  **untracked** `deploy/glm53-flash/` and live on the boxes.
- Many unrelated dirty files exist (`harness/electronics/power_datasheet.py`,
  `tests/test_power_datasheet_*`, `results/key-features-grid-*`, etc.). **Do not touch those** — they are Sam's, not part of this work.
- Do not commit unless Sam asks.

## Files I changed this session (all under `deploy/glm53-flash/`, untracked)

- `site.env` — flipped from eager/8K to **graphs + 16K**:
  - `GLM53F_CUDAGRAPH_MODE=FULL_AND_PIECEWISE` (was `NONE`)
  - `GLM53F_MAX_NUM_BATCHED_TOKENS=16384` (was `8192`)
  - `GLM53F_MAX_NUM_SEQS=6` (kept; 64 is aggregate-only, not 1–2 OpenCode sessions)
  - `GLM53F_DEPLOYMENT_HOLD=0`
  - Pinned image/model unchanged: `ghcr.io/tonyd2wild/vllm-glm53-flash:sm121-v11-dflash2`,
    image ID `sha256:35c6f70f...bc23b6`, model `RedHatAI/GLM-5.3-Flash-NVFP4` @ `240131d6...`
- `launch-rank.sh` — comment block updated; the graph-arg logic itself was already
  correct (NONE→`--enforce-eager`, else `--compilation-config {"cudagraph_mode":...}`).
- `README.md` — speed-profile notes updated.
- Backup of the old pin lives on DGX2: `/srv/models/deploy/glm53-flash-site.env.bak-eager-8192`.

Live copies on every rank: `$HOME/glm53-flash-tp4/{launch-rank.sh,orchestrate.sh,...}`
and NFS-shared site env `/mnt/models/deploy/glm53-flash-site.env` (same file on all 4).

## Live cluster (production = Flash, not flagship)

- Container `glm53-flash-tp4` on all 4 ranks. Head serves `:8000`.
- Endpoints: LAN `http://192.168.4.45:8000/v1`, Tailscale `http://100.116.221.82:8000/v1`.
- Served name: `glm-5.3-flash`. API key: `~/.secrets/glm53-key` on DGX2 (copied to both Macs).
- Profile: native MTP k=4, fp8_e4m3 KV, 24 GiB/rank (`25769803776`), `max_num_seqs=6`,
  batched 16384, `FULL_AND_PIECEWISE`, `reasoning_effort=max`, `enable_thinking=true`,
  tool parser `glm47`, reasoning parser `glm45`, multimodal template `chat_template_mm.jinja`,
  marlin MoE, `--enforce-eager` OFF. Driver 580.173.02 (NOT the 590 graph-deadlock line).
- KV pool reported: 3,774,873 tokens (~3.6× at 1M). Headroom ~21 GiB free after boot.
- Boot to healthy: ~750 s (graphs added ~4.5 min; capture 30 s / 0.29 GiB).
- Required overlay: `sparse_attn_indexer_kpool.py` (sha256
  `8a3ecfb0bab241dd7417ed00a10d142191496149f88e5fe79fcfaea4b160980`)
  or decode past ~24K dies.
- Orchestrate on DGX2: `~/glm53-flash-tp4/orchestrate.sh {preflight|start|stop|status}`.
  Hold is checked before cutover; flagship drain; rollback of flagship on failed start.

### Rank map (200G fabric)

| rank | host | fabric | user |
|---|---|---|---|
| 0 | dgx2 (`spark-49af`) | 10.77.0.1 | samkim2 (Tailscale 100.116.221.82) |
| 1 | asus1 (`gx10-fc2e`) | 10.77.0.2 | samkimasus1 |
| 2 | dgx3 (`spark-69c8`) | 10.77.0.3 | samkim3 |
| 3 | asus3 (`gx10-0309`) | 10.77.0.4 | samkimasus3 |

Weights: `/srv/models/GLM-5.3-Flash-NVFP4-redhat` on DGX2, NFS-mounted read-only
at `/mnt/models/...` on workers.

### Measured (this cluster, graphs+16K)

- Qualify: listing/coherence/tools PASS. C1 count-to-100 **69 tok/s**, C2 **113 tok/s** aggregate.
- Real soak: two concurrent OpenCode streams ~50 tok/s aggregate; single long-context
  decode ~30–37 tok/s; huge prefills (95K–512K) chunked cleanly.

## Why graphs are safe here (the key insight)

`--enforce-eager` is a property of the **b12x / NVFP4-KV lane**, not of this model.
This image is **fp8 KV + marlin**, which is supposed to run graphs. Tony's 2026-09-02
SPEED-RUN correction: `FULL_AND_PIECEWISE` beats eager on every prompt type on this lane.
Do NOT re-enable eager unless you switch to the b12x/NVFP4-KV lane or the topkfix
image (those deadlock under graphs). DFlash2 stays OFF (NC-licensed).

## Qwen3.8-Flash-Next (asus2 + asus4) — analysis only, do not retune

- asus2 head `qwen38-flash-next-head` + asus4 worker, TP2 SGLang,
  image `lmsysorg/sglang:nightly-dev-cu13-20260909-db272201`.
- Endpoint `http://100.68.133.1:8888/v1`, model `qwen38-flash-next-nvfp4-sglang`, 262144 ctx.
- Checkpoint: `RadixArk/Qwen3.8-Flash-Next-NVFP4` @ `7b719225...` (125B, `qwen4_exp`).
- Recipe `~/qwen38-sglang-recipe/.env`: TP2, mem 0.80, chunked prefill **1024**,
  max running 6, SPEC 3/1/4 NEXTN, PLE offload, **`--disable-cuda-graph` /
  `ENABLE_DECODE_GRAPHS=0`**, thinking off. KV bf16.
- Graphs OFF on purpose after prior NaN/assert incidents. Published 2-Spark numbers
  with graphs are ~47 typical / ~70 peak; this site is the conservative lane.
- **Mistake earlier this session:** a probe hit `/flush_cache` on Qwen and wiped its
  prefix cache. Do not GET `/flush_cache` on Qwen.

## Pending: archive flagship to Mac mini (the one open task)

- Source: `/srv/models/GLM-5.3-Int4-Int8Mix` on DGX2 (743B, ~406 GB / 379G on disk,
  282 shards). Flagship container `glm53-tp4` is **Exited** (stopped, not deleted).
- Destination: Mac mini `samsonkim@100.80.101.116`, external `/Volumes/BIGGEYBIGS/model-archives`.
- DGX2 has key `~/.ssh/glm53-archive-macmini` authorized on the mini.
- **Do not delete the source until a checksummed copy exists on the mini.**
- **Do not stop Flash to do this.** Run it when Sam is off Flash.
- After archive + verify, reclaim ~406 GB on DGX2.

## Macs / OpenCode

- `m5max-ai` (this repo): `~/.config/opencode/opencode.json` provider `glm-dgx` →
  `http://100.116.221.82:8000/v1`, model `glm-5.3-flash`, 1M ctx, attachments on.
  Default model still Qwen.
- M4 (`samsonkim@100.102.121.37`): `~/.config/opencode/opencode.jsonc` updated
  similarly; key `~/.secrets/glm53-key`; backup `opencode.jsonc.bak-glm53-flash-20260910T184303`.
  Restart OpenCode.app if picker stale. Picker id `glm-dgx/glm-5.3-flash`.

## Hard rules

- Do not send test inference at Flash or Qwen while Sam is working.
- Do not retune either lane without explicit approval.
- Do not stop/delete the flagship source until the archive verifies.
- Do not commit/push without Sam's say.
- Be explicit about checkpoint names — Sam got angry when Flash vs flagship was confused.
- `lets go` = Flash authorized; flagship stop only when he says he will stop it.

## Contact / access

- DGX2: `ssh samkim2@100.116.221.82` (Tailscale) or `samkim2@192.168.4.45` (LAN).
- Workers reachable from DGX2 over fabric (10.77.0.x); this Mac cannot reach fabric IPs directly.
- asus2 (Qwen head): `ssh samkimasus2@100.68.133.1`.
