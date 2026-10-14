#!/usr/bin/env python3
"""Pillar extraction: universal comparison facets from every datasheet.

These are the attributes that EVERY brand prints and EVERY engineer uses
to select parts. Each pillar is a deterministic table/feature reader that
works across all vendors. Together they form the comparison backbone.

Pillars (by corpus frequency, all deterministic):
  1. package_info       — package type, pin count (from ordering/features)
  2. esd_ratings        — HBM, CDM, latch-up (from EC tables)
  3. operating_range    — supply voltage, temp range, freq (from ROC/EC)
  4. memory_config      — Flash KB, RAM KB, EEPROM (from features/EC)
  5. interface_counts   — SPI, I2C, UART, USB, CAN, Ethernet, DMA, ADC (features)
  6. clock_max          — max operating frequency (from features/EC)
  7. security_features  — TrustZone, crypto, secure boot (from features)
"""

from __future__ import annotations

import json
import re
from typing import Any, Mapping, Sequence


PILLAR_SCHEMA = "harness.electronics-pillar-facts.v1"

# Pattern libraries — each is (pattern, field, unit, value_type)
# Package patterns carry a format tag instead of a unit:
#   'C': FAMILY## / FAMILY-## ("LQFP48", "QFN-32")
#   'B': FAMILY: ## Pin ("TSSOP: 14 Pin" — TI features sections)
#   'A': ##-FAMILY ("20-TSSOP", "16-QFN" — ordering tables)
_PKG_FAMILIES = (r'LQFP|QFP|VQFN|UQFN|WQFN|TQFP|PQFP|BQFP|CQFN|QFN|BGA|WLCSP|'
                 r'SOT|SOIC|TSSOP|HTSSOP|SSOP|MSOP|VSSOP|DFN|PDIP|CDIP|DIP|'
                 r'SOP|DSOP|CSP|LGA|WSON|VSON|SON|TO-?\d+|SOT-?\d+')
_PACKAGE_PATTERNS = [
    (re.compile(r'\b(' + _PKG_FAMILIES + r')\s*(-?)\s*(\d{1,4})\b', re.I), 'package', 'C'),
    (re.compile(r'\b(' + _PKG_FAMILIES + r')\s*:?\s*(-?)\s*(\d{1,4})\s*-?\s*(?:pin|leads?)\b', re.I), 'package', 'B'),
    (re.compile(r'\b(\d{1,4})\s*-\s*(' + _PKG_FAMILIES + r')\b', re.I), 'package', 'A'),
]

# Standard IC pin counts — rejects PDF line-wrap artifacts like "TSSOP1"
# (from "TSSOP14" split across lines). Deterministic whitelist.
_PLAUSIBLE_PIN_COUNTS = {
    3, 4, 5, 6, 8, 10, 12, 14, 16, 18, 20, 24, 28, 30, 32, 36, 38, 40, 42, 44,
    48, 52, 56, 60, 64, 68, 72, 76, 80, 84, 88, 96, 100, 104, 108, 112, 120,
    121, 124, 128, 132, 144, 160, 164, 168, 169, 176, 180, 192, 196, 200, 208,
    216, 224, 225, 240, 256, 260, 272, 280, 288, 289, 292, 296, 304, 320, 324,
    336, 352, 360, 376, 388, 400, 424, 432, 448, 456, 480, 484, 512, 516, 536,
    540, 576, 624, 625, 676, 728, 729, 784, 832, 896, 900, 961, 1023, 1024,
    1156, 1296, 1329, 1517, 1600, 1760, 2104, 2500,
}

_ESD_PATTERNS = [
    (re.compile(r'(?:HBM|human.body.model).{0,30}?±?\s*(\d{2,5})\s*V', re.I), 'esd_hbm_v', 'V'),
    (re.compile(r'(?:CDM|charged.device.model).{0,30}?±?\s*(\d{2,5})\s*V', re.I), 'esd_cdm_v', 'V'),
    (re.compile(r'(?:latch[- ]?up).{0,40}?(\d{2,4})\s*mA', re.I), 'latchup_ma', 'mA'),
]

_SUPPLY_PATTERNS = [
    (re.compile(r'(?:supply|operating|V\s*DD|VCC|V\s*DDA)\s*(?:voltage|range|volt)?.{0,15}?(\d(?:\.\d+)?)\s*V\s*(?:to|–|-|~)\s*(\d(?:\.\d+)?)\s*V', re.I), 'supply_v', 'V'),
]

_TEMP_PATTERNS = [
    (re.compile(r'operating\s+temperature.{0,20}?(-?\d{2,3})\s*°?\s*C?\s*(?:to|–|-|~)\s*([+-]?\d{2,3})\s*°?\s*C', re.I), 'temp_range_c', '°C'),
    (re.compile(r'(?:TA|Tamb|ambient).{0,15}?(-?\d{2,3})\s*(?:to|–|-|~)\s*([+-]?\d{2,3})\s*°?\s*C', re.I), 'temp_range_c', '°C'),
]

_MEMORY_PATTERNS = [
    (re.compile(r'(\d{1,5})\s*(K|M|k|m)?\s*(B|b|bytes?)?\s*(?:of\s*)?(?:flash|program.memory|code.storage)', re.I), 'flash_kb', 'KB'),
    (re.compile(r'(\d{1,5})\s*(K|M|k|m)?\s*(B|b|bytes?)?\s*(?:of\s*)?(?:SRAM|RAM|data.memory)', re.I), 'sram_kb', 'KB'),
    (re.compile(r'(\d{1,5})\s*(K|M|k|m)?\s*(B|b|bytes?)?\s*(?:of\s*)?(?:EEPROM)', re.I), 'eeprom_kb', 'KB'),
]
_MEMORY_FIELDS = {'flash_kb', 'sram_kb', 'eeprom_kb'}
# plausibility floors (KB): no MCU ships main flash under 1KB; tiny RAM is real
_MEMORY_FLOOR = {'flash_kb': 1.0, 'sram_kb': 0.0625, 'eeprom_kb': 0.0625}

_INTERFACE_PATTERNS = [
    (re.compile(r'(\d+)\s*[x×]\s*(?:SPI|Serial.Peripheral)', re.I), 'n_spi', 'count'),
    (re.compile(r'(\d+)\s*[x×]\s*(?:I2C|I²C|IIC)', re.I), 'n_i2c', 'count'),
    (re.compile(r'(\d+)\s*[x×]\s*(?:UART|USART|serial.port)', re.I), 'n_uart', 'count'),
    (re.compile(r'(\d+)\s*[x×]\s*(?:USB|USB.port)', re.I), 'n_usb', 'count'),
    (re.compile(r'(\d+)\s*[x×]\s*(?:CAN|CAN.FD|CAN.bus)', re.I), 'n_can', 'count'),
    (re.compile(r'(\d+)\s*[x×]\s*(?:Ethernet|ETH|MAC)', re.I), 'n_ethernet', 'count'),
    (re.compile(r'(\d+)\s*(?:channel\s*)?DMA', re.I), 'n_dma', 'channels'),
    # n_adc: channel counts ONLY — "ADC10" is a bit-width product token,
    # not 10 channels (verbatim audit 2026-10-09 caught the confusion)
    (re.compile(r'(\d{1,3})\s*[- ]?(?:channel|ch)s?[^.\n]{0,40}?(?:ADC|analog.to.digital|analog.input|converter)', re.I), 'n_adc', 'channels'),
    (re.compile(r'(?:ADC|analog.to.digital)[^.\n]{0,30}?\b(\d{1,3})\s*[- ]?(?:channel|ch)s?\b', re.I), 'n_adc', 'channels'),
    (re.compile(r'with\s+(\d{1,3})\s+(?:channels?)\b', re.I), 'n_adc', 'channels'),
    (re.compile(r'(\d+)\s*(?:channel\s*)?(?:timer|PWM|pulse.width)', re.I), 'n_timer', 'count'),
    (re.compile(r'(\d+)\s*(?:GPIO|general.purpose.I/O|I/O.pin)', re.I), 'n_gpio', 'pins'),
]

_CLOCK_PATTERNS = [
    (re.compile(r'(?:up.to|max|maximum).{0,10}?(\d{2,4})\s*MHz', re.I), 'clk_max_mhz', 'MHz'),
    (re.compile(r'(\d{2,4})\s*MHz\s*(?:CPU|core|clock|processor|operating)', re.I), 'clk_max_mhz', 'MHz'),
]

_SECURITY_PATTERNS = [
    (re.compile(r'\bTrustZone\b', re.I), 'has_trustzone', 'bool'),
    (re.compile(r'\bAES[- ]?(?:128|192|256)?\b', re.I), 'has_aes', 'bool'),
    (re.compile(r'\b(?:secure.boot|root.of.trust|ROM.trust)\b', re.I), 'has_secure_boot', 'bool'),
    (re.compile(r'\b(?:TRNG|true.random|random.number.generator)\b', re.I), 'has_trng', 'bool'),
    (re.compile(r'\b(?:unique.ID|UID|UUID|device.ID|serial.number)\b', re.I), 'has_unique_id', 'bool'),
]


def _enclosing_line(src: str, m) -> str:
    """The printed line a match came from — the user-validatable evidence."""
    ls = src.rfind("\n", 0, m.start()) + 1
    le = src.find("\n", m.end())
    if le == -1:
        le = len(src)
    line = " ".join(src[ls:le].split())
    if len(line) > 220:
        # keep the match centered in the window
        rel = m.start() - ls
        s = max(0, rel - 100)
        line = line[s:s + 220]
    return line


def _window(src: str, m, width: int = 100) -> str:
    s = max(0, m.start() - width)
    e = min(len(src), m.end() + width)
    return " ".join(src[s:e].split())[:220]


def _extract_pillars(text: str) -> list[dict[str, Any]]:
    """Extract all pillar facts from a text block (page 1 + features).

    Every fact carries the verbatim printed line it was read from
    (truth + provenance directive, Sam 2026-10-09) and a confidence:
    deterministic print reads start at 0.95; 0.98 when the same value is
    printed more than once in the block (corroboration).
    """
    facts = []
    # PDF text layers wrap mid-token ("TSSOP1\n4"); joining newlines restores
    # package names. Only used for the package pattern.
    joined = text.replace("\n", "")

    for patterns in (_PACKAGE_PATTERNS, _ESD_PATTERNS, _SUPPLY_PATTERNS,
                      _TEMP_PATTERNS, _MEMORY_PATTERNS, _INTERFACE_PATTERNS,
                      _CLOCK_PATTERNS, _SECURITY_PATTERNS):
        for pattern, field, *rest in patterns:
            unit = rest[0] if rest else None
            src = joined if field == 'package' else text
            hits = list(pattern.finditer(src))
            if not hits:
                continue
            if field == 'package':
                fmt = unit  # 'A', 'B', or 'C'
                seen_pkgs: dict[str, dict] = {}
                for m in hits[:6]:
                    groups = m.groups()
                    if fmt == 'A':
                        dig, fam = groups[0], groups[1]
                    else:
                        fam, dig = groups[0], groups[-1]
                    if not dig.isdigit() or int(dig) not in _PLAUSIBLE_PIN_COUNTS:
                        continue  # line-wrap/table-cell artifact (e.g. "TSSOP1")
                    pkg = f"{fam.upper().replace('-', '')}{dig}"
                    if pkg not in seen_pkgs:
                        seen_pkgs[pkg] = {'field': field, 'value': pkg,
                                          'unit': None,
                                          'verbatim': " ".join(m.group(0).split()),
                                          'method': 'deterministic_pillar_v2'}
                facts.extend(list(seen_pkgs.values())[:3])
            elif field in ('supply_v', 'temp_range_c'):
                groups = hits[0].groups()
                facts.append({'field': field,
                              'value': [float(groups[0]), float(groups[1])],
                              'unit': unit,
                              'verbatim': _enclosing_line(src, hits[0]),
                              'method': 'deterministic_pillar_v2'})
            elif unit == 'bool':
                facts.append({'field': field, 'value': True, 'unit': None,
                              'verbatim': _enclosing_line(src, hits[0]),
                              'method': 'deterministic_pillar_v2'})
            elif field in _MEMORY_FIELDS:
                for m in hits[:2]:
                    g = m.groups()
                    try:
                        num = float(g[0])
                    except (TypeError, ValueError):
                        continue
                    km = (g[1] or '').lower()
                    b = (g[2] or '').lower()
                    if km == 'k':
                        kb = num
                    elif km == 'm':
                        kb = num * 1024.0
                    elif b:
                        kb = num / 1024.0
                    else:
                        kb = num  # bare number in a KB-labeled context
                    if kb < _MEMORY_FLOOR.get(field, 0.0) or kb > 65536.0:
                        continue
                    facts.append({'field': field, 'value': round(kb, 4),
                                  'unit': 'KB',
                                  'verbatim': _enclosing_line(src, m),
                                  'method': 'deterministic_pillar_v2'})
            else:
                for m in hits[:2]:
                    g = m.groups()
                    raw = g[0] if isinstance(g, tuple) and g else m.group(0)
                    try:
                        val = float(raw)
                    except (TypeError, ValueError):
                        continue
                    facts.append({'field': field, 'value': val, 'unit': unit,
                                  'verbatim': _enclosing_line(src, m),
                                  'method': 'deterministic_pillar_v2'})

    # corroboration: same field+value printed multiple times → 0.98
    counts: dict[tuple, int] = {}
    for f in facts:
        key = (f['field'], json.dumps(f['value'], sort_keys=True, default=str))
        counts[key] = counts.get(key, 0) + 1
    for f in facts:
        key = (f['field'], json.dumps(f['value'], sort_keys=True, default=str))
        f['prints'] = counts[key]
        f['confidence'] = 0.98 if counts[key] > 1 else 0.95

    return facts


def pillar_facts_from_document(document) -> list[dict[str, Any]]:
    """Extract pillar facts from front pages + features sections."""
    facts = []
    max_page = min(6, document.page_count)
    for i in range(max_page):
        text = document[i].get_text()
        page_facts = _extract_pillars(text)
        for f in page_facts:
            f['page_1based'] = i + 1
        facts.extend(page_facts)
    
    # Also check features section pages (usually pages 1-2)
    return facts


__all__ = ["PILLAR_SCHEMA", "pillar_facts_from_document", "_extract_pillars"]
