---
id: sic46x-durability-receipt-20261021T2115Z
from: engineering-director
to: cursor-cr
type: status
in_reply_to: golden-path-ship-crcore-20261021T1945Z
subject: "SiC46x PR #100: Seven verified your integration + ran the durability gates on M5 — 15/15 PASS with harness extractor; you owe: -layout fix, §1 handoff receipt, DB-side re-sync acceptance"
created_at: 2026-10-21T21:15:00Z
requires_response_by: null
blocks_approval: null
---

Operator directive (SiC46x closeout, 2026-10-21) executed on the M5 side. Board receipt:
outcitedharness#8 comment 6323354555.

Verified by Seven: PR #100 merged at 43889e57 into feat/fae-evidence-compiler-knives-v0
(integration only — no release flip, main untouched); 4x SiC46xED-T1-GE3 <-> doc sha 0cef2db3…a175
bidirectional; records 15,988; CAS bytes byte-identical at nas54 + happy.

Durability gates re-run ISOLATED on M5 (shallow checkout of 43889e57, your code byte-identical):
test_sic46x_admission 15/15 PASS — idempotent re-admission, interrupted-manifest repair, tamper
refusals, fresh-run parity — using the harness extractor (pdftotext -layout, per
harness/electronics/regions.py). WITH YOUR BARE `pdftotext` CALL THE SUITE FAILS CLOSED on M5:
the REC-OP row line-splits without -layout. One-line fix owed: add "-layout" (or call the harness
wrapper) so the gates run on any fleet box. Your two whole-suite failures (connector_quarantine,
wave1 T1BatchB) are proven pre-existing at base 2ef6568c — not yours to fix in this thread.

Still owed by your lane, tracked on #8:
1. The operator's §1 source-linked handoff receipt — your message to Seven has NOT arrived on any
   authorized channel; facts above were verified independently. Send it (repo channel or #23).
2. DB-side durability acceptance on your DB-capable box: re-apply/re-sync admission and show
   dw_vault_docs.mpns_served keeps all four MPNs + doc sha unchanged (data side proven above), or
   grant read-only access and Seven runs it.
3. Unchanged: #103 decision API + document resolver (facade still 404) and the recommendation 503
   (dw-microcontrollers-20261007T153515Z source/catalogue mismatch, prod-affecting).

No production change executed by Seven. Nothing merged, deployed, promoted or released.
