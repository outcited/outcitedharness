# Handoff — Qwen3.8-Flash-Next speed tuning on asus2/asus4 (2026-09-12)

For the next agent. Sam authorized full control of the Qwen lane ("do what you
need to with qwen — I want speed and a bigger context"). Flash/spark/training
lanes were untouched and are healthy.

## TL;DR

- **Pinned recommendation (config D): native 262K ctx, bf16 KV, decode CUDA
  graphs ON, flashinfer, mem 0.80** → **2x decode speed** with byte-identical
  outputs and 60/60 needle retrieval. 66.8 tok/s single-stream (counting),
  35.9 prose, **125 tok/s 2-stream aggregate** (vs GLM-Flash 4-node: ~parity
  single-stream, 2.5x on 2-stream, on half the hardware).
- **1M-context rung FAILED**: boots fine (pool 1.24M tokens) but the deep
  prefill gate (~768K–1M actual context) **wedged BOTH nodes at userspace
  level**. Root cause confirmed in both previous-boot journals: NVIDIA
  driver memory exhaustion — `NVRM: NV_ERR_NO_MEMORY ... _memdescAllocInternal`
  from 02:51:14 (asus2) / 02:51:29 (asus4). Kernel kept pinging; sshd,
  Tailscale, HTTP all starved. Both nodes needed a physical power cycle
  (GX10s auto-power-on when replugged). Suspect mechanism: deep prefill's
  workspace + radix cache growth at 1M-YaRN exceeds the 0.86-fraction
  budget mid-request; the author's freeze precedent (300K history with
  bigger chunks) was the same class of failure.
- **RECOVERED 06:15**: pinned config D re-verified serving on both nodes
  (262K native, bf16 KV, decode graphs captured — see
  `~/qwen-tune-evidence/20260912/boot-recovery3-final.log`), pool 840,128
  tokens, canary clean, watchdog re-armed and active.
- NVFP4 KV and fp8-KV+graphs are **dead ends on the upstream nightly**
  (`db272201`): see blocker list.

## What was running before this session (baseline, 09-09 nightly boot)

asus2 `qwen38-flash-next-head` + asus4 `qwen38-flash-next-worker`, TP2,
SGLang `0.0.0.dev1+gdb272201a`, image
`lmsysorg/sglang:nightly-dev-cu13-20260909-db272201`
(digest `sha256:e11ff021…4a1c6`), weights
`RadixArk/Qwen3.8-Flash-Next-NVFP4` @ `7b719225`. 262144 ctx, bf16 KV
(pool 774,848 tok), NEXTN spec 3/1/4, graphs OFF, chunk 1024, radix ON,
flashinfer, mem 0.80. Endpoint `http://100.68.133.1:8888/v1` (key at
`~/.secrets/qwen38-key`), Sam's default opencode model on both Macs.
Watchdog `qwen38-watchdog.timer` active.

Measured baseline that night (idle, from asus2 localhost):
prose decode 20.5 tok/s, counting 33.4, 64K-ctx counting 55.1,
cold prefill 2258 tok/s (64K), 2-stream aggregate 60.4.

## The A/B ladder (all evidence in `~/qwen-tune-evidence/20260912/` on asus2)

| # | config | result |
|---|---|---|
| 1 | flashinfer + NVFP4 KV + graphs | boot assert: KV4 MHA needs triton/torch_native/flex/trtllm_mha (upstream guard) |
| 2 | triton + NVFP4 KV + graphs | graph capture dies: Triton `KeyError: 'float4_e2m1fn_x2'` (packed FP4 dtype unsupported in binder) |
| 3 | flashinfer + fp8 KV + graphs | graph capture dies: merged SM121 QSA kernel asserts **BF16-only**, TP2 12Q/1KV, bs≤128, sel KV≤2055 |
| 4 | **flashinfer + bf16 KV + graphs ON** | **BOOT + PASS** (pool 785,216 tok, 21.8 GB free post-capture) |
| 5 | same + YaRN 1M + mem 0.86 | boots (pool 1,239,488 tok) but deep-prefill needle gate **wedged the node** |

### Config D vs baseline (identical bench, `~/qwen38-sglang-recipe` scripts in
`/tmp/qwen_bench.py`, results `/tmp/qwen_needle_results.jsonl` on asus2 —
may be lost to tmpfs on power cycle; copies of scripts on m5max-ai `/tmp/`)

- short decode (counting): 33.4 → **66.8 tok/s** (2.0x)
- prose decode: 20.5 → **35.9 tok/s** (1.75x)
- 2-stream aggregate: 60.4 → **125.0** (63.5/stream, 2.07x)
- TTFT: 0.32–0.43 s → 0.16–0.18 s
- prefill cold 64K: 2258 → 2075 (noise)
- canaries: byte-identical outputs vs baseline (graphs corruption check)
- needle gate 32K/64K/128K/256K targets (actual 24K/49K/98K/195K prompt
  tokens), early/mid/late + code needles, 3 trials: **60/60 = 100%**
- real repo task (~175K prompt tokens of actual Harnessv1 source, 4 scored
  questions, ground truth computed by importing the real code): cross-file
  retrieval 3/3 both runs; patch generation 1/2 (right table/convention both
  times, one malformed hunk). Scripts + artifacts on m5max-ai
  `/tmp/qwen_repo_task.py`, `/tmp/qwen_repo_task_out/`.

## Post-power-cycle recovery (COMPLETED 2026-09-12 ~06:15)

Both nodes were power-cycled by Sam (asus2 ~05:00, asus4 ~06:06 — both
GX10s auto-powered on re-plug). Note: **asus4 also wedged** in the same
02:51 event; its userspace was just as dead. Recovery executed:

```sh
# (done) stop watchdog first — it auto-starts on boot and would fight the load
sudo -n systemctl stop qwen38-watchdog.timer
# (done) crash evidence pulled from both boxes' PREVIOUS-boot journals:
journalctl -b -1 -k | grep -iE "NV_ERR_NO_MEMORY"   # both show the 02:51 storm
# (done) the 1M gate's partial JSONL in /tmp was lost (tmpfs wipe) — the
#         surviving eval evidence is in ~/qwen-tune-evidence/ on asus2
# (done) revert .env to config D and boot (~9 min cold boot):
sed -i 's/^CONTEXT_LENGTH=1048576/CONTEXT_LENGTH=262144/; s/^MEM_FRACTION_STATIC=0.86/MEM_FRACTION_STATIC=0.80/' ~/qwen38-sglang-recipe/.env
# (done) verified: 262144/bf16/flashinfer, pool 840,128 tok, graphs captured, canary '46'
# (done) watchdog re-armed: sudo systemctl start qwen38-watchdog.timer
```

Recovery lessons: start.sh's worker-ssh preflight failed twice while asus4
was still wedged (a ping-only health check is NOT enough — test the ssh
banner before calling a node alive); and re-arming the watchdog before the
server is confirmed up risks a restart fight against a slow boot.

## Blocked on this nightly (do not retry blindly)

1. **NVFP4 KV**: blocked by #1 and #2 above. Only ever worked on the recipe
   author's private patch image (fp8-upcast QSA varlen kernel). License note:
   author's champion (NVFP4 KV) measured 64 tok/s single-stream, but their
   image carries the pre-#37052 codebase the site moved off of on 09-09.
2. **fp8 KV + graphs**: blocked by the bf16-only SM121 QSA kernel guard (#3).
   fp8 KV + graphs OFF might work but loses the 2x lever — not worth it.
3. **1M YaRN deep prefill**: wedge. If retried, do it disciplined: mem 0.84,
   MAX_RUNNING_REQUESTS=1, `/flush_cache` between lengths (deliberately —
   the 09-10 incident was an accidental flush), one length at a time, watch
   `/metrics` live, kill switch ready. A 512K middle rung is the sane next
   experiment. Expect prefill to degrade with depth (chunk 1024 is forced
   above 262K ctx by the launcher).

## Open items (not done, in rough priority)

- 512K middle-rung experiment (if Sam still wants >262K) — discipline
  listed in the "Blocked" section above; root cause (driver OOM at deep
  prefill) applies to any rung past the pool's comfortable margin.
- Speculative steps 4/1/5 test at 262K (accept-len was 3.4–4.0/4 on gate
  tasks; a longer chain may add a few % — one boot to test).
- Chunked prefill 4096 at native 262K (prefill ~2x; freeze precedent was
  only at >262K ctx; risky, one boot to test).
- **Rotate the HF token on asus2/asus4** (`~/.secrets/hf-token`) — pasted in
  chat on 09-07, still unrotated.
- Flash lane: MTP-off A/B still untested (from the 09-11 speed discussion).
- `scripts/qwen38_sglang.env` in this repo was refreshed to the pinned
  config snapshot — keep it in sync with the live `.env` from now on.

## Hard rules (unchanged)

- Flash lane (DGX2+ASUS1+DGX3+ASUS3), spark embedder/coder, training pool:
  do not touch.
- No commit/push without Sam's say. This doc is untracked.
- Do not send test inference while Sam is actively using a lane.
