"""Section census: frequency of datasheet section headers across the substrate.

The council's evidence engine — 'hey Sam, pillar FOO appears in 94% of docs.'
Runs over extract-results JSONs; regex header detection, aisle-aware summary.
"""

import json
import re
import sys
from collections import Counter
from pathlib import Path

RESULTS_ROOTS = [
    Path("/Volumes/M5_4TB/extract-results/runs"),
    Path("/Volumes/M5_4TB/extract-results"),
]

HEADER_PATTERNS = [
    (r"absolute\s+maximum\s+ratings", "absmax"),
    (r"recommended\s+operating\s+(conditions|parameters)", "recommended_operating"),
    (r"electrical\s+characteristics", "electrical_characteristics"),
    (r"thermal\s+(characteristics|information|data)", "thermal"),
    (r"pin\s+(configuration|description|assignments|functions)", "pinout"),
    (r"ball\s+(map|out|grid)", "ballout"),
    (r"ordering\s+(information|options)", "ordering_opns"),
    (r"package\s+(information|outline|drawings?)", "package"),
    (r"typical\s+(operating|application)", "typical_application"),
    (r"application\s+(information|notes?)", "applications"),
    (r"errata(ta)?", "errata"),
    (r"footnotes?", "footnotes"),
    (r"operating\s+(conditions|ranges?)", "operating_conditions"),
    (r"switching\s+(characteristics|parameters)", "switching"),
    (r"esd\s+(protection|ratings)", "esd"),
]
COMPILED = [(re.compile(p, re.IGNORECASE), name) for p, name in HEADER_PATTERNS]


def iter_docs():
    seen = set()
    for root in RESULTS_ROOTS:
        if not root.exists():
            continue
        for j in root.rglob("*.json"):
            if j.name.startswith(".") or j.stem in seen:
                continue
            seen.add(j.stem)
            try:
                yield json.loads(j.read_text())
            except Exception:
                continue


def census(limit=None):
    docs = docs_with = Counter()
    doc_count = 0
    ocr = 0
    total_pages = 0
    for i, doc in enumerate(iter_docs()):
        if limit and i >= limit:
            break
        doc_count += 1
        total_pages += doc.get("page_count") or 0
        if doc.get("needs_ocr"):
            ocr += 1
        found = set()
        for page in doc.get("pages", []):
            head = page.get("text", "")[:2000]
            for rx, name in COMPILED:
                if name not in found and rx.search(head):
                    found.add(name)
        for name in found:
            docs_with[name] += 1
    return {
        "documents": doc_count,
        "pages": total_pages,
        "needs_ocr_rate": round(ocr / doc_count, 4) if doc_count else None,
        "sections": [
            {"section": name, "docs": c, "rate": round(c / doc_count, 4)}
            for name, c in docs_with.most_common()
        ],
    }


if __name__ == "__main__":
    lim = int(sys.argv[1]) if len(sys.argv) > 1 else None
    print(json.dumps(census(lim), indent=2))
