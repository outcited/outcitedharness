# DesignWins Truth Funnel — snapshot 2026-10-18T17:53:48+00:00
schema `designwins.truth-funnel.v1` · reader `scripts/truth_funnel.py` · host m5
Fail-closed. UNKNOWN means not measured. Grains never summed.

| stage | grain | count | cohort | denominator | status |
|---|---|---|---|---|---|
| a.crawled_assets | asset | 118573 | vault cas+landing PDFs | n/a | measured |
| b.addressable_parts | part | 121695 | board categories | 171551 | measured |
| b.addressable_parts.connector | part | 401 | connector | 401 | measured |
| b.addressable_parts.mcu | part | 64272 | mcu | 97718 | measured |
| b.addressable_parts.power | part | 36636 | power | 37899 | measured |
| b.addressable_parts.refdesign | part | 20386 | refdesign | 35533 | measured |
| c.facet_parts | part | 157944 | staging mcu+power+refdesign+connector | 226411 files | measured |
| c.pinout_parts.staging | part | 9996 | staging files with non-empty pinout block | 226411 files | measured |
| d.staged_pin_rows | claim | 24162 | rows inside staging pinout blocks (quote-verified at batch retrieval per receipt; re-verification not re-run here) | staging pinout parts | measured |
| c.pinout_parts.cr_core | part | 5471 | cr-core delivery files with pinout | 5721 files | measured |
| e.verified_pin_docs | part | 14256 | pin batch verdict=ok docs | 23,318 batch docs (receipt) | measured |
| e.completeness_tiers | part | 794 | admission tiers | 14256 | measured |
| f.identity_exact_joined | identity-exact | 16 | identity-joins status=exact | 14147 | measured |
| e.gold_rows | claim-verified | 5574 | pinout-gold r1.1 gold rows | 17292 | measured |
| e.validated_gt_parts | part | 1487 | VALIDATED_GROUND_TRUTH files | all validated-store files | measured |
| h.served_release_records | release-record | laned=True | api-store-export served release | n/a | measured |
| i.fe_click_resolvability | UNKNOWN | UNKNOWN |  |  | UNKNOWN |

## Sources
- **vault_cas**: available=True producer_at=2026-10-04T20:59:40+00:00 `['/Volumes/M5_4TB/vault/cas', '/Volumes/M5_4TB/vault/landing']` — filesystem CAS walk
- **board_snapshot**: available=True producer_at=2026-10-18T08:17:45+00:00 `/Users/samkim/Harnessv1/results/api-coverage/board/20261018T081745Z.json`
- **staging_root**: available=True producer_at=2026-10-09T18:52:24+00:00 `/Volumes/M5_4TB/staging/datasheet_extractions`
- **cr_core**: available=True producer_at=2026-08-10T16:33:37+00:00 `/Volumes/M5_4TB/staging/datasheet_extractions/mcu_family_content/from-cr-core`
- **pin_batch_dir**: available=True producer_at=2026-10-15T22:35:02+00:00 `/Users/samkim/Harnessv1/results/pin-tables-20261014`
- **nas_gold**: available=True producer_at=2026-10-12T04:32:45+00:00 `/Users/samkim/mnt/charleybasecamp-smb/m5-4tb/pinout-gold/pinout-gold-r1.1-20261012/manifest.json` — distinct population; rows are not parts
- **nas_validated**: available=True producer_at=2026-07-28T01:50:00+00:00 `/Users/samkim/mnt/charleybasecamp-smb/m5-4tb/DigiKey_Reference_Designs/MCU_Pinout_Validated`
- **gold_mount_dead**: available=False producer_at=UNKNOWN `/Users/samkim/mnt/yosemiteraid5/Public/m5-opencode/pinout-gold` — board GOLD_FREEZE path; dead since 2026-10-16
- **export_root**: available=True producer_at=2026-10-18T08:16:39+00:00 `/Users/samkim/mnt/yosemiteraid5/Public/m5-opencode/api-store-export/cube.json`
