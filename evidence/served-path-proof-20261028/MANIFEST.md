# DW-TRUTH-FUNNEL-02 served-path proof — input manifest (2026-10-28)

System of record: GitHub issue `outcited/outcitedharness#5` (phase 2).
Reader/audit and tests are checked in; outputs are snapshot-pinned.
All paths below are aliased (public-artifact hygiene); full paths live only in
the gitignored local alias config (`scripts/evidence_path_aliases.local.json`).

## Declared dependency

- Pilot branch `truth-funnel/pilot-20261028` @ `9c570dc2bf124c76afab36f6030d12bb1ad4d992`
  (unmerged **PR #56**). This branch is stacked on it. We reuse the pilot's
  `scripts/sampled_source_proof.py` helpers and `scripts/alias_evidence_paths.py`
  alias tooling **read-only**; the pilot branch is not modified.

## Input SHAs

| input | alias | sha256 |
|---|---|---|
| served-path tool | `scripts/served_path_proof.py` | `c9fbc5eabe0429b659fc489154c9e87220e5072c5e020abcdbc439baf47803a6` |
| served-path tests | `tests/test_served_path_proof.py` | `720d1d810b2dd8accabad10ad58ca9ba9f30d6c544f8707aa2f7c96de70b804b` |
| FE main committed bundle | `fe://readonly/data/labs/compare_ready_spine.jsonl` | `231ac51e68f3dc75387c9a05b26004a6bfd917ca5441290dbc5df7468b052f9b` |
| FE main commit | `fe://readonly` HEAD | `c0810b32822aa74a879a313aad2a2da15da1190d` |
| export compare feed | `store://api-store-export/compare_ready.jsonl` | `ae2c2cd4de498c35561b00e111113875d20742807b2c5a5fd454da9eb15b3c31` |
| export fold manifest | `store://api-store-export/fold_manifest.json` | `d28910405c0f0d95830a8b351f9c29b2121726c8056bcba4a95a101fa5810f4f` |
| export cube | `store://api-store-export/cube.json` | `fe0f85c90e000583d7912dd6ae895d0263ba0954d0eb579d6f3c8df0bc327c5a` |
| export parts catalog | `store://api-store-export/parts_catalog.jsonl` | `ecab80712463d7a3c4d06da6f735d1ed73c030380cdc6d2e166caa8cfef40b8a` |
| deployments evidence | `deployments.json` (gh api) | empty list `[]` → no deployment records |
| vault CAS (docs by sha) | `nas://m5-4tb/vault/cas` | filesystem walk |
| FE claim-bound evidence | `fe://readonly/public/labs/r2/evidence` | 3 PNGs (LMR14030 R2 fixture) |

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
  evidence/served-path-proof-20261028/raw/deployments.json \
  evidence/served-path-proof-20261028/deployments.json
```

Tests:

```
uv run --with pytest --with pymupdf python -m pytest \
  tests/test_served_path_proof.py
```

## Denominators (measured)

- cohort: 10 claims, selection `doc_sha_overlap` (documented shortfall —
  byte-exact FE∩export intersection is 0).
- two-run reproducibility: identical except the `observation` block (asserted).
- link counts (independent, never summed): verified_source 1, identity_joined 0,
  folded 10, bundled 10, authorized 0, deployed 0, clickable 0.