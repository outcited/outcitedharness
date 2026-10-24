# RUST-ACTIVATION-03 + PINOUT-CATALOG-04 — Interim Report — 2026-10-17

**Branch:** `agent/power-data-01` · **Predecessors read:** `ab9fff6a`
(mine), `1ef7fc44` (FACTORY-ORCHESTRATION-03 acceptance — PASS),
`bbfb34ae` (PINOUT-ADMISSION-02 tiers), `fd255c91` (PINOUT-IDENTITY-03
join chain + the 11,195 bucket). Accepted frozen/parity evidence NOT
repeated.

## PINOUT-CATALOG-04 — COMPLETE

**Population:** exactly the 11,195 `cause=opn-absent-from-served` rows
from `identity-joins.jsonl` @ `fd255c91` (slug, stem, manufacturer, sha,
pin-rows, tier). Every row classified; 7,181+1,916+2,098 = 11,195
reconciles exactly.

| verdict | count | rule (deterministic) |
|---|---:|---|
| **ADMIT** | **7,181** | manufacturer canonical (delivery-manifest table, 35 mfrs) ∧ cleaned stem printed in its OWN document in the ordering/part-numbering region ∧ unique canon(opn)+manufacturer |
| DEFER | 1,916 | title-only hit (lit-number risk, human review) 1,553 · present-but-not-ordering 177 · doc not held 131 · actually-served join-miss 55 |
| REJECT | 2,098 | stem absent from own document (full-length recheck) 1,356 · document-id shape (en.DM#####, 20005624B) 714 · mfr not in manifest table 26 · duplicate identity 2 |

**No fuzzy joins** (raw substring → line-level canon containment, both
recorded), **no invented identities** (admitted OPN is a printed form
verified in-document; stem cleaning recorded per row), **no gold
promotion, no release** — output is an intake packet for cr-core:
`results/soak-m3-rust/pc04-opn-intake-packet.json` (7,181 candidates
with per-row evidence: hit pages, ordering pages, match form, package
token + page, sha, pin-rows).

**Evidence pass:** 10,696/11,195 held blobs read from corpus deliveries
(NFS); ~45 min × 8 workers. Two audit-driven refinements: v2 (stem
cleaning — leaked `.pdf`/`DS-` prefixes, onsemi `-D` doc-rev suffixes;
canon-line matching) moved 6,220 false rejections to evidence-based
verdicts; v3 (served-collision check → join-miss routing; title-only
downgrade; full-length recheck of absent-rejects) tightened admits.
Admit mix: onsemi 3,438 · nxp 791 · microchip 783 · TI 694 · infineon
663 · st 411 · others. Packages: SOIC 1,082 · SOT 1,059 · QFN 639 · … ·
UNKNOWN 1,898 (named absence).

**Blockers/notes:** 131 docs not held (defer, acquisition list in the
JSONL); 499 blob-miss shas overlap the doc-id/tier buckets; package
UNKNOWN never gated admission (absence named).

## RUST-ACTIVATION-03 — RUNNING (canary launched per 1ef7fc44's PASS)

- Cohort: **full POWER aisle, 9,201 docs** (NFS-enumerated, cohort frozen
  `ra03-power-cohort.json`), substrates 9,201/9,201 (local store 7,149 +
  extract-results 2,052 localized to NVMe — immune to the SMB read-wall
  that blocked enumeration this session).
- Isolated state: `scratch/m5-soak/ra03-state` (369 chunks, max-attempts
  3, lease 900 s + per-doc renewal); outputs `ra03-out` (never production
  queues); 22 workers, Rust controller + Python model calls (shim → live
  burn_aisle chain, TP4-local, text fail-closed).
- Progress at interim: ~570/9,201, 21 chunks sealed, 0 failed; CPU
  ~200-250% of 1600% (endpoint-bound, as measured in the M3 soak).
- ETA ≈ 25 h; final reconciliation (every doc: registered = sealed +
  leased + queued; outputs vs Python baseline per stem), throughput and
  recommendation follow at completion.

**Subprocess overhead (benchmark, endpoint-free replay timing):**

| operation | measured |
|---|---|
| python spawn + seam import | 18 ms |
| `dw-verify` spawn + 20-claim verdict | **3.4 ms** |
| `dw-parse` spawn + parse | 16.6 ms |
| 100-doc replay pass, rust-verify vs python-verify | 5.0 s vs 4.2 s → **+7.3 ms/doc** |

Against ~30 s/doc endpoint time, the subprocess seam is ~0.02-0.05% —
noise. (dw-soak's shim spawn measured 31 ms in RC01.)

**Rollback verified (live demonstration):** one Rust worker paused
(SIGSTOP) mid-run; the Python factory path (`rc02_python_live_pass.py`
→ live burn_doc, seam default python) ran the same cohort docs
concurrently and emitted **shape-identical rows** (same keys, same fold
format) into its own output dir — launcher-flip rollback works with zero
migration while the canary is live. Rust worker resumed cleanly after.

## M4 coordination — 48 V→5 V @ 1.5 A decision API (LIVE, this session)

- Engine started from the m4 worktree (`ENGINEERING_DECISIONS_ENABLED=1`
  loopback-noauth), health OK on bundle `curve-evidence-bundle-v3-…`.
- **The real query answered:** condition-matched rows SiC463 93.934% /
  SiC464 93.933% / SiC462 93.897%, each with full evidence refs (curve
  id, doc sha, page); 21 candidates: 9 eligible / 11 ineligible / 1
  unknown. Matches M4's 2026-10-14 findings.
- **Admission dependency demonstrated:** cohort restricted to
  TPS54360+LM5005 → both hard-eligible, `evidence_rows_attached: 0` —
  my delivered TI rows (`363bc4f1`) await M4's next bundle cut; SiC46x
  serving on released parts awaits cr-core's admission (packet
  delivered). Coordination mail sent to m4opencode.
