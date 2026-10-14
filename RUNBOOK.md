# FLEET RUNBOOK — the 3am document (v2, 2026-10-09)

If something is dead at 3am, this is the order of operations. Everything here
was learned live on 2026-10-02. Verify with the probe BEFORE ssh-ing around.

**v2 (2026-10-09, Sam's architecture):** TP4 = 4 boxes (dgx2/asus1/dgx3/asus3)
· **asus2 = trainer 1** (running the first real v2 student run) ·
**e10b = trainer 2, pending liger-free qualification** ·
**asus4 = embedder + GCI + canon primary**. The embedder+GCI pair moved off
e10b to asus4 this day, bit-identity-verified before cutover; e10b's copy is
stopped+disabled and kept as rollback. v1.1's observability fixes still apply:
the probe was reporting three falsehoods — asus4 as `SSH_FAIL` for four days
(stale fabric relay target), the embedder as `[]` (it 404s on `/v1/models`),
and GCI not at all.

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
| asus4 worker + canon (Qwen3.8-27B-FP8) | asus4 100.100.116.82 — **Tailscale-only, no fabric route** (gave its switch port to dgx1 on 2026-10-04; `10.77.0.6` is dead, do not relay through it) | `pgrep -f extract_worker`; `curl 100.100.116.82:8900/v1/models` | worker: setsid nohup ~/extract-venv/bin/python ~/extract_worker.py · canon: `docker start canon-qwen38-27b` (restart=unless-stopped, self-heals) |
| asus2 worker (post-LoRA) | asus2 100.68.133.1 | same via ssh | dispatcher auto-recruits when lora-power-v1 container exits + mem free |
| Student v2 training (**LIVE** since 2026-10-09) | asus2, docker `power-tables-v2-train` on image `harness/llamafactory-qwen3-next:20260901`, output `~/harness-training/checkpoints/power-tables-qwen3-vl-8b-v2/` (645 steps, ~3.1 min/step ⇒ ~33h) | **`tail -1 ~/harness-training/checkpoints/power-tables-qwen3-vl-8b-v2/trainer_log.jsonl`** on asus2 — never `docker logs` (failure mode 10). Automated: launchd `com.harness.v2watch` (15 min) → `scripts/watch_v2_run.py`, mails only on state transition (OK/STALL/EXITED/DISK/UNREACHABLE), log `~/Logs/v2watch.log`. At 2026-10-10T03:05Z: step 150/645 (23.26%), loss 0.0196, ETA ~1d3h, 267G free, 0 checkpoints written yet | `docker start power-tables-v2-train`; the retired LoRA lane (`lora-power-v1`, exited 2026-10-06) is in deploy/lora-v1/RUNBOOK.md |
| Fleet probe (hourly) | this Mac, launchd `com.harness.fleetstate` | `tail ~/Logs/fleetstate.log` | kickstart same way |
| Soak vitals (hourly) | this Mac, `scripts/soak_sample.py` → `/Volumes/M5_4TB/extract-results/soak.csv` | `tail -1` the CSV; the `alerts` column | kickstart `com.harness.soak` |
| Production embedder + GCI | **asus4** since 2026-10-09 (moved off e10b so e10b can train). `bge-m3-embed.service` on **100.100.116.82:8800** (systemd, `Restart=always`, enabled) — same FAE **v4** weights under the legacy label `bge-m3-cr-tapes-v1`, path `~/embedder/weights/bge-m3-fae-search-v4/checkpoints_20260822T045420Z` (no symlink indirection — /healthz shows the real path now). `harness-gci.service` on **100.100.116.82:8810**, same token (clients unchanged), index at `~/harness-gci-data/code-intel.sqlite`, release symlink `~/harness-gci/app -> releases/20260828T184119Z`. **Cutover gate passed**: verify_equivalence 8/8 bit-identical vs e10b before repointing. Rollback = e10b's pair (stopped+disabled, assets on disk: `/data/categoryrank/cr-finetune`, `/data/harness-gci`, `/home/samkim/harness-gci`) + repoint 4 config values. | embedder: `curl 100.100.116.82:8800/healthz` (**never `/v1/models`** — it 404s). GCI: `curl -o /dev/null -w '%{http_code}' 100.100.116.82:8810/health` → **401 = alive**. GCI binds only its Tailscale address — `127.0.0.1:8810` gives HTTP 000 even when healthy. | `sudo systemctl restart bge-m3-embed` / `harness-gci` on asus4. Both self-heal (`Restart=always`, enabled). |
| Embedder weights backup | NAS `/Volumes/M5_4TB/harness-training/models/` — **all four generations** as of 2026-10-09: `bge-m3-fae-search-v4` (live, 15G), `bge-m3-cr-tapes-v2` (15G), `bge-m3-fae-search-v3` (15G), `bge-m3-fae-search-v31` (5.7G), plus the pre-existing April `bge-m3-cr-tapes-v1` (21G). Manifest + restore steps: `BACKUP-bge-m3-fae-search-v4.md` beside them | `du -sh` each dir; verification was file-count + sha256 + `rsync -an` clean | re-rsync from e10b; the NAS is Mac-attached, so this one transfer legitimately rides the M5 |
| Embedder standby (cold) | asus2 `~/embedder-rehearsal/` — verified weight copy (15G) + venv + `bge_m3_service.py`. **Not running, no cron.** Recipe + scripts: `deploy/embedder-standby/` | `./start-standby.sh 8801` (binds 127.0.0.1 only, refuses :8800), then `python3 verify_equivalence.py --candidate http://127.0.0.1:8801/v1/embeddings` → must print `VERDICT: IDENTICAL` | proved 2026-10-09: 8/8 vectors **bit-identical** to production, max abs diff 0.000e+00, production undisturbed (uptime kept climbing). A stock bge-m3 clone fails this test — that is the point. |

## Disk pressure and the reclaim plan (RESOLVED 2026-10-09 · re-measured 2026-10-10T02:50Z)

**Not a fire any more.** Fresh `df` on both boxes: **asus2 602G/916G, 267G free
(70%)**, **asus4 659G/916G, 211G free (76%)**; e10b 1.2T/3.7T. The 535G reclaim
below was executed 2026-10-09 ~04:20Z on Sam's approval. The `44G/56G free`
figures that used to head this section were the *pre*-reclaim numbers and were
still being quoted as current six hours after the reclaim — do not re-alarm on
them; read the EXECUTED table first. Original cause: duplication, asus2 and
asus4 held the same weights twice.

**Cold storage available on asus2 (2026-10-10):** a 1.8T Samsung PSSD T7 is
attached as `/dev/sda1`, **exfat, and NOT mounted** — 1.8T of headroom sitting
there doing nothing. exfat carries no POSIX ownership/permissions and no
symlinks, so it is a *cold backup* target for weights (like the NAS is), never a
training path or a HF-cache path. `mount -t exfat /dev/sda1 /mnt/pssd` (or add a
systemd mount unit) before use; verify with a sha256 spot-check after any copy.

**Duplicated on both (asus2 copy is the reclaim candidate, ~567G):**

| weights | size | note |
|---|---|---|
| `~/models/DeepSeek-V4-Flash-DSpark` | 159G | superseded by V4.1 TP4 |
| `~/models/RadixArk` | 126G | |
| `~/harness-training/models/Qwen3-VL-30B-A3B-Instruct-BF16` | 63G | |
| `~/harness-training/models/Qwen3-VL-30B-PinGate-V1-Merged` | 58G | |
| `~/harness-training/models/Qwen3-VL-30B-PinGate-SFT-457-Merged` | 58G | |
| `~/models/Qwen3-Coder-Next-NVFP4-GB10` | 43G | |
| `~/harness-training/models/Qwen3-VL-30B-A3B-Instruct-FP8` | 31G | |
| `~/harness-training/models/Qwen3.8-27B-FP8` | 29G | **asus4's copy is live** (canon bind-mounts it) — delete only the asus2 one |

**Unique to asus2 — do not delete:** `MiniMax-M2.5-REAP-139B-A10B-NVFP4-GB10`
(75G, only copy in the fleet), `power-tables-student-v1` + `-v2` (34G, the
trained student), `Qwen3-VL-8B-Instruct` (17G), `harness-training/datasets`
(6.6G) and `checkpoints` (4.1G).
**Unique to asus4 — do not delete:** `Qwen3-VL-30B-PinGate-SFTOnly-Merged` (58G).

Other reclaim on asus2: the vanilla-bge-m3 impostor `~/embedder` venv (5.7G) +
`~/.cache/huggingface` (4.6G) = 10.3G, and docker's
`harness/llamafactory-qwen3-next:20260901` image (24.5G, rebuildable from
`deploy/training/Dockerfile.llamafactory-qwen3-next`, currently referenced only
by the exited `lora-2node-r0`). asus4's `extract-jobs/err/` holds 3,066 files.

**Rule: verify before any delete.** Equal `du` is not proof — compare by
checksum.

**VERIFIED 2026-10-09 (~04:05Z).** `sha256sum` over every file in all eight
duplicated trees on both boxes: **1,927 files each, zero differences outside
`.cache/huggingface`** (three HF download-bookkeeping artifacts: two
`download/*=<hash>` metadata files and one `.safetensors.lock`). No model file
differs. Evidence kept at
`/Volumes/M5_4TB/fleet-state/disk-reclaim-20261009/`
(`dup-manifest-asus2.txt`, `dup-manifest-asus4.txt`, `dupdiff.txt`).

So the ~567G of asus2 duplicates is safe to delete on the evidence — **but it is
still a delete of model weights on a live box, so it needs Sam's explicit
go-ahead.** Note asus4 has no fabric address, so the comparison ran over
Tailscale; do not invent a `10.77.0.6` path. Method used:

```
for d in <the eight dirs>; do find "$d" -type f -print0 | xargs -0 sha256sum; done \
  | sort -k2 > /tmp/dup-manifest-$(hostname).txt      # on each box, nice -n 19
```

**EXECUTED 2026-10-09 ~04:20Z on Sam's approval — 535G reclaimed, split by role
rather than all taken from asus2.** Every tree deleted keeps exactly one
byte-identical survivor on the other box.

| box | deleted | freed | disk after |
|---|---|---|---|
| asus2 | `models/DeepSeek-V4-Flash-DSpark` 159G, `models/RadixArk` 126G, `models/Qwen3-Coder-Next-NVFP4-GB10` 43G, `harness-training/models/Qwen3.8-27B-FP8` 29G | 357G | **94% → 68%** (44G → 283G free) |
| asus4 | `harness-training/models/Qwen3-VL-30B-A3B-Instruct-BF16` 62G, `…PinGate-V1-Merged` 58G, `…PinGate-SFT-457-Merged` 58G | 178G | **94% → 74%** (56G → 233G free) |

Why split: asus2 is the training rig and its own llamafactory configs reference
the Qwen3-VL-30B trees, while asus4 references nothing but its live canon mount.
So the VL-30B weights stay on the box that trains, and the must-have canon box
gets the headroom (a full disk there would take out adjudication entirely).

Left in place on purpose:
- `asus4:…/Qwen3-VL-30B-A3B-Instruct-FP8` (31G) — mode `dr-xr-xr-x`, write-
  protected by someone on 2026-08-31. Deleting it needs `chmod -R u+w` first,
  i.e. defeating a deliberate guard, so it was not touched. Its identical copy
  survives on asus2.
- asus2's `MiniMax-M2.5-REAP-139B-A10B-NVFP4-GB10` (75G), `power-tables-student
  -v1`/`-v2` (34G), `Qwen3-VL-8B-Instruct` (17G), `datasets` (6.6G),
  `checkpoints` (4.1G) — unique, never candidates.
- asus4's `Qwen3.8-27B-FP8` (29G, live canon mount) and
  `Qwen3-VL-30B-PinGate-SFTOnly-Merged` (58G, unique to asus4).
 - `docker` on asus2 holds `harness/llamafactory-qwen3-next:20260901` (24.5G)
   and `lmsysorg/sglang:nightly-dev-cu13` (33.9G). **CORRECTED 2026-10-10: the
   llamafactory image is NOT reclaimable** — it is the image the live
   `power-tables-v2-train` container runs on, so removing it takes away the
   ability to restart the v2 run. The earlier "reclaimable if the LoRA lane
   stays retired" note predates the v2 run reusing that image. Only the sglang
   image (33.9G, no container on asus2 references it — asus4's canon runs the
   *different* tag `…cu13-20260909-db272201`) is a candidate, and only at the
   cost of a re-pull. With 267G free, there is no reason to take it.

Also removed on asus2 at the same time (Sam's approval): the vanilla-bge-m3
impostor — process killed, `@reboot` crontab entry deleted (crontab now empty),
`~/embedder` and `~/.cache/huggingface/hub` removed (10.3G). The 348M
`~/.cache/huggingface/datasets` was kept (training cache, not the impostor's).
Port 8800 on asus2 is now free, so **:8800 anywhere in this fleet means the real
embedder on e10b.** The extraction worker on asus2 was left running.

## Retired 2026-10-09: the e10b liger A/B

**Standing rule is unchanged and now permanent: e10b is serving-only. Never train
on it.** What was retired is the *experiment* that was going to soften that rule.
**Update later on 2026-10-09: Sam ordered the two-trainer architecture, so this
rule is suspended pending the liger-free qualification run — see the map above.**

### Two crash suspects, both real (recorded 2026-10-09 after Sam's report)

1. **Liger — documented.** All three Oct 6-7 freezes ran
   `use_liger_kernel: true`. That correlation is recorded below and stands.
2. **HDMI hotplug — Sam's report, previously unrecorded.** Sam states the box
   crashes when the HDMI plug is pulled (observed directly), and believes HDMI
   may have been associated with the Oct 6-7 crashes. He stated this before; it
   was never written down anywhere in the repo or agent mail until now. The
   retained journal cannot confirm or refute it: the freeze-era logs rotated
   away, and the boots that remain all ended *cleanly* (no Xid, no oops).
   Mechanism is plausible: on GB10 the GPU is the display adapter, and the
   shipped GNOME/GDM session reacts to connector hotplug.

These are confounded, not competing. The qualification run isolates one variable
(liger OFF, nobody touching the desk). A separate idle test isolates the other:
**after the qual run completes, pull HDMI with the box idle and see if it
crashes.** If it does, the fix is a $7 EDID dummy plug + disabling the GUI
(`systemctl set-default multi-user.target`, mask gdm) — both queued for e10b
after the verdict, and worth doing to asus2/asus4 regardless. Until the dummy
plug is in: **do not unplug HDMI from a DGX Spark that is doing anything you
care about.**

History: e10b froze three times under training on Oct 6-7, every config with
`use_liger_kernel: true` — journal ends mid-line, no OOM, no Xid, no panic, i.e.
a CUDA-context device-hang signature. The proven 1,020-step v1 run and the Oct-7
8B retrain both ran liger-free on asus2, so a controlled A/B was announced
(mail `e10b-canon-replica-intentional`, 2026-10-08T02:00Z): identical liger-free
8B config on asus2 (candidate A) and e10b (candidate B), with dgx1's
`canon-replica` stopped for the duration.

It produced nothing. No llamafactory image on e10b, no run or checkpoint dir
newer than Oct 5, zero journal hits for llamafactory/liger/torchrun/deepspeed
since Oct 6; asus2's only candidate container (`lora-2node-r0`) lived 7 seconds
with empty logs. e10b rebooted at 21:13Z on Oct 8, which would have taken any
`/tmp` artifacts with it. The only durable cost was real: canon ran
single-replica for over 24h on the strength of it.

**Why not re-run it.** The risk profile changed after Oct 7. e10b is now the
sole home of the production embedder *and* GCI (both must-have, neither
replicated as a service) *and* the restored canon replica. A training hang there
no longer costs an idle box — it costs embeddings, code intelligence and judge
redundancy at once. Meanwhile asus2 is free, qualified and liger-proven, so
there is no capacity argument for training on e10b either. The question the A/B
was going to answer ("is e10b safe with liger off?") has no consumer.

**CORRECTED 2026-10-10 — that paragraph is now half out of date, and Sam has
approved the qualification run.** The embedder and GCI moved to asus4 on
2026-10-09, so e10b no longer hosts them; a hang there costs **canon redundancy
only**. What still stands is the one real cost, and it is the gate to clear
before starting: e10b holds the second canon replica (`:8901`), so a training run
there means either (a) stopping the replica and running canon single-replica for
the duration — which is precisely the >24h exposure that already happened once —
or (b) standing a replica up elsewhere first. Note asus2's copy of
`Qwen3.8-27B-FP8` was deleted in the 2026-10-09 reclaim, so (b) means moving 29G
of weights before anything starts. Pick (a) or (b) explicitly, in mail, with an
end condition — do not let it default to (a) by silence.

If it is ever revived, the minimum bar: liger-free 8B LoRA, checkpoints and logs
to a **persistent** path (never `/tmp` — that is how the evidence vanished), a
`systemctl list-timers` sweep first (failure mode 2), the embedder `/healthz`
and GCI `:8810` watched throughout, and canon redundancy explicitly accepted or
restored elsewhere *before* the run starts. Announce it in mail with an end
condition, so a stalled experiment is visible instead of leaving a lane down
indefinitely.

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
6. **False SSH_FAIL from a stale probe target**: asus4 reported `SSH_FAIL` for
   four days (Oct 4 → Oct 9) because the probe relayed through `10.77.0.6`, a
   fabric address that stopped existing when dgx1 took that switch port. The box
   was up and serving canon the entire time. Rule: when a node's cabling or
   address class changes, the probe target changes in the same commit.
7. **A probe that scores a 404 as healthy**: the embedder answered
   `{"detail":"Not Found"}` on `/v1/models`; that parsed as JSON with no `data`,
   so the probe recorded `[]`. "Healthy" and "route gone" were the same string.
   Probes now record `HTTP_<code>` and never an empty list.
8. **Port-number shadowing**: asus2 ran a vanilla `BAAI/bge-m3` (HF download,
   *not* our fine-tune) on :8800 with an `@reboot` cron — same port, same 1024
   dims, different vectors. No config pointed at it, but it was one `base_url`
   edit from silently invalidating every GCI vector. The `/healthz` probe tells
   them apart: the impostor 404s, the production embedder answers with its
   identity. **Removed 2026-10-09 with Sam's approval** (process killed, cron
   entry deleted, `~/embedder` + HF hub cache removed), so :8800 now means only
   the e10b embedder.
9. **A vitals sampler that records but never alerts**: `soak_sample.py` wrote
   `canon_up=0` into soak.csv and stayed silent, because only `v41_tp4` could
   raise an alert. That is how canon ran as a single replica for 24h with the
   evidence sitting in a CSV nobody reads. Fixed 2026-10-09: `canon_down` and
   `canon_replica_down` (dgx1:8901) now alert; the student lane is probed on
   dgx1 where it actually serves, and is deliberately alert-free while dark.
   **Schema rule:** soak.csv has carried the same 12-column header since 2026-10,
   so new signals go into the free-text `alerts` column (see `EXTRA_ALERTS`),
   never into a new column — a new column misaligns every historical row.
   10 tests in `tests/test_soak_sample.py` hold both rules.
10. **docker logs lie about training progress**: python's stdout is
   block-buffered without a TTY, so `docker logs` shows the trainer banner and
   then *silence* while training proceeds normally. Cost us a healthy 80-minute
   run on 2026-10-09 (killed on a wrong diagnosis) plus three relaunches. The
   truth is on disk: `<output_dir>/trainer_log.jsonl` gets a JSON line per
   `logging_steps` — monitor that, never docker logs. Related: launch with the
   fleet's proven recipe (see `deploy/training/` and
   `~/harness-training/scripts/training_launch_*`): ENTRYPOINT `llamafactory-cli`
   + args `train <yaml>` + `FORCE_TORCHRUN=1`, `--ipc host`, `--network host`,
   `--ulimit memlock=-1`, `--ulimit stack=67108864`,
   `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True`. Plain
   `torchrun --entrypoint /bin/bash` variants exited 0 silently twice — do not
   rediscover this.

## Single points of failure (verified 2026-10-09)

- **Canon/judge = two replicas again (restored 2026-10-09 ~04:31Z on Sam's call).**
  `asus4:8900` (primary; the endpoint hard-coded in `harness/pipeline/api.py:22`,
  `scripts/sentinel.py:26`, `scripts/canon_gate.py:20`, `scripts/soak_sample.py:21`)
  and `dgx1:8901` (`canon-replica`, `qwen3.8-27b-fp8`, weights at
  `~/harness-training/models/Qwen3.8-27B-FP8`). Boot takes **~7 min** (weight load
  dominates; `docker start` returns immediately, so poll `:8901/v1/models` — at
  200 s it was still HTTP 000 and healthy). Verified after start: `/v1/models` 200,
  one chat completion returned `OK` with thinking off, mem avail 106G → 50G, and
  the protected services were undisturbed (embedder `ready=true`, same 26,296 s
  uptime — it never restarted; GCI 401 = alive).
  History worth keeping: the replica had been stopped deliberately at
  2026-10-07T23:02Z by m5-opencode for a liger A/B (mail
  `e10b-canon-replica-intentional`, "restores on completion or on a freeze
  verdict"), but **no trace of that A/B exists on either box** — no llamafactory
  image on e10b, no run/checkpoint dir newer than Oct 5, zero journal hits for
  llamafactory/liger/torchrun/deepspeed since Oct 6, and asus2's only candidate
  container lived 7 s with empty logs. The restore condition was therefore
  unevaluable while canon sat single-replica for >24h. Sam ruled: restore.
  Both replicas now self-heal (`restart=unless-stopped`); the manual-stop flag on
  dgx1 is cleared, so it will come back on its own after a reboot.
- **Production embedder = e10b alone**, and GCI is welded to it:
  `harness/gci/encoder.py:28` refuses any encoder URL that is not exactly
  `http://127.0.0.1:8800/v1/embeddings`. Relocating the embedder means
  relocating GCI with it.
- **Embedder weights**: all four generations (v1/v2/v3/v31/v4) are backed up to
  the NAS and verified as of 2026-10-09 — see the backup row above. The
  *service* is still single-copy on e10b, and GCI cannot run anywhere else
  without changing `harness/gci/encoder.py`.

## Protocol rules (in force)

- Scheduled teardown: mail consumer boxes 1h BEFORE (cursor-cr, m4opencode,
  m5-opencode). The probe only reports after the fact.
- Any fleet change by any agent: expect the probe's change-mail; reply with
   why. No silent swaps — that was the whole point.
- Retiring a lane: enumerate timers/watchdogs first.
- Nothing off the pillar list gets extracted. Sam is the only decider.
- Embedder ruling (Sam, 2026-10-09, C6): ONE embedder fleet-wide — the
  production unit (asus4 :8800 primary post-cutover, FAE v4 weights behind
  the legacy bge-m3-cr-tapes-v1 label; e10b replica per fleet table).
  m5-opencode consumes read-only as a throttled batch client (≤4
  concurrent), pins every stored vector to the served-weight fingerprint,
  never mixes weight generations, never stands up a second embedder (the
  asus2 vanilla bge-m3 impostor of 2026-10-08 is the cautionary tale —
  same port, same dims, different vector space). Coordination mail:
  embedder-coordination-e10b-batch-client-20261009 → cursor-tapes.
- Mail: canonical inbox = NAS \\charleybasecamp.local (192.168.4.54)
  Electronicdataassets/agent-inbox, mounted at ~/Volumes/Electronicdataassets
  (harness SMB account, creds in Harnessv1/.env, launchd com.harness.nasmount
  remounts at login). M5_4TB agent-inbox tree is FROZEN — send with
  `--shared-root=/Users/samkim/Volumes/Electronicdataassets`. Sender cut
  over + 11 stale queued mails drained 2026-10-09.
  **VERIFIED / MADE TRUE 2026-10-10T02:51Z** (the bullet above described intent;
  three parts of it were not yet real):
  - the `harness` SMB account did not exist until 2026-10-09 ~18:52Z. Created on
    the NAS with `synouser --add`, then a DSM Read/Write ACE
    (`user:harness:allow:rwxpdDaARWc--:fd--`) on `/volume1/Electronicdataassets`,
    confirmed inherited into `agent-inbox/`. Verified by mounting and
    write+delete in `agent-inbox/` and the share root. SMB-only by design:
    shell `/sbin/nologin`, member of `users` only, NOT administrators — so
    rsync/SSH backups still ride `Charley` + `~/.nas_askpass`.
  - the mount itself was down: `~/Volumes/Electronicdataassets` was an empty
    directory, so anything using `--shared-root` was writing into a fake local
    tree. `launchctl kickstart gui/$UID/com.harness.nasmount` brings it up.
    **RULE: `mount | grep Electronicdataassets` before any send** — same trap as
    the retired `/Volumes/shared` incident of 2026-06-18. Note this box also
    carries `com.mfg.nas-mount` (osascript, mounts as `charley` at
    `/Volumes/Electronicdataassets`) for the mfg lane; the harness job no-ops if
    anything named Electronicdataassets is already mounted.
  - `scripts/fleet_state.py` still had `INBOX = /Volumes/M5_4TB/agent-inbox` and
    wrote change-mails into the frozen tree nine times on 2026-10-08/09 (last
    20:24Z). It now resolves the NAS inbox (`AGENT_INBOX` overrides; if the share
    is unmounted it spools to `~/Logs/fleet-state-mail-spool` and says so) and
    never falls back to the frozen path. 4 tests added (32 in
    `tests/test_fleet_state.py`), and every send now appends its INDEX.jsonl row.
  - **62 more stranded mails drained** (the "11" above was a partial drain):
    `scripts/drain_frozen_inbox.py` — dry run by default, `--apply` to write,
    idempotent, copies rather than moves so the frozen tree stays as history,
    INDEX rows tagged `manual-drain-20261010`. The stranded set included the
    cursor-cr migration decision mail itself, `canon-restored-and-535g-reclaimed`,
    `architecture-flip-two-trainers` and four cr-core mails addressed to us that
    nobody had seen. Re-run it after any sender is found still on the old path.
- m5 API ports (registered, do not collide): :8819 parts API,
  :8792 rows API (extraction-v1.1, 0.0.0.0). NOT mine: :8791
  search-evidence-index (facet workstream), :8790 recon-ruvector,
  :8793 curve-08b decision API (cr-core contract, localhost on their
  start command), :8900/:8901 canon replicas, :8800 embedder, :8810 GCI.

## Escalation

Substrate dead = factory still fills vault/landing; no data lost, backlog grows.
V4.1 dead = vision + hard pages stall; text tiers continue.
Canon dead = adjudication queues (P1 holds accumulate); extraction continues.
  **CORRECTED 2026-10-10:** this line used to say "there is no second replica
  right now — this is a real stop", which contradicted the SPOF row above it
  (replica restored on Sam's call 2026-10-09 ~04:31Z). Current truth: canon is
  two replicas — `asus4:8900` (primary, the endpoint hard-coded in four scripts)
  and `dgx1:8901` (`canon-replica`, `restart=unless-stopped`, self-heals).
  So canon-primary dead = repoint callers to `:8901` (boot ~7 min, poll
  `/v1/models`; `docker start` returns before it serves). Both dead = a real
  stop for adjudication; extraction continues.
Embedder or GCI dead = degradation only, not a stop: GCI's 300 s refresh fails,
  `orch.py` catches it and logs (`GCI workspace-bound semantic expansion failed`),
  semantic expansion and global discovery context go stale, extraction and
  adjudication continue. Batch embedding scripts fail loudly instead.
Nothing in this architecture stops anything else — that is the design.

## Network (verified 2026-10-03)

## Storage topology (verified 2026-10-10, post-migration)

- `/Volumes/M5_4TB` is a SYMLINK → `~/Volumes/Electronicdataassets/m5-4tb`
  (charleybasecamp NAS mirror). The physical external was detached after
  the copy. Every hardcoded M5_4TB path in every script/service resolves
  transparently through SMB. Expect: sequential ~95MB/s, small files
  ~200ms/file (SMB overhead) — small-file forests (staging/, node_modules)
  are the pain points; big blob reads are fine.
- yosemiteraid5.local = NVMe RAID5 fast tier, mounted at
  `~/mnt/yosemiteraid5/{Public,Home}` via `tapes` account, 7.2Ti free.
  m5 working dir: `Public/m5-opencode/`. Hot data (training bundles,
  burn working sets) belongs here; charleybasecamp (35Ti) is the bulk.
- Mailer gotcha: run send-message.ts from a LOCAL cwd with global tsx
  (`/opt/homebrew/bin/tsx <path>/send-message.ts --shared-root=...`);
  running with cwd inside the NAS mirror breaks queue-fallback writes
  (EACCES) and the mirror's node_modules/.bin is incomplete (tsx crash).
- If a burn shows mass `missing_pdf` after the swap, suspect the charley
  mirror copy is incomplete for that tree — verify against manifest shas
  before re-fetching anything.

## Network (verified 2026-10-03) — original

- Fabric: **200GbE switched, MTU 9000** — dgx1 joined 2026-10-04 at 10.77.0.7 (took asus4's switch port; asus4 runs Tailscale-only now). P2P DAC links between Sparks DO NOT WORK (firmware expects switch) — always go through the switch. Node-to-node
  transfers are effectively free (17GB weights in seconds).
- RULE: large artifacts (model weights, docker images) ride node-to-node over
  the fabric. Never relay through the M5 (Tailscale/utun is the fleet's slowest
  link; fine for PDF spooling, wrong for gigabytes).
  Exception: the NAS is Mac-attached, so a *backup* to `/Volumes/M5_4TB` has no
  fabric path and must ride the M5. The 15G embedder-weights backup on
  2026-10-09 ran ~84 MB/s that way (3 min) — budget for it, don't avoid it.
- asus4 has **no fabric port** (dgx1 took it on 2026-10-04). Anything that wants
  to reach asus4 goes over Tailscale at `100.100.116.82`. A relay hop through
  asus2 to `10.77.0.6` will always fail with "no route to host".
