# FACET03 SERIES SIGNOFF — owner review package (15 packets, 237 memberships)

Prepared 2026-10-09 per owner ordering ("make the 15 series packets your
next owner gate"). **Nothing promoted.** Same contract as the family
admission: `promote_packet(family_id=series_id, expected_packet_sha256,
expected_population, approval_ref)` — signature binds to exact bytes.

## What is being decided

237 of the 626 verified family memberships additionally carry a printed
series designator (CoolMOS C7/CFD2/CFD7/CFD7A/CFDA/CM8/CPA/G7, CoolSiC G2,
OptiMOS 2/3/5/6/7/8, StrongIRFET 2). Approval moves those from
family-only identity to family + series identity. The 387 memberships
without a printed designator stay honestly unknown; the 2 ambiguous
extractions stay out entirely (never guessed).

Evidence standard is identical to the admitted families: the designator is
captured from the SAME byte-verified quote (sha + page + verbatim text)
that earned the family membership — no new sources, no OPN-string parsing
(case-sensitive all-caps capture with closed interstitial vocabulary;
prose like "CoolMOS is a revolutionary technology" cannot match).

## Packet hashes (pinned identity for promote_packet)

| Packet | SHA-256 (prefix) | Members |
|---|---|---|
| series-coolmos-cfd2-review-v1.json | `6cf4e4d2de12da53` | 6 |
| series-coolmos-cfd7-review-v1.json | `11b25d151d19d854` | 5 |
| series-coolmos-cfd7a-review-v1.json | `bb5968a170836625` | 5 |
| series-coolmos-cfda-review-v1.json | `50ee90d5dc7d3328` | 4 |
| series-coolmos-cm8-review-v1.json | `7273438f8d384f88` | 18 |
| series-coolmos-cpa-review-v1.json | `e0ba947d72b8d92c` | 1 |
| series-coolmos-g7-review-v1.json | `0d7a9ede2484f648` | 2 |
| series-coolsic-g2-review-v1.json | `9bc861de8296c776` | 24 |
| series-optimos-2-review-v1.json | `a70b26df0410d6b0` | 5 |
| series-optimos-3-review-v1.json | `2593463239f49612` | 9 |
| series-optimos-5-review-v1.json | `4c13d3599b6082a0` | 84 |
| series-optimos-6-review-v1.json | `b77e341f486decd7` | 22 |
| series-optimos-7-review-v1.json | `be1524d228e90a3d` | 17 |
| series-optimos-8-review-v1.json | `f1ef9994e0af2f49` | 5 |
| series-strongirfet-2-review-v1.json | `7f246d547bd7d641` | 30 |
| **Total** | | **237** |

Full 64-char hashes + member lists: `facet03-packets/series/*.json`;
verification detail: `series-review-results-v1.json` (same directory).

## Independent sample verification (42 members, source PDFs)

Method (same discipline as the family review): per packet, sample first /
middle / last member; locate the actual PDF; **re-hash its bytes against
the recorded sha**; open it; confirm the series designator is printed on
the recorded page; confirm the OPN is named in the front matter; confirm
the recorded quote appears verbatim.

**Result: 42/42 supported, 0 failures, 0 packet exceptions.**
(The family-review harness defect class — quote truncation — does not
apply: series quotes are the already-reviewed membership quotes.)

## Risks / known limits

1. **Series grain rides family quotes**: a membership whose family quote
   lacks a designator yields no series (387) — coverage ceiling 37.9% at
   this evidence layer, honest.
2. **Interstitial vocabulary is closed** ("MOSFET/Module/Automotive/
   Trench/Power/..."): an unusual printed construction ("CoolMOS, the C7
   generation") would be missed — direction of failure is unknown, never
   wrong.
3. **Numeric-generation series (OptiMOS 5, StrongIRFET 2)** capture only
   the digit printed adjacent to the family token; sub-variants (LS5 vs
   NLS5 suffixes) are OPN-string territory and deliberately NOT parsed.
4. Two ambiguous members (multiple designators in one quote) are excluded
   from all packets.

## Recommendation

**APPROVE all 15 packets.** Evidence standard identical to the admitted
families; sample verification clean; failure modes all fall to unknown.

## Owner decision block

- [ ] Approve all 15 (single reference) — ref: ______________________
- [ ] Or per-packet decisions (list): ______________________
- Suggested reference form: `FACET03-SERIES-APPROVAL-<date>-<commit>`

On signature: promotion runs through `promote_packet` per packet (hash +
population + drift-bound), identity snapshot advances, facets gain the
series dimension automatically, and the admission record lands in
`ADMISSION_DECISION` style beside the family one.

## EXECUTED 2026-10-09 — owner decision recorded

Approval reference: `FACET03-SERIES-OWNER-APPROVAL-2026-10-09-0c23a34d`
(scope: exactly the 237 memberships in the 15 pinned packets; 387 unknowns
and 2 ambiguous untouched).

- [x] All 15 packets promoted via hash-bound `promote_packet` — every
      packet re-hashed at promotion time, population asserted, drift-checked
- **15 series identities verified; 237/237 series→OPN memberships verified**
- 626 family memberships unchanged (still verified)
- 15 family→series structural edges remain `proposed` — outside the signed
  scope (they are hierarchy edges, not memberships); flagged for a future
  owner decision or v2 packet
- Decision record: `facet03-packets/ADMISSION_DECISION_SERIES_2026-10-09.json`

## Catalog re-key EXECUTED 2026-10-09 (owner GO)

- Backup: `catalog.db.bak-pre-rekey-20261009T181224Z` (auto, pre-write)
- Moved to verified shas: **86,406 claims + 1,874 part_sources + 1,874
  parts** — exactly the prepared/tested counts
- Quote integrity: checksum over all 86,736 provenance quotes PRE == POST
  (`3e4f3022ca86…`) — **byte-identical**; identity-only as specified; no
  page injection
- Residual unhashed: **330 claims across 7 stems** =
  the 5 unresolved BQ stems (NULL sha, 324 claims) PLUS 2 stems that are
  sha-resolved + byte-verified but **quote-unverified** (IPP034NE7N3 G:
  0/4 quotes matched substrate; RSH065N06: 0/2) — the conservative
  eligibility bar (quote_verified>0) correctly withheld identity re-key
  where content linkage failed. Both stay discovery-only; investigation
  candidates (missing substrate text or revision drift).
