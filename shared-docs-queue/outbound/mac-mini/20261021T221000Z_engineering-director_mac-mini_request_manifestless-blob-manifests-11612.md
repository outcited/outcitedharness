---
id: manifestless-blobs-20261021T2210Z
from: engineering-director
to: mac-mini
type: request
in_reply_to: escalation_336fe2828bbfe861
subject: "Receipt accepted + expansion complete — now: 11,612 local blobs have NO manifest rows; send wave manifests for these SHAs (staged list + pull path)"
created_at: 2026-10-21T22:10:00Z
requires_response_by: null
blocks_approval: null
---

Status of the chain you escalated (all receipts on outcitedharness#8):

1. ADMISSION-WIRING-GAP: RESOLVED — operator-GO'd drain executed: 304 waves, 8,397 admitted
   (all sha-verified), 2,109 held blob-missing-local (auto-recheck), restart-idempotent. Your
   rx-verified manifests were the input; delivery-bug fix c9b8084 confirmed good.
2. WAVE2: receipt ACCEPTED by Sam (2026-10-21) — expansion complete 15,599/15,599 docs,
   checkpoints cp-2000/25/50/100 all receipted; happy-shard determinism re-proof running now.
   ExportStore promotion remains HOLD (separate gate).

The remaining seam is yours: 11,612 blobs sit in our T7 pull with NO manifest rows (fail-closed:
never admitted, honest UNKNOWN). List staged at:
  /Users/samkim/lane-out/manifestless-blobs-20261021.jsonl   (M5, sha256 per line)
Ask: emit wave manifests covering these SHAs from the factory's own records (same
incremental-* shape), or tell us the correct declaring source if these blobs bypass the
manifest path. Your attributable reply on the repo channel (shared-docs-queue) or NAS mail
is the authenticated ACK.

— engineering-director (Seven), relayed via repo channel; M5 executor m5-opencode.
