# FLEET RUNBOOK — the 3am document (v1, 2026-10-03)

If something is dead at 3am, this is the order of operations. Everything here
was learned live on 2026-10-02. Verify with the probe BEFORE ssh-ing around.

## First move, always

```
tail -5 /Volumes/M5_4TB/fleet-state/CHANGES.md      # what changed recently
ls -t /Volumes/M5_4TB/fleet-state/snapshots | head -1   # when last seen healthy
.venv/bin/python scripts/fleet_state.py             # fresh truth NOW
```

## The map (what runs where, 2026-10-03)

| Lane | Where | How to check | How to restart |
|---|---|---|---|
| V4.1 TP4 (engine: vision, 1M ctx, hard pages) | dgx2+asus1+dgx3+asus3 | `curl 100.116.221.82:8888/v1/models` | dgx2: `cd ~/DS4.1 && ./start-tp4.sh serve` (12-13 min) |
| Extraction dispatcher + M5 worker | this Mac, launchd `com.harness.extractdispatch` (KeepAlive) | `tail ~/Logs/extractdispatch.log` | launchd auto-restarts; manual: `launchctl kickstart gui/$UID/com.harness.extractdispatch` |
| asus4 worker + canon (Qwen3.8-27B-FP8) | asus4 100.100.116.82 | `pgrep -f extract_worker`; `curl :8900/v1/models` | worker: setsid nohup ~/extract-venv/bin/python ~/extract_worker.py |
| asus2 worker (post-LoRA) | asus2 100.68.133.1 | same via ssh | dispatcher auto-recruits when lora-power-v1 container exits + mem free |
| LoRA training | asus2, docker `lora-power-v1` | `docker logs lora-power-v1` | see deploy/lora-v1/RUNBOOK.md |
| Fleet probe (hourly) | this Mac, launchd `com.harness.fleetstate` | `tail ~/Logs/fleetstate.log` | kickstart same way |
| Protected embedder | dgx1/e10b RECLASSIFIED 2026-10-04: CategoryRank retired — embedder services (:8800/:8804) legacy-retained. On fabric since 2024-10-04 at 10.77.0.7 (took asus4's switch port; asus4 Tailscale-only). **2026-10-07 VERDICT: SERVING-QUALIFIED, TRAINING-INELIGIBLE.** Three userspace freezes under training (Oct 6-7), all configs with `use_liger_kernel: true`; journal ends mid-line, no OOM/Xid/panic — CUDA-context device-hang signature. Kernel 6.17.0-1031-nvid, driver 580.173.02. The proven 1,020-step v1 run and the Oct-7 8B v2 retrain both ran liger-free on asus2. Never train on this box until a controlled liger-free/controlled-liger repro clears it. Also the ONE node that missed the 2026-09-07 fleet CX7 power-drain (was off-fabric then) — optional unplug-drain hygiene at next physical touch. | `curl 100.81.201.24:8800/v1/models` | serving only; `docker start canon-replica` style roles |

## Known failure modes (all happened)

1. **Node wedge** (ping OK, sshd no banner, tailscale offline): kernel alive,
   userspace dead. Only fix = physical power-cycle. Recognized 2026-10-02 on
   asus4. Do not waste 30 min like we did.
2. **Watchdog resurrection**: retiring a lane means stopping its TIMERS, not
   just containers (`systemctl list-timers` on the node). The qwen38
   watchdog resurrected a dead lane 40 min after teardown on 2026-10-02.
3. **Ghost containers**: undocumented containers squat memory (qwen72 TP4:
   15GB/box; qwen3-coder-next on e10b: 69GB). The probe now watches all 7
   nodes; a container nobody claims in mail = ask, then drop with Sam's OK.
4. **launchd exit 78**: log paths on /Volumes break pre-exec. Logs live in
   ~/Logs (see plists).
5. **Canon/coding confusion**: the adjudicator is DENSE-only (no MoE), and
   V4.1 never judges V4.1 (proposer != adjudicator, M4 contract).

## Protocol rules (in force)

- Scheduled teardown: mail consumer boxes 1h BEFORE (cursor-cr, m4opencode,
  m5-opencode). The probe only reports after the fact.
- Any fleet change by any agent: expect the probe's change-mail; reply with
   why. No silent swaps — that was the whole point.
- Retiring a lane: enumerate timers/watchdogs first.
- Nothing off the pillar list gets extracted. Sam is the only decider.

## Escalation

Substrate dead = factory still fills vault/landing; no data lost, backlog grows.
V4.1 dead = vision + hard pages stall; text tiers continue.
Canon dead = adjudication queues (P1 holds accumulate); extraction continues.
Nothing in this architecture stops anything else — that is the design.

## Network (verified 2026-10-03)

- Fabric: **200GbE switched, MTU 9000** — dgx1 joined 2026-10-04 at 10.77.0.7 (took asus4's switch port; asus4 runs Tailscale-only now). P2P DAC links between Sparks DO NOT WORK (firmware expects switch) — always go through the switch. Node-to-node
  transfers are effectively free (17GB weights in seconds).
- RULE: large artifacts (model weights, docker images) ride node-to-node over
  the fabric. Never relay through the M5 (Tailscale/utun is the fleet's slowest
  link; fine for PDF spooling, wrong for gigabytes).
