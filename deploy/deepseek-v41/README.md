# DeepSeek-V4.1-Flash TP4 (four Sparks) — SGLang

Serving deployment for `deepseek-ai/DeepSeek-V4.1-Flash` (552B MoE, MXFP4 experts,
1M ctx, multimodal + tool calling) on dgx2/asus1/dgx3/asus3, **SGLang TP4/EP4** with
DSpark speculative decoding and NVMe-resident Engram. Replaced the GLM-5.3-Flash TP4
lane on the same four nodes on 2026-09-16.

Source recipe: **MiaAI-Lab/DeepSeek-v4.1-Flash-DGX-Sparks** (126★, AGPL-3.0),
`start-tp4.sh` profile. Author's numbers: 45.4 tok/s C1 prose, 103–134 aggregate
(C4–C16), 3.2–3.8K tok/s prefill, 1M needle verified.

## Why this recipe, not the vLLM one

The first choice was `tonyd2wild/DeepSeek-V4.1-Flash-vLLM-DGX-Spark` (74★). It was
abandoned **before** any weights moved to it took effect, because its pinned base
image `vllm/vllm-openai:nightly-8a728663c1c3eeace834a95f5654fa653cc1998c` had been
garbage-collected from Docker Hub and the per-commit wheel bucket was gone too.
Rebuilding that base from source (pinned tree `e47aa780bc`) works but means
re-deriving every compiled extension; that path is preserved on the nodes in the
`dsv41:overlay*`/`~/vllm-dsv41-src` lineage only as reference, and has been deleted.

Rule adopted: **verify the recipe's build inputs still exist before committing.**
MiaAI's base `lmsysorg/sglang:dev-dsv41` and rhys101's digest
`lmsysorg/sglang@sha256:3475d88e…` were both checked pullable first.

## Fleet map

| rank | node | hostname | fabric IP | user |
|---|---|---|---|---|
| 0 | dgx2 | spark-49af | 10.77.0.1 | samkim2 (head, API :8888) |
| 1 | asus1 | gx10-fc2e | 10.77.0.2 | samkimasus1 |
| 2 | dgx3 | spark-69c8 | 10.77.0.3 | samkim3 |
| 3 | asus3 | gx10-0309 | 10.77.0.4 | samkimasus3 |

- Endpoint: `http://10.77.0.1:8888/v1` (LAN) / `http://100.116.221.82:8888/v1`
  (tailscale), model id `deepseek-v4.1-flash`, **no API key**.
- Fabric: switched RoCE 10.77.0.0/23, HCAs `rocep1s0f1,roceP2p1s0f1`, socket ifname
  `enp1s0f1np1`, GID auto-detected per rank (index 3).
- Weights: head `/srv/models/DeepSeek-V4.1-Flash` (local NVMe); workers read it
  over the **existing host nfsd** at `/mnt/models/DeepSeek-V4.1-Flash` (ro, nfs4).
- Engram: repacked per rank onto node-local NVMe, ~48 GiB/node:
  head `~/dsv41-engram`, workers `/var/tmp/dsv41-4x-spark/engram`.

## Site adaptations (two, both in this directory)

1. **Per-worker SSH usernames** — MiaAI's `start.sh` assumes one `WORKER_USER`; our
   three workers have three different usernames. `start.sh.site.patch` adds a
   `WORKER_USERS` list + `user_for_host()` and uses it in `remote_on` and the rsync
   destination. `.env.tp4` sets `WORKER_USERS`, `SSH_IDENTITY=$HOME/.ssh/id_ed25519`.
2. **NFS bypass** — dgx2 already runs host nfsd on :2049 (exporting `/srv/models`),
   so MiaAI's own NFS exporter container cannot bind. `NFS_SHARE=0`, and each worker
   gets a bind docker volume so `cmd_serve`'s checks pass:

   ```bash
   docker volume create --driver local --type none \
     --opt device=/mnt/models/DeepSeek-V4.1-Flash --opt o=bind dsv41-weights
   ```

   (`--type none` is docker's name for the local bind driver: `--driver local --opt
   type=none`.) Also `READY_TIMEOUT=1800` (default 360 s is shorter than a 12–13 min
   boot).

## Files on the fleet

- `~/DS4.1/` (dgx2): recipe checkout. `start.sh` is patched; `start.sh.orig-site` is
  the pristine copy. `.env.tp4` is the site profile (copy in this directory).
- `~/dsv41-engram/` (head) and `/var/tmp/dsv41-4x-spark/engram/` (workers): packed
  Engram shards.
- Image `dsv41-4x-spark:local` on all four nodes (from base
  `lmsysorg/sglang:dev-dsv41` + the recipe overlay).

## Ops

```bash
cd ~/DS4.1
./start-tp4.sh doctor     # ssh, docker, IB, checkpoint shards, image, busy GPU
./start-tp4.sh build      # base+overlay image on all nodes, rsync recipe to workers
./start-tp4.sh pack       # repack each rank's Engram rows to local NVMe (idempotent)
./start-tp4.sh serve      # workers first, then head; streams until API ready
./start-tp4.sh status     # containers + API
./start-tp4.sh logs [N]   # head engine log;  logs worker2 [N]
./start-tp4.sh smoke      # arithmetic smoke
./stop.sh                 # tear down all ranks
```

Boot is 12–13 minutes (weights ~1 min/rank at TP4 here, then KV pool and CUDA-graph
capture). Re-run `pack` only if the checkpoint revision changes.

## Validation (2026-09-16, this deployment)

| Check | Result |
|---|---|
| Decode C1/C2/C3/C4 aggregate (greedy) | 36.5 / 51.0 / 66.4 / 77.0 tok/s |
| Garbage/NaN gate (batches 3/4/4, greedy+sampled) | 0 garbled |
| Needle 32.8K / 136K prompt | exact; 2,305 / 2,033 tok/s prefill |
| 4 × ~128K concurrent (383K prompt tokens) | 4/4 exact, wall 178.8 s, no OOM |
| Tool calling | `get_weather {"city":"Paris","unit":"c"}`, `finish_reason=tool_calls` |
| JSON mode | valid JSON |
| Vision | red 64×64 PNG → "Red" |
| Unit tests | thinking 6/6, max_tokens 7/7, loop_abort 12/12, row_store pass, **encoder parity 15/15** |
| Head memory under load | ≥13.8 GB free; memguard never fired |

Residual images stay flat during serving; the KV pool is 8,000,000 tokens pinned
(13.4 GB/rank) with `MEM_FRACTION_STATIC=0.80`.

## Caveats

- **Checkpoint revision differs from MiaAI's pin.** Ours is HF snapshot
  `dba1be0a40aa45a94ad051997016db3960a90277`; MiaAI pins
  `fb2764a5cf321eaa5070ca8f9e892818f477c16d`. Serving works and the encoder-parity
  test passes against our checkpoint's own `encoding/encoding.py`, but if anything
  looks off, re-pull the pinned revision and re-run `pack`.
- **No `dspark_sps/sts` tables** — DSpark runs its verify-all schedule. Profiling one
  (`python -m sglang.benchmark.dspark_sps_profiler`) should raise concurrency
  throughput; unmeasured here.
- **GB10 slow-state**: a node can latch to a low clock after long idle (see
  `HANDOFF-AGENT-ONBOARDING.md`); if decode halves after an idle period, power-cycle
  the affected node.
- The recipe's default `CONTEXT_LENGTH=1M`, `MAX_TOTAL_TOKENS=8M` and
  `MAX_RUNNING_REQUESTS=8` are optimistic ceilings; test with real agent traffic
  before relying on 1M under concurrency.
