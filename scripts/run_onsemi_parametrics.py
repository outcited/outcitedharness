#!/usr/bin/env python3
"""onsemi PRT wave: build the queue with deterministic class inference, then extract.

Class inference from front-matter text (documented on every row);
extraction reuses the TI-wave extractor's extract_part unchanged.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

import pymupdf

ROOT = Path("/Users/samkim/Harnessv1")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.extract_ti_parametrics import extract_part  # noqa: E402

CORPUS = Path("/Volumes/M5_4TB/exports/cr_corpus/onsemi-prt-pdfs-20260912")
OUT = Path("/Volumes/M5_4TB/scratch/onsemi-parametrics-20260912")
OUT.mkdir(parents=True, exist_ok=True)

CLASS_PATTERNS = [
    (re.compile(r"\bmosfet\b|\bfet\b", re.I), "mosfet"),
    (re.compile(r"gate\s+driver", re.I), "gate_driver"),
    (re.compile(r"battery\s+charger|charging\s+system|charge\s+management", re.I), "charger"),
    (re.compile(r"linear\s+regulator|low[- ]dropout|\bldo\b|buck|boost|step[- ]down|step[- ]up|switching\s+regulator|dc[- ]dc|voltage\s+regulator", re.I), "regulator"),
    (re.compile(r"power\s+management|multi[- ]channel|system\s+power|pmu\b", re.I), "pmic"),
    (re.compile(r"load\s+switch", re.I), "load_switch"),
]


def infer_class(pdf: Path) -> tuple[str, str]:
    doc = pymupdf.open(pdf)
    text = " ".join(doc[i].get_text() for i in range(min(2, doc.page_count)))[:3000]
    doc.close()
    for pattern, cls in CLASS_PATTERNS:
        m = pattern.search(text)
        if m:
            return cls, m.group(0)
    return "unknown", "no_class_pattern_matched"


def main():
    queue = []
    pdfs = sorted(CORPUS.glob("*.pdf"))
    for pdf in pdfs:
        part = pdf.stem
        cls, evidence = infer_class(pdf)
        queue.append(
            {
                "part_number": part,
                "device_class": cls,
                "class_inference_evidence": evidence,
                "pdf_file": pdf.name,
            }
        )
    (OUT / "onsemi-queue.json").write_text(json.dumps(queue, indent=1))
    counts = Counter(q["device_class"] for q in queue)
    print("queue:", len(queue), "| classes:", dict(counts))

    out_rows = []
    tally = Counter()
    for item in queue:
        pdf = CORPUS / item["pdf_file"]
        try:
            rows = extract_part(pdf, item["part_number"], item["device_class"])
        except Exception as exc:
            tally[f"error:{type(exc).__name__}"] += 1
            continue
        for r in rows:
            r["device_class_inferred"] = True
            r["class_inference_evidence"] = item["class_inference_evidence"]
            out_rows.append(r)
            tally[f"axis:{r['field']}"] += 1
        if rows:
            tally["parts_with_rows"] += 1
        else:
            tally["parts_no_rows"] += 1
    (OUT / "onsemi-parametric-fills.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in out_rows)
    )
    print(json.dumps({"parts": len(queue), "rows": len(out_rows), **dict(sorted(tally.items()))}, indent=1))


if __name__ == "__main__":
    sys.exit(main())
