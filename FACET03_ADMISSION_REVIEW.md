# FACET03 ADMISSION REVIEW — owner packets & verification breakdown

Prepared for owner review per directive 2026-10-08. **Nothing here is
approved**: no family membership promoted, no embeddings run, no catalog
authority changed, no merge performed.

Branch `facet/03-family-provenance` pushed to origin at
`48afc3427d380031bc0a8b996932a360435be008` (verified: assignment == HEAD,
clean worktree, exact authorized commit).

## 1. The four review packets

`/Volumes/M5_4TB/extract-results/facet03-packets/review/`

| Packet | Members | Exceptions | File |
|---|---|---|---|
| OptiMOS | 243 | 0 | family-optimos-review-v1.json |
| CoolMOS | 207 | 0 | family-coolmos-review-v1.json |
| CoolSiC | 127 | 0 | family-coolsic-review-v1.json |
| StrongIRFET | 49 | 0 | family-strongirfet-review-v1.json |
| **Total** | **626** | **0** | |

Every member record carries: canonical parent id
(`family:mfr:infineon:<label>`), canonical child id (`opn:<OPN>`),
membership status (`proposed`), norm rule (`front_matter_vocab_v1`),
confidence, **source SHA-256 (byte-verified), printed page, and the
match-centered verbatim quote containing the family identifier**.

Evidence-capture fix during packet prep: the first packet cut quoted lines
at 200 chars and 3 StrongIRFET quotes did not show the family token inside
the excerpt. Capture is now a match-centered window (±120/200 chars), the
backfill re-ran (idempotent), and all 626 quotes demonstrably contain
their family identifier — 0 exceptions across all four packets.

## 2. Quote-verification breakdown (the 5,760)

**SHA recovery is NOT quotation verification — measured separately.**
Runtime strict check (per-page, whitespace-normalized substring): 37,247 /
43,007 verified (86.6%); 5,760 failed strict. Full-range re-analysis of
every failed quote against the complete substrate text:

| Class | Count | Meaning | Disposition |
|---|---|---|---|
| verified on wide-range pass | 5,178* | strict per-page scan missed cross-page/window matches | content-verified |
| normalization gap | 404 | present after aggressive fold (°, µ, intra-token spaces: "V GS=10 V" vs "VGS=10 V") | extraction-normalizer artifact; recoverable, NOT auto-accepted |
| absent in substrate | 58 | genuinely not found (multi-line table-cell quotes flattened differently by the extractor) | remain quote-unverified; units keep sha but no page recovery → discovery-only where pageless |
| stem unresolved | 120 | quotes belonging to the 5 unresolved stems — nothing to check against | discovery-only |

*43,127 wide-range total − 582 residual failures; the strict/wide delta is
scan-scope, not new evidence. Bottom line: **58 quotes are genuinely
absent from substrate text and 120 are unverifiable** — those claims are
never page-recovered and never grade-promoted on sha alone.

## 3. Rekey integrity verification (87,543 units)

- **Supersedes 1:1**: retired = 87,543 (85,669 claims + 1,874 families),
  all with `retired_reason = provenance-resolved to <sha12>`; active
  rekeyed (`+prov1` versions) = 87,543. Every retired doc (1,911) has a
  byte-verified provenance_map row; 0 orphans.
- **Locator integrity 100%**: all 85,669 rekeyed claims match a retired
  original EXACTLY on {quote, row_header, column_header, text_repr}; the
  only locator additions are `origin_stem`, recovered `page`, and
  `page_source=quote_locate_v1`. (An earlier 88/300 "mismatch" sample was
  a crude LIKE-prefix matcher artifact — exact-match full-count check: 0
  mismatches.)
- **Rollback**: search-index release history is intact and append-only
  (…351682faa7c6 → d659c106df6e → 8ce00bbf318b → 8caf6bea2b32 →
  d36e957831d5); pre-rekey units remain retrievable via
  `include_retired=true` (verified live); restoring a prior db file or
  re-indexing from catalog reproduces prior behavior — rollback test 12
  green in the suite.
- **Correction to the release report**: evidence-grade share is
  **315,318 / 356,061 = 88.6%** of active units. The previously reported
  90.8% used a mid-state denominator (346,919) captured between rekey
  runs. Total rows 443,604 = 356,061 active + 87,543 retired (history
  preserved, never deleted).

## 4. Unresolved stems — preserved discovery-only

`BQ2000T, BQ24003, BQ24008, BQ24071, BQ24401` (TI battery
management; no pairs-manifest entry, no vault match). Active units:
36/97/95/58/36 = 322, all `unhashed:`, all discovery-only by
construction (evidence gate), none retired, none guessed. Recovery path:
mac-mini re-acquisition or vault ingest, then re-run the resolver.

## 5. Risks for the owner weighing admission

1. **Vocabulary-bounded families**: v1 recognizes 6 Infineon tokens only.
   A part whose datasheet prints a family v1 doesn't know is `unknown` —
   safe direction (no false members), but coverage ceiling ≈ 48% of the
   power cohort (Infineon share) until vocabulary/TI/Rohm authority lands.
2. **Front-matter scope**: tokens are matched on pages 1-2 only; a family
   named only in deep contents pages is missed (again: unknown, not wrong).
3. **Quote capture window**: ±120/200 chars around the match; owners
   approving from packets see the window, and the full page text is
   retrievable via the source sha + page.
4. **Single-source memberships**: every relationship rests on one
   document's front matter (by design — it is the manufacturer's own
   statement). Cross-document corroboration exists for multi-source parts
   but is not required for proposal.
5. **0 conflicts observed** — but conflict machinery is tested (both rows
   kept, neither wins); first real conflict will surface as
   `conflicting_membership` exception in packets, never silent merge.

## 6. Owner-approval checklist (explicit)

For each of the four packets:

- [ ] Spot-check ≥5 members per family: open source PDF at recorded sha
      (vault/cas/<sha[:2]>/<sha>.pdf or pairs pdf dir), confirm the quoted
      line on the recorded page names the family for that OPN.
- [ ] Decide per-family: approve all / approve subset / reject.
- [ ] For approvals, issue an approval reference (mail id or decision
      log entry). Promotion is then mechanical and auditable:
      `identity.promote(con, family_id, approval="<ref>")` — the ONLY code
      path to `verified`; it writes the reference into meta.
- [ ] Decide whether approved families may gate UI display (status filter
      `verified`-only) or remain labeled `proposed` in the pilot.
- [ ] Explicitly NOT part of this approval: catalog.db family-column
      writes (extraction-lane authority), new embedding runs, ANN
      infrastructure, merge of any branch, production routing.

## 7. Directive compliance

- [x] branch/assignment/worktree/commit verified before push
- [x] pushed to origin, remote SHA confirmed; no merge; no production change
- [x] four packets with evidence, sha, page, quoted identifier, canonical ids, exceptions
- [x] 5,760-quote breakdown produced; sha-recovery ≠ quote-verification honored
- [x] 87,543 rekeyed units: supersedes 1:1, locators exact, rollback demonstrated
- [x] 5 unresolved stems preserved discovery-only
- [x] no approvals granted, no embeddings run, catalog authority untouched
