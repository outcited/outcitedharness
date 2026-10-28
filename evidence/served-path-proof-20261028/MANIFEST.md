# DW-TRUTH-FUNNEL-02 served-path proof — input manifest (2026-10-28, corrected)

System of record: GitHub issue `outcited/outcitedharness#5` (phase 2).
Reader/audit and tests are checked in; outputs are snapshot-pinned.
All paths below are aliased (public-artifact hygiene); full paths live only in
the gitignored local alias config (`scripts/evidence_path_aliases.local.json`).

This is the **owner-required correction pass** (5 corrections). It amends the
2026-10-28 branch; history is not rewritten.

## Declared dependency

- Pilot branch `truth-funnel/pilot-20261028` @ `9c570dc2bf124c76afab36f6030d12bb1ad4d992`
  (unmerged **PR #56**). This branch is stacked on it. We reuse the pilot's
  `scripts/sampled_source_proof.py` helpers and `scripts/alias_evidence_paths.py`
  alias tooling **read-only**; the pilot branch is not modified.

## Input SHAs

| input | alias | sha256 |
|---|---|---|
| served-path tool | `scripts/served_path_proof.py` | `a587b315643eae073263034b9d85e43f91b87e00b35cfd04dfb104c322e8c58c` |
| served-path tests | `tests/test_served_path_proof.py` | `37c349605c4da420b9b6c0705d80bbfce6aa441b0de5036f12657ab8cbffa026` |
| served-path proof output | `served_path_proof_v1.json` | `02634474e016ed302a10da6eec9ad195cc08617089618cd6fe3827fe6aa7b061` |
| quote-failure disposition | `quote_failure_disposition_v1.json` | `6b34f79916fd4d1818b18a226b7ad3ef6faf5c25c1dca440adaf1bb108757f21` |
| FE main committed bundle | `fe://readonly/data/labs/compare_ready_spine.jsonl` | `231ac51e68f3dc75387c9a05b26004a6bfd917ca5441290dbc5df7468b052f9b` |
| FE main commit | `fe://readonly` HEAD | `c0810b32822aa74a879a313aad2a2da15da1190d` |
| export compare feed | `store://api-store-export/compare_ready.jsonl` | `ae2c2cd4de498c35561b00e111113875d20742807b2c5a5fd454da9eb15b3c31` |
| export fold manifest | `store://api-store-export/fold_manifest.json` | `d28910405c0f0d95830a8b351f9c29b2121726c8056bcba4a95a101fa5810f4f` |
| export cube | `store://api-store-export/cube.json` | `fe0f85c90e000583d7912dd6ae895d0263ba0954d0eb579d6f3c8df0bc327c5a` |
| export parts catalog | `store://api-store-export/parts_catalog.jsonl` | `ecab80712463d7a3c4d06da6f735d1ed73c030380cdc6d2e166caa8cfef40b8a` |
| deployments evidence | `deployments.json` (gh api) | empty list `[]` → no deployment records |
| vault CAS (docs by sha) | `nas://m5-4tb/vault/cas` | filesystem walk |
| FE claim-bound evidence | `fe://readonly/public/labs/r2/evidence` | 3 PNGs (LMR14030 R2 fixture) |

## C3 — release fingerprint (cube byte-sha verification result)

The cube fingerprint is verified against **actual bytes read from
`cube.json`**, never against a manifest field compared to itself.

| check | value | result |
|---|---|---|
| cube.json bytes read + sha256 (measured) | `fe0f85c90e000583d7912dd6ae895d0263ba0954d0eb579d6f3c8df0bc327c5a` | — |
| fold_manifest `cube.sha256` (recorded) | `fe0f85c90e000583d7912dd6ae895d0263ba0954d0eb579d6f3c8df0bc327c5a` | **MATCH** |
| fold_manifest `files["cube.json"].sha256` (recorded) | `fe0f85c90e000583d7912dd6ae895d0263ba0954d0eb579d6f3c8df0bc327c5a` | **MATCH** |
| compare_ready.jsonl recorded digest | *(none exists in fold_manifest)* | recorded as absent — **not invented** |

**Overall cube fingerprint: MATCH (measured bytes == recorded).** A corrupted
cube would fail this check loudly (see `test_cube_fingerprint_corrupted_bytes_fails`).

## Deployed-commit evidence

`gh api repos/outcited/designwins-v2/deployments` → `[]` (0 records).
FE head `c0810b32…` commit status → `pending`. Deployment metadata is therefore
**inconclusive** → the `deployed` link is **UNKNOWN** for every claim. The live
site (`https://www.designwins.ai`) is auth-gated: every lab asset path
307-redirects to `/login`.

## Reproduction

```
python3 scripts/served_path_proof.py \
  --export-root   <store>/api-store-export \
  --fe-bundle     <fe>/data/labs/compare_ready_spine.jsonl \
  --parts-catalog <store>/api-store-export/parts_catalog.jsonl \
  --vault-cas     <nas>/vault/cas \
  --deployments   evidence/served-path-proof-20261028/raw/deployments.json \
  --evidence-dir  <fe>/public/labs/r2/evidence \
  --out-dir       evidence/served-path-proof-20261028/raw \
  --fe-commit     c0810b32822aa74a879a313aad2a2da15da1190d \
  --resolve-probe gated
```

Then alias private paths into the committed copies:

```
python3 scripts/alias_evidence_paths.py \
  evidence/served-path-proof-20261028/raw/served_path_proof_v1.json \
  evidence/served-path-proof-20261028/served_path_proof_v1.json \
  evidence/served-path-proof-20261028/raw/SERVED-PATH-PROOF.md \
  evidence/served-path-proof-20261028/SERVED-PATH-PROOF.md \
  evidence/served-path-proof-20261028/raw/quote_failure_disposition_v1.json \
  evidence/served-path-proof-20261028/quote_failure_disposition_v1.json \
  evidence/served-path-proof-20261028/raw/QUOTE-FAILURE-DISPOSITION.md \
  evidence/served-path-proof-20261028/QUOTE-FAILURE-DISPOSITION.md \
  evidence/served-path-proof-20261028/raw/deployments.json \
  evidence/served-path-proof-20261028/deployments.json
```

Tests:

```
uv run --with pytest --with pymupdf python -m pytest \
  tests/test_served_path_proof.py
```

## Denominators (measured, corrected)

- cohort: 10 claims, selection `doc_sha_overlap` (documented shortfall).
  - exact **claim-key** intersection (independent comparison, C2): **0**
  - raw-line byte-exact intersection: **0**
  - claim-identity present in export: **1** (lm3410)
- two-run reproducibility: identical except the `observation` block (asserted in
  both `served_path_proof_v1.json` and `quote_failure_disposition_v1.json`).
- link counts (independent, never summed): verified_source 1, identity_joined 0,
  folded 0, bundled 0, authorized 0, deployed 0, clickable 0.
- fold distribution (C1): FAILED 1 (lm3410 — identity present, claim fields
  diverge), ABSENT 9 (slug-shaped OPNs with no export identity row).
- bundle distribution (C2): DIVERGENT 1 (lm3410 — key present both sides, fields
  differ), UNKNOWN 9 (export key unconstructible: no export identity row).
- quote-failure disposition (C4): EXTRACTION_ARTIFACT 9 (of 9 failures).