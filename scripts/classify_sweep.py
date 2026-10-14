#!/usr/bin/env python3
"""S1 — classification sweep over the L0 census unknowns (pure CPU, no models).

PLAN-unseen-doc-types-20261012 §2/§6. The measured pile (2026-10-12): 83,235
unknowns of 198,240 census docs — 60,834 from `datasheet-tail-20261007`, plus
file types the classifier never planned for (1,879 .zip, 1,698 images, 15 EDA
models, xlsx, txt).

The sweep re-labels ONLY where evidence exists, and records how it decided:

  1. extension class  (container / image_only / sidecar_text / tabular_sheet /
     eda_model) — forced by the file type, checked first (a zip is a container
     whatever its bytes say). `.bin` never maps (fail closed).
  2. batch-name prior -> `_PRIOR_CLASS_MAP` — the delivery batch names carry
     truth the content classifier could not see (blob-sha'd filenames).
  3. else stays `unknown`. A guess is not a class.

Content-classified rows are copied untouched. Originals are never modified
(L0 verbatim law) — output lands in a new directory with `class_source`
recording the decision path (`content` / `extension` / `manifest_prior`).

Usage: python3 scripts/classify_sweep.py
"""

from __future__ import annotations

import glob
import json
import os
import re
from collections import Counter

from harness.electronics.l0 import _PRIOR_CLASS_MAP, extension_class

RESULTS = "/Users/samkim/Harnessv1/results"
OUT = os.path.join(RESULTS, "l0-reclass-20261012")

# Batch-name token -> prior (evidence: the batch name itself). Conservative:
# only unambiguous tokens; `unassigned`/`incremental`/`holdout` deliberately
# absent — they name no class and must stay unknown (negative controls).
_BATCH_PRIORS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"datasheet", re.I), "datasheet"),
    (re.compile(r"pcn|change.?notif", re.I), "pcn"),
    (re.compile(r"refdesign", re.I), "reference_design"),
    (re.compile(r"manuals?", re.I), "reference_manual"),
    (re.compile(r"errata", re.I), "errata"),
    (re.compile(r"\bbom\b", re.I), "bom"),
    (re.compile(r"brochure", re.I), "brochure"),
    (re.compile(r"app.?notes?", re.I), "application_note"),
    (re.compile(r"fact.?sheet", re.I), "fact_sheet"),
)


def batch_of(source_path: str) -> str:
    """The delivery batch directory name from a blob path, or ''."""
    parts = source_path.replace("\\", "/").split("/")
    for seg in reversed(parts[:-1]):
        if re.search(r"2026\d{4}|l0", seg):
            return seg
    return ""


def prior_for(batch: str) -> str | None:
    for pattern, prior in _BATCH_PRIORS:
        if pattern.search(batch):
            return _PRIOR_CLASS_MAP.get(prior)
    return None


def sweep() -> dict:
    os.makedirs(OUT, exist_ok=True)
    out_path = os.path.join(OUT, "l0-records.jsonl")
    counts: Counter[str] = Counter()
    sources: Counter[str] = Counter()
    before: Counter[str] = Counter()
    total = 0

    with open(out_path, "w") as out:
        for census in sorted(glob.glob(f"{RESULTS}/l0-census-*") + glob.glob(f"{RESULTS}/l0-refdesign-*")):
            rec_path = os.path.join(census, "l0-records.jsonl")
            if not os.path.exists(rec_path):
                continue
            with open(rec_path) as fh:
                for line in fh:
                    rec = json.loads(line)
                    total += 1
                    old = rec.get("doc_class", "unknown")
                    before[old] += 1
                    if old != "unknown":
                        rec["class_source"] = rec.get("class_source", "content")
                        counts[old] += 1
                        sources[rec["class_source"]] += 1
                        out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                        continue

                    source_path = rec.get("source_path", "")
                    filename = os.path.basename(source_path)
                    new_class = None
                    how = None

                    ext = extension_class(filename)
                    if ext is not None:
                        new_class, how = ext, "extension"
                    else:
                        prior = prior_for(batch_of(source_path))
                        if prior:
                            new_class, how = prior, "manifest_prior"

                    if new_class is None:
                        counts["unknown"] += 1
                        sources["unresolved"] += 1
                        rec["class_source"] = "unresolved"
                    else:
                        counts[new_class] += 1
                        sources[how] += 1
                        rec["doc_class"] = new_class
                        rec["class_source"] = how
                    out.write(json.dumps(rec, ensure_ascii=False) + "\n")

    summary = {
        "sweep": "l0-reclass-20261012",
        "law": "re-label only with evidence; unknown stays unknown (fail closed); originals untouched",
        "records": total,
        "classes_before": dict(before.most_common()),
        "classes_after": dict(counts.most_common()),
        "decided_by": dict(sources.most_common()),
        "still_unknown": counts.get("unknown", 0),
    }
    with open(os.path.join(OUT, "summary.json"), "w") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=1)
    return summary


if __name__ == "__main__":
    s = sweep()
    print(f"records: {s['records']}")
    print(f"still unknown: {s['still_unknown']}")
    print("decided_by:", s["decided_by"])
    for k, v in s["classes_after"].items():
        print(f"  {k:20s} {v}")
