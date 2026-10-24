# HANDOFF — 2026-10-21 late session: GOLDEN-PATH-SHIP executed (operator P0)

**Executor:** m5-opencode, acting under Seven (engineering-director) and operator
order DW-GOLDEN-PATH-SHIP-01 (outcitedharness#23). Board receipts: #8 comments
6322972033 / 6323109116 / 6323354555 / 6325445024 / 6325794568 / 6326390815 /
6326656009. FE receipts: designwins-v2#72 comments 6322309504 / 6322964938 /
6323101733 / 6326390212. cr-core: outcited.com#103 comment 6322309558.

## Delivered this session (all receipts on GitHub + git)

1. **FE golden path CLOSED:** designwins-v2 PR #73 squash-merged (`677d6b2e`,
   2026-10-21T20:55Z). Main CI green for the first time since 2026-10-15.
   #69/#71 closed (content carried; lineage in PR). Private authenticated
   preview: **http://100.106.13.24:3118/labs/test-drive** (M5 `next start`,
   LaunchAgent `com.samkim.dw-preview`, worktree `/Users/samkim/designwins-v2-preview`,
   branch=main). Field key: `~/.secrets/dw-preview-field-key` (0600; also LAN
   http://192.168.4.59:3118, localhost on M5). Browser QA 23/23 receipts in
   `docs/audits/golden-path-ship/`. Prod MCU NL journey is DOWN (cr-core 503,
   see below) — page states it honestly.
2. **Wave-2 CLOSED:** operator accepted; expansion was already 15,599/15,599
   (cp-100 receipted, 10,406 independently verified recovered IDs); happy-shard
   determinism re-proof executed + PASSED
   (`lane-out/spine/reader2/wave2-receipts/happy-shard-rerun-determinism.receipt`).
   ExportStore promotion HOLD unchanged.
3. **Admission verified + counter fixed:** drain state 8,397 admitted /
   2,109 held (auto-recheck). `pull_from_mini.sh` mtime-counter bug fixed
   (commit `a7a7bc7a`, live under com.harness.pdf-intake). 11,612 manifestless
   blobs: list staged `lane-out/manifestless-blobs-20261021.jsonl`, manifests
   requested from mac-mini (mail `db38cbdc`).
4. **Feed repairs (Seven's eyes):** sentinel LKG push added to
   `com.harness.sentinel.nightly` (her copy was frozen since 10-19);
   fleet-state relocated behind `FLEET_STATE_ROOT` (NAS tree uid-1026-owned,
   probe had died 10-12), new hourly `com.samkim.fleetstate` + daily heartbeat
   + push to seven. Telemetry emitter clobber bug fixed (`041b2a9a`).
5. **SiC46x:** PR #100 verified (integration-only, no release flip); gates
   re-run ISOLATED on M5 15/15 with harness extractor (`pdftotext -layout`);
   cr-core owes the one-line `-layout` fix + DB-side re-sync acceptance
   (their DB). Mail: `5b0279cd`, `0ce29cf3`.
6. **M4 window collapsed** (operator): PR #73 canon. M4 post-merge hygiene
   (worktrees, wrong tracking branch) remains theirs, optional.

## Dispatched, awaiting attributable replies (nothing gated on them)

- **cr-core:** recommendation 503 (prod-affecting: `facade.designwins.ai`,
  release `dw-microcontrollers-20261007T153515Z`, source/catalogue mismatch;
  repro in mail), #103 endpoints (still 404), SiC46x items, ACK.
- **mac-mini:** manifests for 11,612 SHAs, ACK.
- Seven re-kicked (206 events consumed); sentinel + fleet_changes markers
  cleared. Issue #25 (thedeep-coverage) prioritized: next Seven-lane build
  after cr-core-dependent closures + reader-v2 line.

## Open / gotchas for the next agent

- **cr-core 503 is a PROD OUTAGE** on www.designwins.ai MCU discovery. It is
  cr-core's fix; FE honest-failure states verified by browser QA.
- FE live mode: set `DECISION_API_URL` (base ends `/api/designwins`) +
  `DECISION_API_TOKEN` in the PREVIEW env only when cr-core's #103 receipt
  lands; negative-test per #72.
- ExportStore promotion: HOLD (Sam). Spine rc1 (PR #20) STAGED ONLY.
- Main worktree `feat/normalization-spine-20261020` carries OTHER lanes'
  unstaged changes (corpus/INDEX.jsonl, burn_vision.py,
  extract_pin_tables_tp4.py, spine.py + untracked) — DO NOT TOUCH.
- dw-preview `.env.local` (gitignored) holds facade key + session secrets;
  never commit. `crk_live_…` key lives in M4's `.env.local` (gitignored there).
- Scratch: `/tmp/sic46x-isolated` (cr-core @43889e57 shallow clone, tests pass
  with layout shim), `/tmp/dw-qa` (browser QA). `/private/var/folders/…/opencode/dw-fe`
  is the original FE work clone (superseded by the preview worktree).
- LaunchAgents added this session: `com.samkim.dw-preview` (:3118, KeepAlive),
  `com.samkim.fleetstate` (hourly). Modified: `com.harness.sentinel.nightly`
  (adds LKG push to seven). `com.harness.fleetstate` booted OUT (replaced).
