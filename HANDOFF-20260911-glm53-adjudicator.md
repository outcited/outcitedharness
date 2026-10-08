# Handoff to GLM-5.3-Flash Agent - 2026-09-11

Read this before editing or running cluster commands. Sam requested a handoff,
not a service cutover. Do not change OpenCode configuration or inference services.

## Immediate Task

Continue the power-gold adjudicator work after a validation failure and retraction.
The next useful change is preserving full source evidence context, not loosening
comparison rules to recover attractive scores. Then coordinate the corrected
fixture re-ship and start the RDS-condition subset of the ambiguous queue.

Read the latest CR reply in full:

`/Volumes/M5_4TB/agent-inbox/m5-cursor/20260911T172600Z_cursor-cr_m5-cursor_answer_adjudicator-retraction-ack-20260911.md`

## Git and Ownership

- Workspace: `/Users/samkim/Harnessv1`, branch `main`.
- HEAD: `37603fee` (initial adjudicator patch, contains the faulty fallback).
- Previous HEAD: `01c3bf60 Add layout IR and OCR fallback to page evidence`.
- `github`: `https://github.com/beargallbladder/outcitedharness.git`.
- `origin`: `https://origin.cursor.com/samkim2dgx/harnessv1.git`.
- Sam explicitly authorized the initial commit. Nothing was pushed.
- The subsequent corrective changes are UNCOMMITTED. Do not reset to HEAD:
  that would restore the faulty behavior. No commit/amend/push without Sam's ask.
- This agent's application-code changes are limited to:
  `scripts/power_gold_adjudication.py` and
  `tests/test_power_gold_adjudication.py`.

Other dirty work belongs to Sam or other agents. At handoff, this includes
`.env.example`, `config/models.yaml`, `config/pricing.yaml`,
`harness/electronics/power_datasheet.py`,
`scripts/compare_datasheet_frontier.py`,
`tests/test_datasheet_frontier_comparison.py`,
`tests/test_power_datasheet_mosfet.py`, `tests/test_power_datasheet_prose.py`,
`HANDOFF-20260907-qwen-cluster.md`, and three `results/key-features-grid-*`
files. Untracked unrelated work includes `harness/electronics/word_columns.py`,
`scripts/build_datasheet_escalation_queue.py`, `deploy/glm53-flash/`,
`deploy/glm53/`, and `HANDOFF-20260910-glm53-flash-speed.md`.
Inspect current status; do not stage, revert, or overwrite these incidentally.

## What Went Wrong

CR reported two adjudicator defects: missing per-row resistance-unit normalization
and pooling continuous drain current with pulsed/IDM/output-characteristic rows.

The initial patch added a WRONG fallback: interpret a declared Ohm label as a
bare milliohm numeral too. This can match quantities 1,000 times apart. It also
matched pooled numeric values without checking conditions or typical/max
qualifiers. The purported ROHM selector-unit justification was unsupported.

The agent then incorrectly called the resulting count collapse validation and
mailed CR. Passing tests and matching CR's aggregate 314 count did not validate
the physics. Sam challenged this; the agent acknowledged the error, corrected
the working tree, and sent an explicit retraction. Do not repeat the earlier
claims that all residual disagreements are genuine corrections.

## Current Corrective Code

Read the actual diff against HEAD, not just this summary.

- `label_readings` now uses only the label's declared unit; no numeral fallback.
- Resistance normalization accepts explicit units; speculative damaged-unit
  readings such as bare W, bare m, m#, and compound mOhmV are no longer accepted.
- CR's bounded `mW` -> `mOhm` glyph correction is applied only to an RDS
  document row, never a fixture label.
- `id_a` filtering rejects IDM/pulsed/peak/chip/silicon-limited evidence and
  checks labels and condition fields as well as symbols.
- `same_context` requires explicit condition and quantity qualifier on both
  sides. It compares normalized text conservatively and rejects additional or
  conflicting table conditions. It is NOT a general electrical-condition parser.
- A verdict requires a single normalized comparable value. Missing context or
  conflicting values is `ambiguous_comparison`, not a match or disagreement.
- Payload now includes row condition, table condition, and same-context flag.
- Full context is STILL NOT preserved: labels remain capped at 100 characters
  and evidence at four rows; source table/header/column provenance needs work.

Do not infer absent qualifiers from field names or choose a convenient numeric
maximum across rows. A strict gate holding everything can reflect lost evidence,
not bad source documents. Inspect both the report producer and grid schema.

## Verification and Reports

Last full suite run: `.venv/bin/pytest -q` -> **798 passed, 6 warnings**, 14.91s.
This predates any additional concurrent edits visible at handoff; rerun after edits.

New CLI-level tests cover declared-unit factor-of-1000 rejection, explicit-context
positive matches, VGS mismatch, typical/max mismatch, missing qualifier, and
conflicting values. Helper tests also cover signed conditions, extra table
context, and ID exclusions. These are regression tests, NOT PDF-level validation.

Seven historical datasets were regenerated, each with
`adjudication-context-reviewed.jsonl` alongside the originals:

- `results/power-grids-20260909/`
- `results/power-grids-20260909-ifx/`
- `results/power-grids-20260909-ifx2/`
- `results/power-grids-20260909-ifx3/`
- `results/power-grids-20260909-ifx4/`
- `results/power-grids-20260909-rohm-si/`
- `results/power-grids-20260909-ifx-rohm/`

Inputs per directory: `grids.jsonl` and `gold-report-lenient.json`.
Keep original `adjudication.jsonl`: CR used that evidence historically.
**`adjudication-fixed.jsonl` is superseded and unsafe for promotion.**

IFX2 revised totals (3,963): 2,210 reader_miss, 1,636 ambiguous_comparison,
117 no_spec_row, zero established identities/disagreements under the strict gate.
RDS: 1,289 ambiguous, 23 no-spec, 262 reader misses.
ID: 314 ambiguous, 85 no-spec, 246 reader misses.
ROHM-Si: 207 ambiguous, 1 reader miss (198 of the ambiguous are RDS).
These are triage counts only. No new-fixture end-to-end rescore was completed.

## CR Reply and Next Sequence

CR accepted the retraction. They confirm nothing consumed the faulty
`adjudication-fixed.jsonl` outputs.

Their 513 corrections stand on their separately condition-matched and PDF-checked
evidence. They reopened ROHM PDFs: the source outline box explicitly says
`RDS(on)(Max.) 3.4 Ohm`; the max qualifier was absent from the abbreviated payload.
Do NOT reinterpret that 3.4 Ohm as 3.4 milliohm. CR is enriching its corrections
layer with the missing qualifier evidence for 197 ROHM prose-basis corrections.

Their requested sequence:

1. CR finishes enriching the corrections layer (in flight in their latest mail).
2. **Our side rebuilds/re-ships the re-labeled fixtures using that layer.** The
   earlier assumption that CR would ship our fixtures was wrong. Locate and
   inspect the actual correction artifact/schema before applying anything.
3. Our ambiguous-reader queue remains ours. They propose starting with the
   RDS-condition subset of the 1,636 IFX + 207 ROHM ambiguous pool. The original
   128-row queue is not a complete current backlog.
4. Supply full row context: condition column, Min/Typ/Max column provenance,
   table caption, full source wording and pages. Preserve ambiguity where the
   source cannot support a verdict. CR will re-run its decomposition.

Useful files to examine next: `scripts/power_gold_adjudication.py`,
`scripts/score_key_features_gold.py`, `scripts/emit_power_grids.py`, existing
`grid_rows.jsonl`/`grids.jsonl`, and CR's correction artifact. Do not alter the
dirty reader merely to improve comparator counts.

## Internal Mail

This is file-based agent mail, not email to a person. Sender in this thread is
`m5-cursor`, recipient `cursor-cr`. Incoming replies live at
`/Volumes/M5_4TB/agent-inbox/m5-cursor/`.

Sent messages:
- `adjudicator-bugs-fixed-20260911` at 16:48:11Z (overconfident, retracted).
- `adjudicator-validation-retraction-20260911` at 17:14:06Z (corrective notice).

Latest reply: `adjudicator-retraction-ack-20260911`, 17:26:00Z.

Use the supported sender, NOT handwritten inbox frontmatter:

```sh
npx tsx scripts/orchestration/send-message.ts \
  --from=m5-cursor --to=cursor-cr --type=answer \
  --id=UNIQUE_ID --in-reply-to=adjudicator-retraction-ack-20260911 \
  --subject="SUBJECT" --body-file=ABSOLUTE_BODY_PATH \
  --shared-root=/Volumes/M5_4TB
```

Run with workdir `/Volumes/M5_4TB/repos/outcited-ai-sandbox-20260822`.
`--from` is necessary: the tool defaults to cursor-cr. `--dry-run` validates.
It writes both the recipient inbox and a fallback queue; that queue is not a
Git commit. Sam approved the prior outgoing messages. Do not claim work not done.

## Production Boundaries and Archive Completion

Flash and flagship are DIFFERENT models. See
`HANDOFF-20260910-glm53-flash-speed.md` for deployment details, but its pending
flagship archive/removal section is now outdated.

- GLM-5.3-Flash: production on DGX2 + ASUS1 + DGX3 + ASUS3, TP4.
- Container: `glm53-flash-tp4`; weights: DGX2
  `/srv/models/GLM-5.3-Flash-NVFP4-redhat`, workers access via NFS.
- Pinned configuration still reads graphs `FULL_AND_PIECEWISE`, 16,384 batched
  tokens, six sequences, MTP k=4, 1,048,576 context, fp8 KV + marlin.
- Image: `ghcr.io/tonyd2wild/vllm-glm53-flash:sm121-v11-dflash2`.
- Endpoint: `http://100.116.221.82:8000/v1`, served name `glm-5.3-flash`.
- Last checked all four Flash containers remained running through cleanup.
  Container status is not a fresh inference qualification; no inference was sent.
- Qwen3.8-Flash-Next stays on ASUS2/ASUS4. Do not retune either lane, stop them,
  send test inference during Sam's work, or touch Qwen `/flush_cache`.
- Preserve Spark/DGX1's protected embedding and GCI services.

743B flagship archive task completed to PORTABLE MEDIA, not yet confirmed on
the mini's 16 TB BIGGEYBIGS:

- Sam moved the LaCie Rugged Mini SSD (500 GB, serial NT372G6Z) from ASUS3 to DGX2.
- Existing personal Charley files were preserved. Copied the flagship directory
  `GLM-5.3-Int4-Int8Mix` plus its SHA256 manifest onto the exFAT drive.
- Transfer: 406,303,428,845 bytes including manifest, about 18m32s.
- Readback verification reported **398/398 OK**, no failed lines.
- Drive synced and cleanly unmounted from DGX2; physical location now requires Sam.
- Sam explicitly authorized deleting the DGX2 flagship weights and removing the
  stopped `glm53-tp4` containers on all four ranks, then authorized removing the
  flagship image `harness/glm53-flagship-vllm:ab666069-sm121`, `~/glm53-tp4/`, and
  `~/.cache/glm53-vllm/` from all four ranks. These removals were performed.
- Flash image/config/weights were kept. DGX2 last showed 2.1T free, 41% used.
- Keep receipts: `/srv/models/GLM-5.3-Int4-Int8Mix.sha256`,
  `~/glm53-lacie-rsync.log`, `~/glm53-lacie-verify.log` on DGX2.
- Do not delete anything from the LaCie. No verified BIGGEYBIGS copy was made
  in this session. The portable archive is the known retained weights copy.

Earlier WAN bandwidth probes were flawed (some dd offsets read beyond EOF);
do not treat the claimed 29 Mbps ceiling or SSH-handshake diagnosis as established.
Similarly the earlier fabric check measured only one HCA on each of the four
Flash nodes (~111.5-111.6 Gb/s as senders), not full 200G aggregate, both HCAs,
or the ASUS2/ASUS4 pair. Do not promote it to a complete fabric qualification.

## Start Here

Read the CR reply, inspect the two-file corrective diff and concurrent worktree
state, then inspect where source qualifiers/conditions are lost between grid
extraction, gold reports, and the four-row adjudication payload. Preserve evidence
first, add negative end-to-end tests, and validate named source cases rather than
tuning toward CR's counts. Ask Sam before committing/pushing the correction.
