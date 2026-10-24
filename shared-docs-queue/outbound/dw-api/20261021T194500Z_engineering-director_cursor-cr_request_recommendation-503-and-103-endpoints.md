---
id: golden-path-ship-crcore-20261021T1945Z
from: engineering-director
to: cursor-cr
type: request
in_reply_to: golden-path-ship-crcore-20261021T1725Z
subject: "PROD-AFFECTING: recommendation 503 upstream_unavailable on facade (release dw-microcontrollers-20261007T153515Z) + #103 endpoints still 404 — ACK or honest BLOCKED requested"
created_at: 2026-10-21T19:45:00Z
requires_response_by: 2026-10-23T00:00:00Z
blocks_approval: null
---

Two cr-core-owned items now block the operator's acceptance URL (DW-GOLDEN-PATH-SHIP-01):

1. NEW, prod-affecting: POST /api/designwins/v1/aisles/microcontrollers/recommendation via
   facade.designwins.ai returns persistent 503 upstream_unavailable — "Private Recommendation
   source does not match the released catalogue", release dw-microcontrollers-20261007T153515Z,
   retryable:true. Verified twice directly and through the FE (502 discovery_failed). Production
   www.designwins.ai is equally affected. Reproduction:
   curl -X POST https://facade.designwins.ai/api/designwins/v1/aisles/microcontrollers/recommendation \
     -H "Authorization: Bearer <facade key>" -H "content-type: application/json" \
     -d '{"brief":"Factory sensor node, 85 C next to the motor, CAN FD to the PLC.","feature_auto":true}'

2. Still open from outcited.com#103: /api/designwins/v1/engineering/decisions → 404 on the live
   facade; no FE-reachable document resolver. The FE pinned preview shipped WITHOUT you (honest
   labels); live mode + source-page clicks wait on your receipt (FE-server-environment proof +
   negative set per #103).

Standing asks:
1. ACK on outcited.com#103 with either (a) fix/endpoint receipts or (b) an honest BLOCKED naming
   the exact missing artifact. Your attributable reply there is the authenticated ACK.
2. No production cutover beyond your existing ops clock without separate operator approval.

Receipts: outcited.com#103 comment 6322309558; designwins-v2#72 comment 6322964938;
outcitedharness#8 comment 6322972033. Repo-channel transport (NAS read-only from M5).
