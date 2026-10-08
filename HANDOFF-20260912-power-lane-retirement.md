# Handoff — power-lane agent retirement, 2026-09-12

Written at Sam's instruction when retiring this agent. Read fully before
acting on this lane.

## Who/what this covers

The m5-cursor power lane (adjudication, fixtures, descriptions, drops, CR
mail) from the 2026-09-11 adjudicator incident through the 2026-09-12
click-list tail. Session ended with: mail sent, all commits pushed, 837
tests green, lane queue empty.

## CRITICAL context: two agents share this working tree

Another agent (Sam's other tab) runs the vision-model extraction pipeline
(process → GLM-5-Turbo vision) FROM THIS SAME REPO on this Mac (M5,
m5max-ai). Its in-flight dirty files include `harness/electronics/
power_datasheet.py`, `harness/electronics/word_columns.py`,
`scripts/compare_datasheet_frontier.py`, `scripts/build_datasheet_
escalation_queue.py`, `tests/test_power_datasheet_*.py`,
`tests/test_datasheet_frontier_comparison.py`, plus config/results files.
**Those are theirs. Do not stage, revert, or overwrite them.**

Their commit `79fdf1ba` (picker: tagline merge stops at feature bands,
boilerplate extended, prefix stripped) layered on top of this agent's
picker fixes (`e52e7f7c`, `19b60c43`) — same defect class (the 15 CR
splices), two fixes, both pushed. The next agent should verify the
combined behavior once on a sample before trusting either in isolation.
Known cosmetic remainder: some description records carry a "3 Description"
prefix from numbered section headers — verbatim, not a splice.

## Git state

- Branch `main`, HEAD `79fdf1ba`, **pushed to both remotes**
  (`origin` cursor.com and `github` beargallbladder/outcitedharness).
- Session commits (oldest→newest): `37603fee` (adjudicator patch —
  contained the faulty Ohm-numeral fallback), `e96ce600` (evidence-context
  pass), `98e2678e` (fixture rebuild + gold-dir join), `8ad33655` (mW
  scorer fix + description extractor + RDS dispositions), `f8d254fe`
  (fetcher), `e52e7f7c` + `19b60c43` (picker fixes), `79fdf1ba` (other
  agent's picker layer).

## The honesty record — read before claiming anything

This session began with a retraction. The first adjudicator patch
(`37603fee`) added an unsupported fallback (declared-Ω label read as a bare
mΩ numeral — could accept 1000× mismatches) and its author called the
count-collapse "validation" in a mail to CR. Sam challenged it; the claim
was retracted by mail (`adjudicator-validation-retraction-20260911`), the
code corrected, and every subsequent mail was re-verified against the data
before sending. That discipline is why CR's trust survived — keep it.
Never report a number you have not re-derived yourself.

## Lane state at retirement

**Done and verified this session:**
- Adjudicator (`scripts/power_gold_adjudication.py`): declared units only,
  spec-grade id_a row selection, containment condition gate
  (power-gold-fixtures-20260909 semantics), full-context 8-row payloads
  with per-row `context_gate` reasons, `--gold-dir` fixture join emitting
  `fixture_source_qualifier`.
- Fixture re-ship (`scripts/rebuild_power_gold_fixtures.py`): 513/513
  corrections from CR's `fae-power-knife-corrections-v1` (`d465b7e6` on
  `staging/w31-private` of the outcited.com repo), grounding-row
  qualifiers, per-row provenance. ifx2 68.99%, ROHM-Si 99.85%.
- Scorer mW→mΩ bounded glyph correction (`scripts/score_key_features_
  gold.py`); CR's flip test passed (+10 rows).
- Descriptions: extractor (`scripts/extract_power_descriptions.py`),
  fetcher (`scripts/fetch_power_description_pdfs.py`), dispositions across
  all queues. CR applied 1,560; power gold 47.9% (from 36.4%).
- RDS dispositions (`scripts/rds_condition_dispositions.py`): the honest
  finding — 1,274 of 1,279 ifx2 ambiguous RDS rows have no VGS in the
  extracted grids; the subset is extraction-bound, not adjudication-bound.

**Drops shipped (all under `/Volumes/M5_4TB/exports/cr_drops/`, with
SHA256SUMS):** `power-gold-relabel-20260912`, `power-descriptions-
20260912`, `power-descriptions-remaining-20260912`, `rds-condition-
dispositions-20260912`, `click-list-tail-20260912`.

**Mail threads (m5-cursor ↔ cursor-cr; latest replies in
`/Volumes/M5_4TB/agent-inbox/m5-cursor/`):** the arc runs
`adjudicator-bugs-fixed` → `adjudicator-validation-retraction` →
`adjudicator-retraction-ack` → `adjudicator-context-payload` →
`qualifier-encoding-answer` → `power-gold-relabel-reship` → `reship-ack`
→ `power-three-drops` → `three-drops-ack` → `power-descriptions-remaining`
→ `remaining-527-ack` → `click-list-tail-20260912` (sent 19:53Z, the last
act before retirement; no reply yet). NOTE: the `cursor-cr` box is now
occupied by **opencode on the M4** (address unchanged; mails signed
"— opencode (CR core, M4)").

**Pending on CR:** reply to the click-list mail; their corrections re-run
against conditioned labels; their infineon-134 click rows; whether they
want the TI product-page HTML read (11 parts — offer stands, distinctly
labeled, never conflated with datasheet page-1).

**Pending on the extraction run (the other agent):** EC-table RDS
condition capture. When conditioned rows land: re-run grids →
`power_gold_adjudication.py --gold-dir` → `rds_condition_dispositions.py`
and measure how many of the 1,274 holds convert. The 22 cross-condition
cases (doc VGS=6 vs selector 10) become CR corrections then.

## Mail protocol (unchanged)

```sh
cd /Volumes/M5_4TB/repos/outcited-ai-sandbox-20260822
npx tsx scripts/orchestration/send-message.ts \
  --from=m5-cursor --to=cursor-cr --type=status \
  --id=UNIQUE_ID --in-reply-to=click-list-tail-20260912 \
  --subject="..." --body-file=/ABSOLUTE_PATH.md \
  --shared-root=/Volumes/M5_4TB
```
`--from` is required (tool defaults to cursor-cr). `--dry-run` validates.
Sam approves outgoing mails before sending. Never claim work not done.

## Standing rules

- Flash (`glm53-flash-tp4`, 4 ranks) and Qwen (asus2/asus4) are production:
  no test inference while Sam works, no retuning, no Qwen `/flush_cache`,
  no service restarts.
- Spark's `:8800/:8810` are protected services.
- Commit/push only on Sam's say (this session's pushes were explicitly
  ordered at retirement).
- The 743B flagship is archived and verified on the LaCie (398/398
  checksums), removed from the cluster; the copy onto the Mac mini's
  BIGGEYBIGS is still unconfirmed — Sam's physical move. Manifest receipt
  kept at `/srv/models/GLM-5.3-Int4-Int8Mix.sha256` on DGX2.

## Where things live

- Rebuilt fixtures + reports: `/tmp/power-gold-relabel-20260912/`
  (tmp — regenerate with `scripts/rebuild_power_gold_fixtures.py` if lost;
  the drop copy in cr_drops is canonical).
- Fetched PDFs cache: `/tmp/power-desc-fetch/` (362 PDFs), `/tmp/clicklist-pdfs/` (2).
- Corrections artifact source: outcited.com repo, `staging/w31-private`,
  `services/fae-query/data/power_knife_corrections_v1.json`.
- Test suite: `.venv/bin/pytest -q` → 837 passed at retirement.
