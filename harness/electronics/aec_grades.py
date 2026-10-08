"""Automotive qualification grades (AEC-Q100 / AIS-Q100), cited only.

The FE rule (cr-core am-muwfhdrd-3997): a grade row ships the day a cited
grade field exists — an operating temperature range never proves
qualification. This reader scans for the printed claim only:

- the standard as printed (AEC-Q100 / AIS-Q100 / AEC Q100 variants)
- the grade as printed (``Grade 0..3``, ``:Grade 1``, ``Grade 1``), and
  ``qualified`` statements when no grade digit is printed
- page + verbatim context for every hit; nothing derived, nothing inferred

Multiple hits per document are kept (front page claim vs qualification
summary section may print different granularity); collapse happens at the
consumer when the cited values agree.
"""

from __future__ import annotations

import re
from typing import Any, Sequence


AEC_SCHEMA = "harness.electronics-aec-grade.v1"

_STANDARD = re.compile(
    r"\b(?:AEC|AIS)[\s\-–]?(?:Q|QA)?[\s\-–]?100(?:\s*Rev(?:ision)?\.?\s*[A-H])?",
    re.I,
)
_GRADE = re.compile(
    r"\bGrade\s*[:\-]?\s*([0-3])\b", re.I
)
_QUALIFIED = re.compile(
    r"\b(?:automotive\s+qualified|qualified\s+to|qualification\s+to|"
    r"compliant\s+to|in\s+accordance\s+with|according\s+to|"
    r"certified\s+to|meeting\s+the\s+requirements?\s+of)\b",
    re.I,
)
_WINDOW = 140


def grade_rows(page_texts: Sequence[str]) -> list[dict[str, Any]]:
    """One row per printed standard mention carrying a grade or a qualified
    statement; plain mentions without either are counted, not emitted."""

    rows: list[dict[str, Any]] = []
    for page_index, text in enumerate(page_texts, 1):
        if not text or "100" not in text:
            continue
        flat = " ".join(text.split())
        for match in _STANDARD.finditer(flat):
            tail = flat[match.end() : match.end() + 6]
            sub_document = re.match(r"\s*[-–]?\s*\d{3}\b", tail)
            standard = " ".join(match.group(0).split())
            start = max(0, match.start() - _WINDOW)
            end = min(len(flat), match.end() + _WINDOW)
            context = flat[start:end]
            grade = None
            grade_match = _GRADE.search(context)
            if grade_match:
                grade = grade_match.group(1)
            qualified = bool(_QUALIFIED.search(context))
            if sub_document and grade is None:
                continue
            if grade is None and not qualified:
                continue
            rows.append(
                {
                    "schema": AEC_SCHEMA,
                    "page_1based": page_index,
                    "standard_verbatim": standard,
                    "grade": grade,
                    "qualified_statement": qualified,
                    "context_verbatim": context[:400],
                    "method": "deterministic_aec_grade_v1",
                }
            )
    return rows


__all__ = ["AEC_SCHEMA", "grade_rows"]
