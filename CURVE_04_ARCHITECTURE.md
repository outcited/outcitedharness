# CURVE-04 — architecture, integration decisions, and gaps

Branch: `curve/04-adjudication-raster` (worktree `/Users/samkim/Harnessv1-curve04`),
base `audit/curve-evidence-v1@28772cf1`. No other agent's branch, worktree,
or uncommitted files were modified.

## R0 — reconnaissance findings

- **CURVE-02/03** live at `harness/electronics/curve_evidence.py`,
  `harness/discovery/curves.py`, pilot fixtures
  (`tests/fixtures/gold/curve_evidence_pilot/`), scorer, experiment runner,
  and the CURVE-03 review-packet tooling. All reused; none rebuilt.
- **Evidence retrieval** (PRD-SEARCH-01) is committed in the OTHER
  worktree at `search/evidence-retrieval-v0@c3f51b07`: an evidence-unit
  store (`harness/search/units.py`; sqlite + FTS5 + trigram; 91,718
  units; releases pinned in `search_index.db`, latest
  `search-release-v1-71adcae3c83f` — the PRD's
  `search-release-v1-9c07373d690d` was not found in `releases`; the
  pinned release present is the `71adcae3c83f` digest, recorded here for
  honesty). Its laws — provenance mandatory, coverage_kind end-to-end,
  verification recorded not minted — are the integration contract.
- **Reusable vs conflicting:** the unit store's grain/locator/provenance
  vocabulary maps cleanly onto curve evidence (figure grain, page+
  bbox+series locator). The conflict is operational: writing into the
  live `search_index.db` from an unapproved lane would mutate another
  agent's release-governed index.

**Decision:** CURVE-04 integration is a *separate flag-gated index*
(`harness/electronics/curve_retrieval.py`, own sqlite DB) that adopts the
unit store's laws without writing its database. When CURVE-04 graduates,
the same row shape can be emitted as figure-grain units through the
search indexer — a documented migration path, not a silent write.

## R1 — adjudication architecture

`harness/electronics/curve_adjudication.py` + `scripts/adjudicate_curves.py`
+ `results/curve-adjudication/` (81 packets, all `human_review_pending`,
reviewer sign-off count 0 — no simulated approval).

- **Packet**: one immutable record per curve series with every PRD field:
  doc SHA + revision, page/figure locator, render path + render SHA,
  bbox, series identity + legend binding, axes/units/scales, coordinates,
  conditions/applicability/uncertainty, algorithm + version,
  machine-verification results.
- **Content addressing**: `packet_hash = sha256(canonical_json(content))`
  via `packet_content_hash`; `verify_packet_integrity` recomputes and
  compares, so any material edit is detectable and prior approvals do not
  transfer (tested).
- **Ledger**: append-only JSONL; every entry (including raw `append`) is
  validated against the state machine — machine states need an actor,
  human states need a named reviewer AND a legal transition (forged
  raw appends raise; tested).
- **States**: `machine_verified → human_review_pending →
  human_approved | human_rejected | needs_rework`; machine verification
  can never reach a human state (tested). Effective state is replay-
  derived, never stored on the packet.
- No visual reviewer was available in this environment (no image input);
  packets + renders are review-ready and approval stays pending, per the
  PRD's own fallback.

## R2 — challenge acquisition

Vault-first inspection (all delivery manifests, `datasheet_cache`,
`datasheet_text_cache`), then network attempts with recorded timestamps:
analog.com unreachable from this environment (curl 000), infineon.com
answers a CloudFront bot challenge (HTTP 202, zero bytes). Outcome
(`results/curve-04-challenges/challenge_manifest.json`): LT8292, IR3883,
IR3447, ADP5003 = recorded coverage gaps with failure reasons; TLS4125D0EPV
held (both revisions, SHAs recorded) and partially recovered by the raster
lane below.

## R3 — raster recovery lane

`harness/electronics/raster_curves.py` (numpy + Pillow + tesseract):

- targeted page rendering at arbitrary DPI; line-run frame detection with
  exact-rectangle topology; candidate frames compete on tick-fit quality
  (parents mixing two plots' ticks fail the fit; nested shading boxes are
  smaller);
- OCR tick reading (digit-whitelisted, confidence-floored, 2× upscale),
  monotone-subsequence filtering + robust single-outlier dropping for
  misreads (100→400), linear and log10 fits;
- hue-clustered trace segmentation (anti-aliasing-robust), legend
  swatches excluded by width coverage;
- per-plot uncertainty (fit residuals as fraction of span, trace
  thickness) and source-image SHA in every record; refusals carry
  specific reasons.

**Resolution experiment** (ground truth = the vector references of the
same page, `results/curve-04-raster/resolution_experiment.json`):

| DPI | plots recovered | series paired | y-err median | p95 | max | time |
| --- | --- | --- | --- | --- | --- | --- |
| 200 | 4/6 | 8/24 | 2.35 % | 34.5 % | 112 % | 1.8 s |
| 300 | 5/6 | 10/24 | 1.91 % | 18.4 % | 116 % | 8.2 s |
| 600 | 2/6 | 4/24 | 1.45 % | 44.1 % | 52.0 % | 13.7 s |

Quantified conclusion (not assumed): **300 DPI is the operating point**
(coverage × accuracy × cost); 600 DPI improves median error but loses
plots to tick-OCR failures at higher glyph fragmentation and costs 1.7×;
200 DPI loses accuracy. Tails (p95/max) come from rank-based
trace-pairing across substrates — reported, not hidden. TLS4125D0EPV p.18
recovers 2 of 4 printed efficiency plots at both 300 and 600 DPI (the
held raster challenge); the rest refuse on tick-label confidence.

Raster outputs are machine references only: identical adjudication gates,
`raster` provenance algorithm string, no CURVE-02 law bypassed (tested).

## R4 — retrieval integration

`harness/electronics/curve_retrieval.py` behind
`CURVE_RETRIEVAL_ENABLED=1` (default off; flag-off = full rollback,
tested). Self-contained sqlite index (59 curves from the frozen pilot;
points/axes/conditions/provenance stored, fixtures not needed at query
time). Match classes explicit: `exact | interpolated |
not_comparable(reason) | retrieved`; incomplete-provenance records are
refused at index time; adjudication state travels on every row; typical
never guaranteed (`guarantee: false` asserted); extrapolation refused
with reason (tested). Rollback: flag off or delete the DB — both paths
return explicit statuses (tested).

## R5 — ablation

`scripts/run_curve_ablation.py` → `results/curve-04-ablation/ablation.json`.
Six fixed questions over the frozen cohort; A (parametric) vs B
(parametric + curves). Eligibility identical across A/B on every question
(curves never eliminate — asserted), 0 unsupported claims, refusals
correct on all three no-defensible-comparison cases, latency measured
(A 0.05 ms vs B 4.8 ms total). Expert shortlist relevance: recorded
**PENDING independent review**, never self-graded.

## Gaps and next actions

1. Human visual review of the 81 packets (renders included) — the only
   path to human_approved gold.
2. Network-blocked acquisitions (LT8292/IR3883/IR3447/ADP5003) — retry
   from an unfiltered environment; LT8292 unlocks real log-axis gold.
3. Retrieval migration into the search indexer (figure-grain units) once
   review signs off — contract documented above.
4. Raster legend association currently leaves series names null; legend
   OCR is the next lane improvement (trace-pairing tails shrink with it).
