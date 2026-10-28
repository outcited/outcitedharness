# Quote-Failure Disposition — 2026-10-28T23:58:11+00:00

System of record: GitHub issue `outcited/outcitedharness#5` (phase 2).

This artifact is ANALYSIS ONLY. It preserves every original failed source record verbatim and adds disposition fields alongside. No stored claim was repaired.

## Summary

- EXTRACTION_ARTIFACT: 9

## Dispositions

| # | opn | page | value | disposition | detail |
|---|---|---|---|---|---|
| 1 | infineon-imbg40r036m2h-datasheet | 4 | 40.0 C/W | EXTRACTION_ARTIFACT | pipe_as_space (column delimiter -> whitespace) |
| 2 | infineon-ipp019n08nf2s-datasheet | 3 | 40.0 C/W | EXTRACTION_ARTIFACT | cell_token_containment (every cell token occurs on the page; permits column-run reading order, wrapped-line joins and repositioned footnote markers) |
| 3 | infineon-irfp260n-datasheet | 1 | 0.5 C/W | EXTRACTION_ARTIFACT | cell_containment (each '/'-delimited cell is a contiguous run of the line-folded page text; cells may live in separate column runs / wrapped lines) |
| 4 | infineon-ipp030n10nf2s-datasheet | 3 | 0.6 C/W | EXTRACTION_ARTIFACT | cell_token_containment (every cell token occurs on the page; permits column-run reading order, wrapped-line joins and repositioned footnote markers) |
| 5 | infineon-ipa65r045c7-datasheet | 4 | 3.6 C/W | EXTRACTION_ARTIFACT | pipe_as_space (column delimiter -> whitespace) |
| 6 | infineon-irfp260m-datasheet | 1 | 0.5 C/W | EXTRACTION_ARTIFACT | cell_containment (each '/'-delimited cell is a contiguous run of the line-folded page text; cells may live in separate column runs / wrapped lines) |
| 7 | infineon-bsc093n15ns5sc-datasheet | 3 | 50.0 C/W | EXTRACTION_ARTIFACT | cell_token_containment (every cell token occurs on the page; permits column-run reading order, wrapped-line joins and repositioned footnote markers) |
| 8 | infineon-imbf170r650m1-datasheet | 3 | 62.0 C/W | EXTRACTION_ARTIFACT | cell_token_containment_footnote_folded (as L3, with footnote markers 'N)' stripped from page and cell tokens before comparison — the extractor may attach or separate a footnote marker from the token it annotates) |
| 9 | infineon-ipd029n04nf2s-datasheet | 4 | 50.0 C/W | EXTRACTION_ARTIFACT | pipe_as_space (column delimiter -> whitespace) |

## Per-failure evidence (originals preserved verbatim)

### 1. infineon-imbg40r036m2h-datasheet (page 4)

- disposition: **EXTRACTION_ARTIFACT**
- quote: `RthJA | ‑ ‑ | 40 | °C/W`
- evidence: `{"cells": null, "level": "L1", "normalization": "pipe_as_space (column delimiter -> whitespace)"}`

### 2. infineon-ipp019n08nf2s-datasheet (page 3)

- disposition: **EXTRACTION_ARTIFACT**
- quote: `RthJA | Thermal resistance, junction - ambient, 2) 6 cm² cooling area | - | 40 | - | °C/W`
- evidence: `{"cells": ["rthja", "thermal resistance, junction - ambient, 2) 6 cm2 cooling area", "-", "40", "-", "\u00b0c/w"], "level": "L3", "normalization": "cell_token_containment (every cell token occurs on the page; permits column-run reading order, wrapped-line joins and repositioned footnote markers)"}`

### 3. infineon-irfp260n-datasheet (page 1)

- disposition: **EXTRACTION_ARTIFACT**
- quote: `RJC | Junction-to-Case | ––– | 0.50 | °C/W`
- evidence: `{"cells": ["r\uf071jc", "junction-to-case", "\u2013\u2013\u2013", "0.50", "\u00b0c/w"], "level": "L2", "normalization": "cell_containment (each '|'-delimited cell is a contiguous run of the line-folded page text; cells may live in separate column runs / wrapped lines)"}`

### 4. infineon-ipp030n10nf2s-datasheet (page 3)

- disposition: **EXTRACTION_ARTIFACT**
- quote: `RthJC | Thermal resistance, junction ‑ case Thermal resistance, junction ‑ ambient, 5) 6 cm² cooling area Thermal resistance, junction ‑ ambient, minimal footprint | ‑ | 0.6 | ‑ | °C/W`
- evidence: `{"cells": ["rthjc", "thermal resistance, junction \u2010 case thermal resistance, junction \u2010 ambient, 5) 6 cm2 cooling area thermal resistance, junction \u2010 ambient, minimal footprint", "\u2010", "0.6", "\u2010", "\u00b0c/w"], "level": "L3", "normalization": "cell_token_containment (every cell token occurs on the page; permits column-run reading order, wrapped-line joins and repositioned footnote markers)"}`

### 5. infineon-ipa65r045c7-datasheet (page 4)

- disposition: **EXTRACTION_ARTIFACT**
- quote: `RthJC | - - | 3.6 | °C/W`
- evidence: `{"cells": null, "level": "L1", "normalization": "pipe_as_space (column delimiter -> whitespace)"}`

### 6. infineon-irfp260m-datasheet (page 1)

- disposition: **EXTRACTION_ARTIFACT**
- quote: `RJC | Junction-to-Case | ––– | 0.50 | °C/W`
- evidence: `{"cells": ["r\uf071jc", "junction-to-case", "\u2013\u2013\u2013", "0.50", "\u00b0c/w"], "level": "L2", "normalization": "cell_containment (each '|'-delimited cell is a contiguous run of the line-folded page text; cells may live in separate column runs / wrapped lines)"}`

### 7. infineon-bsc093n15ns5sc-datasheet (page 3)

- disposition: **EXTRACTION_ARTIFACT**
- quote: `RthJA | Thermal resistance, junction ‑ case, bottom Thermal resistance, junction ‑ case, top Thermal resistance, junction ‑ ambient, 2 4) 6 cm cooling area | ‑ | 50 | ‑ | K/W`
- evidence: `{"cells": ["rthja", "thermal resistance, junction \u2010 case, bottom thermal resistance, junction \u2010 case, top thermal resistance, junction \u2010 ambient, 2 4) 6 cm cooling area", "\u2010", "50", "\u2010", "k/w"], "level": "L3", "normalization": "cell_token_containment (every cell token occurs on the page; permits column-run reading order, wrapped-line joins and repositioned footnote markers)"}`

### 8. infineon-imbf170r650m1-datasheet (page 3)

- disposition: **EXTRACTION_ARTIFACT**
- quote: `Rth(j-a) | Thermal resistance, 1) junction-ambient | 62 | K/W`
- evidence: `{"cells": ["rth(j-a)", "thermal resistance, 1) junction-ambient", "62", "k/w"], "level": "L3b", "normalization": "cell_token_containment_footnote_folded (as L3, with footnote markers 'N)' stripped from page and cell tokens before comparison \u2014 the extractor may attach or separate a footnote marker from the token it annotates)"}`

### 9. infineon-ipd029n04nf2s-datasheet (page 4)

- disposition: **EXTRACTION_ARTIFACT**
- quote: `RthJA | ‑ ‑ | 50 | °C/W`
- evidence: `{"cells": null, "level": "L1", "normalization": "pipe_as_space (column delimiter -> whitespace)"}`

