# DW-TRUTH-FUNNEL-01 pilot — source manifest (2026-10-28)

System of record: GitHub issue outcited/outcitedharness#5.
Reader and audit are checked in; outputs are snapshot-pinned.
All paths below are aliased (public-artifact hygiene); full paths are in the
local session log only.

## Inputs (INPUT SHAs)

| input | alias | sha256 |
|---|---|---|
| funnel reader | `scripts/truth_funnel.py` | `795091c1882c5973dcc7efedc287a78af4c34a7a3397449a4d2a71440c791b62` |
| audit tool | `scripts/sampled_source_proof.py` | `64192880675843be0536591b93dd77ae3216bd39ba7f7b0e23f9b4f73b394d43` |
| move-batch claims (POWER) | `local://lane-out/spine/batch-spine-claims-v2.jsonl` | `36a47f27ee5260576723d3addc4fe16dd06b8e2e78a1393271943eacf6c15f62` |
| served compare feed | `store://api-store-export/compare_ready.jsonl` | `ae2c2cd4de498c35561b00e111113875d20742807b2c5a5fd454da9eb15b3c31` |
| served cube | `store://api-store-export/cube.json` | `fe0f85c90e000583d7912dd6ae895d0263ba0954d0eb579d6f3c8df0bc327c5a` |
| served fold manifest | `store://api-store-export/fold_manifest.json` | `d28910405c0f0d95830a8b351f9c29b2121726c8056bcba4a95a101fa5810f4f` |
| board snapshot (reused) | `local://Harnessv1/results/api-coverage/board/20261018T081745Z.json` | `829f7ad228d745043f95181c37c1beee9c9376b2370311f1a918ddd723e44b1c` |
| vault CAS (docs by sha) | `nas://m5-4tb/vault/cas`, `nas://m5-4tb/vault/landing` | filesystem walk |
| staged MCU pillar store (MCU claims) | `nas://m5-4tb/staging/datasheet_extractions/mcu_family_content` | filesystem walk |

The reader file is byte-identical to lineage `93fcdc5` (sha
`795091c1…1b62`); it was run UNMODIFIED.

## Reproduction

Funnel (Task 1):

```
python3 scripts/truth_funnel.py \
  --out-dir evidence/truth-funnel-20261028/raw \
  --vault-cas-roots <nas>/vault/cas <nas>/vault/landing \
  --staging-root  <nas>/staging/datasheet_extractions \
  --cr-core       <nas>/staging/datasheet_extractions/mcu_family_content/from-cr-core \
  --nas-root      <nas> \
  --pin-batch-dir <local>/Harnessv1/results/pin-tables-20261014 \
  --export-root   <store>/api-store-export \
  --board-snapshot <local>/Harnessv1/results/api-coverage/board/20261018T081745Z.json
```

Audit (Task 2): see `sampled_source_proof.py` docstring; same source flags
plus `--power-claims`, `--mcu-claims-root`, `--served-export`, `--vault-cas`.
Then alias private paths into the committed copies:

```
python3 scripts/alias_evidence_paths.py <raw/out> evidence/truth-funnel-20261028/<out> ...
```

Tests:

```
uv run --with pytest --with pymupdf python -m pytest \
  tests/test_truth_funnel.py tests/test_sampled_source_proof.py
```

## MCU claim source — location note

No MCU claim batch exists in the spine claim-batch format that the POWER lane
uses (`*.jsonl` with `pdf_sha` + `page_1based` + `verbatim` per row). The
spine dir carries only the single `batch-spine-claims-v2.jsonl` lane; the
served export carries only `compare_ready.jsonl` (OPN rows, no raw claims);
the NAS staging carries the MCU claims as **per-part staged pillar records**
(`mcu_family_content/datasheet_extractions/<part>.json`, field
`pillars.<field>` with `pdf_sha` + `page_1based` + `verbatim` + `value` +
`unit`). The audit samples MCU claims from that staged store — the only MCU
claim source that exists — and never substitutes POWER rows.

## Denominators (measured)

- funnel reader: 17 stages, 0 errors (exit 0).
- audit sample: 20 claims (10 POWER + 10 MCU); classification counts in
  `sampled_source_proof_v1.json`.

## Remaining UNKNOWNs

- `i.fe_click_resolvability` — UNKNOWN (needs live facade/FE probe).
- served `parts_a_pinout.jsonl` absent in the current export dir →
  `h.served_release_records.served_pinout_records` = UNKNOWN.
- POWER claims carry no part/OPN identity in the batch (identity UNKNOWN);
  served-OPN join is therefore UNKNOWN for the POWER lane.
- No viewer resolver in the served export → `resolvable.clickable` = UNKNOWN
  for every claim (a bare SHA is not a working link).