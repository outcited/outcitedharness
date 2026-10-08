# GLM-5.3-Flash TP4 (four Sparks)

This is **Flash** (320B / 18B-active, multimodal), not the 743B flagship.

## Recipe

[tonyd2wild/GLM-5.3-Flash-NVFP4-1M-KV-4x-DGX-Spark](https://github.com/tonyd2wild/GLM-5.3-Flash-NVFP4-1M-KV-4x-DGX-Spark)
at `8fd2fcd27c04`. That is the switched-fabric 4× GB10 recipe. His
`docs/FIELD-NOTES-4NODE-100G.md` is the 100G-switch note; our CRS812 is
200G. Decode there matched 200G, so the fabric is not the limiter.

**Checkpoint:** `RedHatAI/GLM-5.3-Flash-NVFP4` @ `240131d6`.
`compressed-tensors`, 10 shards + native MTP file. Do **not** use
LibertAI / ModelOpt NVFP4 — those emit corrupted token IDs (vLLM #54150).

**Image:** `ghcr.io/tonyd2wild/vllm-glm53-flash:sm121-v11-dflash2`

Tony’s default launcher (`launch-glm53-tp4-24g.sh`) is 1M context, 24 GiB
KV/rank, DFlash2 k=7, `FULL_AND_PIECEWISE` graphs. We copy the speed
knobs that apply to this lane, and since 2026-09-12 the drafter too:

- DFlash2 is NC-licensed (CC BY-NC-ND 4.0, `incoai/GLM-5.3-Flash-DFlash2`).
  Enabled with Sam's explicit authorization for private non-commercial use;
  his commercial deliverables run on the public model, not this cluster.
  Rollback is `GLM53F_SPEC_MODE=mtp` in `site.env`.
- Speed profile: `FULL_AND_PIECEWISE` + `--max-num-batched-tokens 16384`.
  Keep `--max-num-seqs 6` for 1–2 OpenCode sessions; 64 is aggregate-only.
- 1M / 24 GiB KV is live. `--memory 112g` assumes sticker 128; ours are
  ~119–122 visible, so no cgroup memory cap.
- NCCL: our HCAs are `rocep1s0f1` / `10.77.0.0/23` / `enp1s0f1np1`, not
  his `rocep1s0f0` / `192.168.192.0/24`. Look up the RoCEv2 GID at launch.
- Required overlay: `docker/sparse_attn_indexer_kpool_sm121.py` or every
  decode past ~24K dies.
- Vision template `chat_template_mm.jinja` must sit inside the weights
  dir or image requests 500.
- Unconditional page-cache flusher during boot. Threshold flushers lie
  on UMA.

## Boxes

| rank | host | fabric |
|---|---|---|
| 0 | dgx2 (`spark-49af`) | 10.77.0.1 |
| 1 | asus1 (`gx10-fc2e`) | 10.77.0.2 |
| 2 | dgx3 (`spark-69c8`) | 10.77.0.3 |
| 3 | asus3 (`gx10-0309`) | 10.77.0.4 |

Weights live on DGX2 at `/srv/models/GLM-5.3-Flash-NVFP4-redhat`.
ASUS2/ASUS4 Qwen is untouched.

## Incident 2026-09-15: asus1 hard power-off

Rank 1 (asus1, `gx10-fc2e`) hard-reset mid-serve at ~04:26 UTC — journal
stopped clean, no panic/OOM/thermal/Xid, pstore empty, `last -x` shows no
shutdown. DGX2's engine died 6 min later on a broadcast `TimeoutError`
(`EngineDeadError`); dgx3/asus3 NCCL heartbeats confirmed the dead rank.
Logs archived with checksums at
`srv/models/archives/glm53-flash-cutover/incident-20260915-asus1-poweroff/`.

This is the published GB10/GX10 signature (identical in tonyd2wild/
DGX-Spark-Hard-Poweroff-Fix, NVIDIA forum #359785, NVIDIA/open-gpu-kernel-
modules#1358, vllm#56824/#55569): EC hard power cut under sustained load,
and a unified-memory over-commit fault (`NV_ERR_NO_MEMORY` at
`_memdescAllocInternal` — asus1 had those scars from Sep 11/13) that
silently wedges the host. GB10 has no BMC/SEL; it is unloggable from
inside. asus1 ran pre-production-adjacent SBIOS `GX10DGX.0105` (2026-05)
while stable fleet units elsewhere report production `0104`; a combined
SBIOS/EC/PD capsule + AC cold-drain is the durable fix (brick-capable;
not yet applied).

Hardening live on asus1 since this boot:
- `gb10-clock-cap.service` — `nvidia-smi -lgc 300,2200`, systemd-persistent.
  Verified under load at 2190 MHz. ~5% decode cost per field reports.
- 2 s telemetry trap: `~/glm53-flash-tp4-hardening/gb10-telemetry.sh` →
  `gb10-telemetry.log`. Last line after a crash shows temp-ramp (thermal
  EC cut) vs MemAvailable crater (over-commit).
- efi_pstore loaded; next kernel panic finally leaves a body.
- kdump needs `crashkernel=` in `/etc/default/grub` + `update-grub` +
  reboot — deferred; serial console (`ttyS0`, 921600) already carries
  panic output.
- `orchestrate.sh` now runs a 15 s rank-liveness watchdog after health:
  a silently power-cycled rank is detected in ≤15 s (was 6 min), logs are
  archived, and survivors are stopped/rolled back. Unit-tested against
  simulated dead/unreachable ranks (T1–T3 pass).

## Do not launch yet

Download and pin only. Drain vision on asus1/dgx3/asus3 only when the
weights verify and we are ready to boot.
