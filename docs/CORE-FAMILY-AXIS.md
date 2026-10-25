# CORE-FAMILY-AXIS-01 — core_family / core_bits lane

**Law:** extraction law on harness `main` @ `b00584f5` (local-first full
document, deterministic before models, span-quotes mandatory). This lane is
Stage 1 only: pure local regex over the PDF text layer, **zero model calls**.
It generalizes the identity_stamp page-1 rules (TI/ST/Microchip/Espressif
title lines, 83.5% stamp rate) into a first-class claim.

## Output contract

`harness/electronics/core_family.py` emits one row per printed axis:

```json
{
  "schema": "harness.electronics-core-family.v1",
  "field": "core_family",
  "value": "cortex-m4",
  "core_bits": 32,
  "page_1based": 1,
  "verbatim": "32-bit Arm Cortex-M4F MCU with ...",
  "span": "Arm Cortex-M4F",
  "evidence_source": "page_1",
  "pdf_sha": "<sha256 of the PDF bytes>",
  "part_number": "STM32H743",
  "method": "deterministic_core_family_v1",
  "verdict": "printed_row"
}
```

- `field=core_family`: `cortex-m0`, `cortex-m0+`, `cortex-m3`, `cortex-m4`,
  `cortex-m7`, `cortex-m23`, `cortex-m33`, `cortex-m55`, `cortex-m85`, `avr`,
  `pic`, `risc-v`, `xtensa`, `msp430`.
- `field=core_bits`: `8` / `16` / `32`, only when printed adjacent to the
  family claim or to a device word (MCU / CPU / microcontroller / core).
  Peripheral widths (`16-bit timers`, `32-bit multiplier`, `10-bit ADC`) never
  emit.
- No family token printed ⇒ no `core_family` row. `Unknown` never eliminates a
  candidate; bare `Cortex-M`, `ESP32-S3` without `Xtensa`, and vendor
  boilerplate (`TI 16-bit and 32-bit microcontrollers`) never invent one.
- Comparative prose (`40x faster than Arm Cortex-M0+`) is rejected — on an
  MSP430 datasheet that is a benchmark, not this part's core.

`verbatim` is an exact line from the page text, `span` is the matched token
inside it, and `pdf_sha` + `page_1based` make every claim L2-verifiable.

## Queue wiring (same contract as the thermal lane)

The queue is the shape `m5_wave_runner.sh` already builds:

```json
{"documents": [{"source_path": "/Volumes/M5_4TB/vault/cas/ab/.../ti-STM32H743.pdf",
                "part_number": "STM32H743"}]}
```

Run on a box that holds the corpus (one lane, full documents):

```bash
python3 scripts/extract_core_family.py \
  --queue /tmp/m5-queue.json \
  --pdf-root /Volumes/M5_4TB \
  --out results/<state>/core-family-w.jsonl \
  --workers 12 --doc-timeout 120
```

Measurement pass (writes nothing but the lane rows and prints the stamp rate):

```bash
python3 scripts/extract_core_family.py \
  --queue /tmp/core-family-measure-queue.json \
  --pdf-root /Volumes/M5_4TB \
  --out /tmp/core-family-measure.jsonl \
  --workers 12
```

The stdout summary is the measured stamp rate with the identity_stamp
baseline alongside:

```json
{"docs": 2, "docs_scanned": 2, "docs_with_claims": 2, "stamp_rate": 1.0,
 "rows": 4, "baseline_identity_stamp": 0.835,
 "claims": {"core_bits=16": 2, "core_family=msp430": 2}}
```

## Measured evidence (2026-10-25, mac-mini)

The mac-mini has no corpus mount (`/Volumes/M5_4TB` absent) and this is a
code lane, not the fleet runtime. The only local datasheets are the curve
fixtures:

| lane | docs | docs_with_claims | stamp_rate |
|---|---|---|---|
| `core_family` | 2 | 2 | 1.000 |
| `identity_stamp` (same sample) | 2 | 1 | 0.500 |

The two fixtures are real TI MSP430 datasheets. `identity_stamp` misses
`msp430g2744.pdf` because the PDF's web chrome (Product / Folder / Sample &
Buy) pushes the title line past its six-line head; the core-family lane scans
the full document and finds it. The FR600x page-1 line
`Up to 40x faster than Arm Cortex-M0+` is correctly **not** claimed as a
Cortex-M0+ part.

This n=2 sample is a smoke, not the corpus rate: the corpus stamp rate versus
the 83.5% identity_stamp baseline is **UNKNOWN** on this box (no PDF vault)
and must be produced by the measurement command above on a box with the
canonical store.

## Fleet rollout

1. **Per-box pass (today):** run the queue command on each box over the same
   document queue the thermal lane consumes; write
   `core-family-<box>.jsonl`. Parallel with thermal; same worker/timeout
   flags.
2. **Merge:** add `core-family` to the wave merge's accepted facets
   (`scripts/wave_merge.py` / `facet_wave_orchestrator.sh` `FACETS`), then the
   existing merge law (ok-facet, dedupe by sha) applies unchanged.
3. **Registry:** axis registration (`core_family`, `core_bits`) in
   `dw-knives` is tracked by the separate PACKAGE-TEMP-AXES bead; this bead
   ships the extractor and its claim contract only.

## Tests

```bash
python3 -m pytest tests/test_core_family.py -q
```

Covers every family class, FPU suffix normalization, adjacent bit capture,
peripheral-width rejection, comparative-prose rejection, page/verbatim
provenance, page-1 collapse, dual-core documents, and the real FR600x
fixture.
