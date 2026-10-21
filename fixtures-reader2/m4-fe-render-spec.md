# M4 FE #72 render spec — spine-rc1 pinned fixtures (authenticated private test)

Fixtures (release-pinned, sha in rc manifest):
1. cmp-mcu.json    — NXP K22 vs Atmel SAM4N — expect 4 axes labeled "comparable"
2. cmp-power.json  — TLE9850 vs MPC561 — expect 5 axes incl. RθJA "comparable"
3. cmp-refuse.json — GD32F207 vs ATtiny104 — expect "QUALIFIED — VDD differs (3.3 vs 5.0)",
   NEVER rendered as directly comparable.

Required behaviors:
- Condition labels: comparable / qualified + reason / noncomparable — exact strings from fixtures.
- Honest unknowns: absent conditions render "conditions unspecified", never blank.
- Source evidence: every displayed value must click-through to {verbatim, page, pdf_sha}
  from the fixture/claim provenance chain (source-click proof).
- Unknown data renders as missing, never zero.

Acceptance receipt per fixture: signed JSON {fixture, expected, actual, verdict, owner, ts}
returned to agent-inbox. Status: PENDING M4 FE owner.
