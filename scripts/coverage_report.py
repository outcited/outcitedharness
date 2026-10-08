#!/usr/bin/env python3
"""Brand and PDF-type coverage report over the substrate.

Sam's proof: the system must work across every brand and a myriad of PDF
types. This reports, per vendor and per document type: volume, text quality,
needs_ocr rate, page-label coverage, and page-count ranges.
"""

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOTS = [Path("/Volumes/M5_4TB/extract-results")]

VENDOR_PATTERNS = [
    (r"^infineon|^bts|^irf|^2n7002|^(i[ks][a-z]\d)", "infineon"),
    (r"^rohm|^si[cs]\d|^rb0|^(s[ij]\w+\d)", "rohm"),
    (r"^ti_|^texas|^lm\d|^tps\d|^msp430|^slau|^slvs|^sm[pt]", "ti"),
    (r"^st_|stm32|stn|^st[a-z]?\d", "st"),
    (r"nrf\d|nordic", "nordic"),
    (r"^esp\d|esp8266|esp32", "espressif"),
    (r"gd32|gd[pm]?", "gigadevice"),
    (r"^nxp|lpc\d|kinetis|mk\d{2}", "nxp"),
    (r"renesas|^r[5678][a-z]\d|ra\d|rl78|rx\d{2}", "renesas"),
    (r"^msp|pic\d|avr|atmega|attiny|samd|samc", "microchip-atmel"),
    (r"^tmk|^c[0-9]\d{3}|^c0[gl]", "samsung-murata"),
    (r"^ncv|^lv\d|^onsemi|^fqp|^mbr", "onsemi"),
    (r"^vishay|^si\d{3}", "vishay"),
    (r"^diodes|^zxm|^ap\d{5}", "diodes"),
    (r"^toshiba|^tck|^tph", "toshiba"),
]

DOC_TYPE_HINTS = [
    (r"um|user.?manual|rm\d+|reference.?manual", "reference_manual"),
    (r"family|lineup|selection.?guide", "family_guide"),
    (r"db|datasheet", "datasheet"),
    (r"an\d+|app.?note|application.?note", "app_note"),
    (r"errata", "errata"),
    (r"pb|product.?brief", "product_brief"),
    (r"package|land.?pattern|footprint", "package_doc"),
]


def vendor_of(name: str) -> str:
    low = name.lower()
    for pat, vendor in VENDOR_PATTERNS:
        try:
            if re.search(pat, low):
                return vendor
        except re.error:
            continue
    return "other/unknown"


def type_of(name: str) -> str:
    low = name.lower()
    for pat, kind in DOC_TYPE_HINTS:
        if re.search(pat, low):
            return kind
    return "datasheet"


def main(limit=None):
    vendors = defaultdict(lambda: {"docs": 0, "ocr": 0, "labeled": 0, "pages": 0,
                                   "quality_fail": 0, "chars": 0, "types": defaultdict(int)})
    total = 0
    seen = set()
    for root in ROOTS:
        for jf in root.rglob("*.json"):
            if jf.name.startswith(".") or jf.stem in seen:
                continue
            if limit and total >= limit:
                break
            try:
                rec = json.loads(jf.read_text())
            except Exception:
                continue
            seen.add(jf.stem)
            total += 1
            v = vendors[vendor_of(rec.get("filename", ""))]
            v["docs"] += 1
            v["pages"] += rec.get("page_count") or 0
            v["chars"] += rec.get("text_chars") or 0
            if rec.get("needs_ocr"):
                v["ocr"] += 1
            if rec.get("page_labels"):
                v["labeled"] += 1
            q = rec.get("text_quality")
            if q is not None and not q.get("quality_ok"):
                v["quality_fail"] += 1
            v["types"][type_of(rec.get("filename", ""))] += 1

    rows = sorted(vendors.items(), key=lambda kv: -kv[1]["docs"])
    print(f"# SUBSTRATE COVERAGE — {total} documents\n")
    print("| vendor | docs | ocr-needed | page-labels | avg pages | text-kb/doc |")
    print("|---|---|---|---|---|---|")
    for name, v in rows:
        avg_p = v["pages"] / v["docs"] if v["docs"] else 0
        kb = v["chars"] / v["docs"] / 1000 if v["docs"] else 0
        print(f"| {name} | {v['docs']} | {v['ocr']} ({v['ocr']/v['docs']:.0%}) | "
              f"{v['labeled']} ({v['labeled']/v['docs']:.0%}) | {avg_p:.0f} | {kb:.1f} |")
    all_types = defaultdict(int)
    for _, v in rows:
        for t, n in v["types"].items():
            all_types[t] += n
    print("\n## Document types across corpus\n")
    for t, n in sorted(all_types.items(), key=lambda kv: -kv[1]):
        print(f"- {t}: {n} ({n/total:.0%})" if total else f"- {t}: {n}")
    tot_ocr = sum(v["ocr"] for _, v in rows)
    tot_lab = sum(v["labeled"] for _, v in rows)
    print(f"\n## Corpus health\n- needs_ocr: {tot_ocr}/{total} ({tot_ocr/total:.1%})\n"
          f"- page-label coverage: {tot_lab}/{total} ({tot_lab/total:.1%})")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else None)
