"""Deterministic CPU core-family claims from printed page text (fill-only).

Generalizes the proven identity_stamp page-1 signals (TI/ST/Microchip/
Espressif title lines, 83.5% stamp rate on the identity lane) from a
document stamp into a first-class ``core_family`` claim. Pure local: regex
over the PDF text layer, zero model calls.

Every row is a printed claim: the exact line is kept as ``verbatim``, the
matched token as ``span``, and the page as ``page_1based``. A core family is
only emitted when the printed text names it — "ARM Cortex-M4", "16-bit AVR",
"RISC-V", "Xtensa", "dsPIC33", "MSP430FR600x" — never inferred from a part
number or a neighbouring document.

Two axes, one pass:

- ``field=core_family``: cortex-m0/m0+/m3/m4/m7/m23/m33/m55/m85, avr, pic,
  risc-v, xtensa, msp430 (measured classes; anything else stays Unknown).
- ``field=core_bits``: 8/16/32 when printed adjacent to the core claim, or
  adjacent to a device word (MCU / CPU / microcontroller / core). Peripheral
  widths ("16-bit timers", "32-bit multiplier", "10-bit ADC") never emit.

A mention without a family token never emits (``Cortex-M`` alone is Unknown).
Comparative prose ("40x faster than Arm Cortex-M0+") is rejected: on an
MSP430 datasheet that sentence is a benchmark against another family, not a
claim about this part.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence


CORE_FAMILY_SCHEMA = "harness.electronics-core-family.v1"
CORE_FAMILY_METHOD = "deterministic_core_family_v1"

# Cortex-M numbers, most specific first (0+ before 0, multi-digit before
# single-digit). The optional F/FP suffix is an FPU indicator, not a family.
_CORTEX_M = re.compile(
    r"(?:ARM[®™\s]*)?Cortex[®™]?[\s\-‑–]*(?:M|m)\s*"
    r"(?P<num>85|55|33|23|7|4|3|1|0\+|0)(?:\s*(?:FPU|FP|F))?(?![0-9A-Za-z])",
    re.I,
)
_RISCV = re.compile(r"\bRISC[\s\-‑–]?V\b", re.I)
_XTENSA = re.compile(r"\bXtensa\b[®™]?", re.I)
_AVR = re.compile(r"\b(?:(?:mega|tiny|classic)\s*)?AVR(?:32)?\b[®™]?", re.I)
_PIC = re.compile(
    r"\b(?:ds)?PIC(?:10|12|14|16|17|18|24|32|33)[A-Z0-9]*\b"
    r"|\b(?:ds)?PIC[®™]"
    r"|\b(?:ds)?PIC\b(?=\s+(?:microcontrollers?|MCUs?|microprocessors?|devices?|family))",
    re.I,
)
_MSP430 = re.compile(r"\bMSP430[A-Z0-9]*", re.I)

_FAMILY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("cortex-m", _CORTEX_M),
    ("risc-v", _RISCV),
    ("xtensa", _XTENSA),
    ("avr", _AVR),
    ("pic", _PIC),
    ("msp430", _MSP430),
)

# Benchmark / competitor prose: the neighbouring family is not this part's.
_CONTRAST = re.compile(
    r"(?:faster|slower|higher|lower|better|worse)\s+than|"
    r"compared\s+(?:to|with)|versus|\bvs\.?\b|competitor|alternative\s+to",
    re.I,
)

# Negation right before a family token: named, but not asserted about this part
# ("do not use MSP430", "not MSP430", "no MSP430 required"). Deliberately narrow
# so a legitimate "MSP430, not MSP432" (family first) is unaffected.
_NEGATION = re.compile(
    r"\b(?:do\s+not\s+use|don'?t\s+use|not|no|without|instead\s+of|"
    r"unlike|exclude|excluding|other\s+than)\s*$",
    re.I,
)

_CORE_BITS = re.compile(r"\b(8|16|32)[\s\-‑–]?bit\b", re.I)
_DEVICE_WORD = re.compile(
    r"\b(?:MCUs?|microcontrollers?|microprocessors?|CPUs?|cores?|processors?|SoCs?|RISC)\b",
    re.I,
)
# Words that turn "N-bit" into a peripheral/data width, not a core width.
_BITS_NOT_CORE = re.compile(
    r"\b(?:ADC|A/D|DAC|timers?|counters?|PWM|registers?|multipliers?|CRC|"
    r"cyclic|redundancy|check|resolution|data|address|bus|memory|flash|RAM|ROM|"
    r"instructions?|pointers?|shift|compare|capture|ports?|GPIO|clock|watchdog|"
    r"AES|encryption|keys?|hardware|sigma|delta)\b",
    re.I,
)

# Vendor boilerplate ("TI 16-bit and 32-bit microcontrollers") describes the
# portfolio, not this part's core.
_BOILERPLATE = re.compile(
    r"\bTI\s+16-bit|Texas Instruments|Product Folder|Copyright|"
    r"All rights reserved|Trademarks?|www\.|SLAS\d",
    re.I,
)

_FEATURES_HEADING = re.compile(
    r"^\s*(?:\d+(?:\.\d+)*\s*)?(?:key\s+)?(?:device\s+)?features?[:\s]*$",
    re.I | re.M,
)
_BULLET = re.compile(r"^\s*[•▪◦‣·*\-–—]\s+")

_EVIDENCE_WEIGHT = {"page_1": 3, "feature_block": 2, "body_text": 1}


def _cortex_family(number: str) -> str:
    return f"cortex-m{number.lower()}"


def _normalized_family(pattern_name: str, match: re.Match[str]) -> str:
    if pattern_name == "cortex-m":
        return _cortex_family(match.group("num"))
    return pattern_name


def _evidence_source(page_no: int, line: str, in_features: bool) -> str:
    if page_no == 1:
        return "page_1"
    if in_features or _BULLET.match(line):
        return "feature_block"
    return "body_text"


def _adjacent_bits(line: str, start: int, end: int) -> int | None:
    """Closest non-peripheral 8/16/32-bit token on the same line."""

    best: tuple[int, int] | None = None
    for match in _CORE_BITS.finditer(line):
        if _BITS_NOT_CORE.search(line[match.end() : match.end() + 24]):
            continue
        if match.end() <= start:
            distance = start - match.end()
        elif match.start() >= end:
            distance = match.start() - end
        else:
            continue
        if distance > 40:
            continue
        bits = int(match.group(1))
        if best is None or distance < best[0]:
            best = (distance, bits)
    return best[1] if best is not None else None


def _bits_claim(line: str, match: re.Match[str]) -> bool:
    """True when a bits token reads as a device core width, not a peripheral."""

    if _BITS_NOT_CORE.search(line[match.end() : match.end() + 24]):
        return False
    after = line[match.end() : match.end() + 24]
    before = line[max(0, match.start() - 24) : match.start()]
    return bool(_DEVICE_WORD.search(after) or _DEVICE_WORD.search(before))


def core_family_rows(
    pages: Sequence[tuple[int, str]],
    *,
    sha256: str | None = None,
    part_number: str | None = None,
) -> list[dict[str, Any]]:
    """Printed core-family / core-bits rows from ``(page_1based, text)`` pages.

    Every emitted row carries ``page_1based`` and a ``verbatim`` line that is
    an exact substring of the page text, plus the matched ``span``. Pages are
    scanned in full-document order; page-1 evidence outranks a feature block,
    which outranks body prose when rows are collapsed.
    """

    rows: list[dict[str, Any]] = []
    for page_no, text in pages:
        text = text or ""
        features = _FEATURES_HEADING.search(text)
        features_start = features.start() if features else -1
        offset = 0
        for raw_line in text.split("\n"):
            line = raw_line.strip()
            line_start = offset
            offset += len(raw_line) + 1
            if not line:
                continue
            in_features = features_start != -1 and line_start >= features_start
            evidence = _evidence_source(page_no, line, in_features)
            emitted_bits: set[int] = set()

            for pattern_name, pattern in _FAMILY_PATTERNS:
                first_by_family: dict[str, re.Match[str]] = {}
                for match in pattern.finditer(line):
                    # Contrast/competitor prose applies to EVERY family, not
                    # just Cortex ("faster than MSP430", "versus PIC").
                    if _CONTRAST.search(line[max(0, match.start() - 48) : match.start()]):
                        continue
                    # Negation: "do not use MSP430", "not MSP430" — the family
                    # is named but not asserted about this part.
                    if _NEGATION.search(line[max(0, match.start() - 24) : match.start()]):
                        continue
                    family = _normalized_family(pattern_name, match)
                    first_by_family.setdefault(family, match)
                for family, first in first_by_family.items():
                    bits = _adjacent_bits(line, first.start(), first.end())
                    row: dict[str, Any] = {
                        "schema": CORE_FAMILY_SCHEMA,
                        "field": "core_family",
                        "value": family,
                        "core_bits": bits,
                        "page_1based": page_no,
                        "verbatim": line[:400],
                        "span": first.group(0)[:120],
                        "evidence_source": evidence,
                        "method": CORE_FAMILY_METHOD,
                    }
                    if bits is not None:
                        emitted_bits.add(bits)
                    if sha256:
                        row["pdf_sha"] = sha256
                    if part_number:
                        row["part_number"] = part_number
                    rows.append(row)

            bits_candidates: dict[int, re.Match[str]] = {}
            for match in _CORE_BITS.finditer(line):
                bits = int(match.group(1))
                if bits in emitted_bits or not _bits_claim(line, match):
                    continue
                bits_candidates.setdefault(bits, match)
            if len(bits_candidates) != 1 or _BOILERPLATE.search(line):
                continue
            for bits, match in bits_candidates.items():
                row = {
                    "schema": CORE_FAMILY_SCHEMA,
                    "field": "core_bits",
                    "value": bits,
                    "page_1based": page_no,
                    "verbatim": line[:400],
                    "span": match.group(0)[:40],
                    "evidence_source": evidence,
                    "method": CORE_FAMILY_METHOD,
                }
                if sha256:
                    row["pdf_sha"] = sha256
                if part_number:
                    row["part_number"] = part_number
                rows.append(row)
    return rows


def collapse_core_family_rows(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Mapping[str, Any]]:
    """One winner per ``field:value``; page-1/feature evidence wins ties.

    A family row that carries printed bits outranks the same family without
    them. Keyed by ``"core_family:<value>"`` / ``"core_bits:<value>"`` so
    dual-core documents keep both families.
    """

    best: dict[str, tuple[tuple[int, int], Mapping[str, Any]]] = {}
    for row in rows:
        key = f"{row.get('field')}:{row.get('value')}"
        weight = (
            _EVIDENCE_WEIGHT.get(str(row.get("evidence_source")), 0),
            1 if row.get("core_bits") is not None or row.get("field") == "core_bits" else 0,
        )
        current = best.get(key)
        if current is None or weight > current[0]:
            best[key] = (weight, row)
    return {key: pair[1] for key, pair in best.items()}


__all__ = [
    "CORE_FAMILY_METHOD",
    "CORE_FAMILY_SCHEMA",
    "collapse_core_family_rows",
    "core_family_rows",
]
