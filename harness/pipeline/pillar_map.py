#!/usr/bin/env python3
"""Pillar map: per-document routing manifest over the substrate.

For each extracted doc: sections found + page ranges -> tier routing.
needs_ocr docs -> vision queue. pinout/ballout/package pages -> vision.
Text pillars (parametrics, OPN, descriptions) -> student tier.
Writes one manifest JSON per doc to /Volumes/M5_4TB/extract-results/routes/.
"""

import json
import re
import sys
from pathlib import Path

RESULTS = Path("/Volumes/M5_4TB/extract-results")
ROUTES = RESULTS / "routes"

SECTION_PATTERNS = {
    "absmax": r"absolute\s+(maximum|limits|ratings)|stress\s+ratings",
    "recommended_operating": r"recommended\s+operating",
    "electrical_characteristics": r"electrical\s+characteristics",
    "thermal": r"thermal\s+(characteristics|information|data|resistance)",
    "pinout": r"pin\s+(configuration|description|assignments|functions|out)",
    "ballout": r"ball\s+(map|out|grid|configuration)",
    "ordering_opns": r"ordering\s+(information|options)",
    "package": r"package\s+(information|outline|drawings?|dimensions)",
    "applications": r"application\s+(information|notes?)|applications",
    "typical_application": r"typical\s+(operating|application)",
    "errata": r"errata(ta)?",
    "footnotes": r"footnotes?|notes?\s*:",
    "operating_conditions": r"operating\s+(conditions|ranges?)",
    "switching": r"switching\s+(characteristics|parameters)",
}
COMPILED = {k: re.compile(v, re.IGNORECASE) for k, v in SECTION_PATTERNS.items()}

VISION_MANDATORY = {"pinout", "ballout", "package"}
STUDENT_PILLARS = {
    "parametric_tables": {"electrical_characteristics", "recommended_operating",
                          "operating_conditions", "switching", "absmax", "thermal"},
    "opns": {"ordering_opns"},
    "descriptions": {"applications", "typical_application"},
}


def map_doc(rec: dict) -> dict:
    sections = {}
    for page in rec.get("pages", []):
        text = page.get("text") or ""
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        heads = []
        for line in lines[:40]:
            ll = line.lower()
            for name, rx in COMPILED.items():
                if rx.search(ll) and len(ll) < 80:
                    heads.append(name)
        heads = list(dict.fromkeys(heads))
        if len(heads) >= 4:
            continue
        for name in heads:
            sections.setdefault(name, []).append(page["page"])
    for name in list(sections):
        sections[name] = sections[name][:4]
    needs_ocr = bool(rec.get("needs_ocr"))
    vision_pages = sorted({p for s in VISION_MANDATORY for p in sections.get(s, [])})
    if needs_ocr:
        vision_pages = sorted(set(range(1, min(6, (rec.get("page_count") or 5)) + 1)) | set(vision_pages))
    student_sections = {
        pillar: sorted({p for s in secs for p in sections.get(s, [])})
        for pillar, secs in STUDENT_PILLARS.items()
        if any(s in sections for s in secs)
    }
    return {
        "sha256": rec.get("sha256"),
        "filename": rec.get("filename"),
        "needs_ocr": needs_ocr,
        "sections": {k: v for k, v in sections.items()},
        "route": {
            "tier": "vision-first" if needs_ocr else "student-first",
            "vision_pages": vision_pages,
            "student_pillars": student_sections,
            "frontier_escalation": bool(rec.get("text_quality", {}).get("chars", 0) == 0 and not needs_ocr),
        },
    }


def main(limit=None):
    ROUTES.mkdir(parents=True, exist_ok=True)
    seen = set()
    for existing in ROUTES.glob("*.json"):
        seen.add(existing.stem)
    count = 0
    for jf in RESULTS.rglob("*.json"):
        if jf.name.startswith(".") or jf.stem in seen or jf.parent.name == "routes":
            continue
        if limit and count >= limit:
            break
        try:
            rec = json.loads(jf.read_text())
        except Exception:
            continue
        if "sha256" not in rec or "pages" not in rec:
            continue
        m = map_doc(rec)
        (ROUTES / f"{jf.stem}.json").write_text(json.dumps(m, ensure_ascii=False))
        count += 1
    print(f"routes written: {count} (skipped {len(seen)} existing)")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else None)
