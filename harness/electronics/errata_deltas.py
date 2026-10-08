"""Errata delta records (T3 truth-relevant lane).

Silicon-errata documents change what the datasheet asserts; cr-core's truth
audit needs the delta, not the prose. The deterministic reader walks
numbered issue sections (``2.2.1 Title ...``) and emits one record per
issue: verbatim title, symptom text, Work Around text, and the affected
silicon revisions exactly as printed (device -> revision tokens, X-matrix
resolved from the printed grid). Data Sheet Clarifications sections are
emitted as ``clarification`` records: heading + correction verbatim + any
reprinted parametric rows, which are the spec deltas against the base
datasheet.

Nothing inferred: a revision is affected only when the document prints its
mark; prose that cannot be attributed to a numbered issue is not an errata
record.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence


ERRATA_SCHEMA = "harness.electronics-errata-delta.v1"

_ISSUE_HEADING = re.compile(r"^(\d+\.\d+\.\d+)\.?\s+(\S.{3,120})$")
_ISSUE_NUMBER_ONLY = re.compile(r"^(\d+\.\d+\.\d+)\.?\s*$")
_CLAR_HEADING = re.compile(r"^(\d+\.\d+)\.?\s+(\S.{3,120})$")
_CLAR_NUMBER_ONLY = re.compile(r"^(\d+\.\d+)\.?\s*$")
_TITLE_CHAR = re.compile(r"[A-Za-z]{3,}")
_TABLEISH_TITLE = re.compile(
    r"\d\s*(?:MHz|kHz|Hz|µA|uA|μA|mA|nA|mA)\b.{0,20}\bV(?:CC|DD)\s*=", re.I
)
_NOTE_LINE = re.compile(r"^note\b", re.I)


def _number_parts(identifier: str) -> tuple[int, ...]:
    return tuple(int(token) for token in identifier.split("."))


def _plausible_title(line: str) -> bool:
    if not line or len(line) < 4 or len(line) > 120:
        return False
    if not _TITLE_CHAR.search(line):
        return False
    if re.match(r"^[\d.\s-]+$", line):
        return False
    if _TABLEISH_TITLE.search(line) or _MARK_LINE.match(line) or _NOTE_LINE.match(line):
        return False
    return line[0].isalpha() or line[0].isupper()
_WORKAROUND = re.compile(r"^work[\s-]?around\b", re.I)
_AFFECTED = re.compile(r"^affected\s+silicon\s+revisions?\b", re.I)
_CLARIFICATIONS = re.compile(r"^data\s+sheet\s+clarifications?\b", re.I)
_DEVICE_LINE = re.compile(r"^([A-Za-z][A-Za-z0-9/+._-]{2,60})\s*$")
_REVISION_LINE = re.compile(r"^(Rev\.?\s*)?([A-Z]\d?|\d+[A-Z]?)\s*$")
_MARK_LINE = re.compile(r"^[Xx\-–—\s]*$")


def _lines(page_texts: Sequence[str]) -> list[str]:
    out: list[str] = []
    for text in page_texts:
        for raw in text.splitlines():
            line = " ".join(raw.split())
            if line:
                out.append(line)
    return out


def _affected_revisions(lines: Sequence[str]) -> dict[str, list[str]]:
    """Parse the printed device -> revision grid. X marks attribute
    positionally when the mark row is unambiguous (marks == revisions);
    anything else withholds rather than guesses."""

    affected: dict[str, list[str]] = {}
    device: str | None = None
    revisions: list[str] = []
    marks: list[str] = []

    def _commit() -> None:
        if device and revisions and marks and len(marks) == len(revisions):
            for revision, mark in zip(revisions, marks):
                if mark.upper() == "X" and revision not in affected[device]:
                    affected[device].append(revision)

    for line in lines:
        if _AFFECTED.match(line):
            _commit()
            device = None
            revisions = []
            marks = []
            continue
        if _MARK_LINE.match(line):
            marks.extend(token for token in re.split(r"\s+", line.strip()) if token)
            continue
        revision = _REVISION_LINE.match(line)
        if revision and device is not None:
            _commit()
            marks = []
            revisions.append(revision.group(2))
            continue
        if _DEVICE_LINE.match(line) and not _REVISION_LINE.match(line):
            _commit()
            device = line
            revisions = []
            marks = []
            affected.setdefault(device, [])
            continue
    _commit()
    return {name: revs for name, revs in affected.items() if revs}


def errata_records(page_texts: Sequence[str]) -> list[dict[str, Any]]:
    """One record per numbered issue; clarifications carry the spec deltas."""

    lines = _lines(page_texts)
    records: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    section: list[str] = []
    mode = "body"
    last_id: tuple[int, ...] | None = None

    def _flush() -> None:
        nonlocal current, section, mode
        if current is not None:
            current["affected_silicon"] = _affected_revisions(section)
            if current.get("symptom_verbatim") or current.get("workaround_verbatim") or current["affected_silicon"]:
                records.append(current)
        current = None
        section = []
        mode = "body"

    index = 0
    while index < len(lines):
        line = lines[index]
        next_line = lines[index + 1] if index + 1 < len(lines) else ""
        heading = _ISSUE_HEADING.match(line)
        if heading and not _plausible_title(heading.group(2)):
            heading = None
        number = None
        if not heading:
            match = _ISSUE_NUMBER_ONLY.match(line)
            if match and _plausible_title(next_line):
                number = match
        if heading or number:
            identifier = (heading or number).group(1)
            parts = _number_parts(identifier)
            if last_id is not None and parts <= last_id:
                index += 1
                continue
            last_id = parts
            _flush()
            current = {
                "schema": ERRATA_SCHEMA,
                "record_kind": "issue",
                "issue_id": identifier,
                "title_verbatim": heading.group(2) if heading else None,
                "symptom_verbatim": None,
                "workaround_verbatim": None,
            }
            if number:
                current["title_verbatim"] = next_line
                index += 1
            mode = "title"
            index += 1
            continue
        if current is None:
            index += 1
            continue
        if _WORKAROUND.match(line):
            mode = "workaround"
            rest = _WORKAROUND.sub("", line).lstrip(" .:-")
            if rest:
                current["workaround_verbatim"] = rest
            index += 1
            continue
        if _AFFECTED.match(line):
            mode = "affected"
            section = []
            index += 1
            continue
        if mode == "affected":
            section.append(line)
        elif mode == "workaround":
            previous = current.get("workaround_verbatim")
            current["workaround_verbatim"] = f"{previous} {line}".strip() if previous else line
        elif mode == "title":
            previous = current.get("title_verbatim")
            candidate = f"{previous} {line}".strip() if previous else line
            if (
                len(candidate) <= 120
                and not candidate.endswith(".")
                and not _MARK_LINE.match(line)
                and not _NOTE_LINE.match(line)
            ):
                current["title_verbatim"] = candidate
            else:
                current["symptom_verbatim"] = line
                mode = "body"
        else:
            previous = current.get("symptom_verbatim")
            current["symptom_verbatim"] = f"{previous} {line}".strip() if previous else line
        index += 1
    _flush()
    for record in records:
        for key in ("symptom_verbatim", "workaround_verbatim"):
            if record.get(key):
                record[key] = record[key][:1200]
    return records


def clarification_records(
    page_texts: Sequence[str],
) -> list[dict[str, Any]]:
    """Data-sheet clarifications: the printed corrections, verbatim."""

    lines = _lines(page_texts)
    records: list[dict[str, Any]] = []
    inside = False
    current: dict[str, Any] | None = None
    last_id: tuple[int, ...] | None = None
    index = 0
    while index < len(lines):
        line = lines[index]
        next_line = lines[index + 1] if index + 1 < len(lines) else ""
        if _CLARIFICATIONS.match(line):
            inside = True
            index += 1
            continue
        if not inside:
            index += 1
            continue
        heading = _CLAR_HEADING.match(line)
        if heading and not _plausible_title(heading.group(2)):
            heading = None
        number = None
        if not heading:
            match = _CLAR_NUMBER_ONLY.match(line)
            if match and _plausible_title(next_line):
                number = match
        if heading or number:
            identifier = (heading or number).group(1)
            parts = _number_parts(identifier)
            if last_id is not None and parts <= last_id:
                index += 1
                continue
            last_id = parts
            if current is not None and current.get("correction_verbatim"):
                records.append(current)
            current = {
                "schema": ERRATA_SCHEMA,
                "record_kind": "clarification",
                "issue_id": identifier,
                "title_verbatim": heading.group(2) if heading else next_line,
                "correction_verbatim": None,
            }
            index += 2 if number else 1
            continue
        if current is not None:
            previous = current.get("correction_verbatim")
            current["correction_verbatim"] = f"{previous} {line}".strip() if previous else line
        index += 1
    if current is not None and current.get("correction_verbatim"):
        records.append(current)
    for record in records:
        if record.get("correction_verbatim"):
            record["correction_verbatim"] = record["correction_verbatim"][:1200]
    return records


_ADVISORY_ID = re.compile(r"^([A-Z]{2,6}\d{1,3})$")
_TI_FIELD = re.compile(r"^(Category|Function|Description|Workaround)\s*:?\s*$", re.I)
_TI_MATRIX_HEADER = re.compile(r"^Errata\s+Number$", re.I)
_TI_REVISION = re.compile(r"^Rev\.?\s*([A-Z])$", re.I)
_TI_MARK = "✓"
# TI errata title block: "Errata\n<DEVICE> Microcontroller\nABSTRACT"
_DEVICE_TITLE = re.compile(
    r"^(?:\S.{2,50}\s+)?(?:Microcontrollers?|Microprocessors?|Processors?|"
    r"Controllers?|MCUs?|SoCs?|Device Famil(?:y|ies)|Data\s+Converters?|"
    r"Amplifiers?|Modules?|Transceivers?|Sensors?)\s*$",
    re.I,
)
_DEVICE_TOKENS = re.compile(
    r"\b([A-Z][A-Z0-9]{2,12}\d[A-Z0-9]{0,12}(?:-[A-Z0-9]+)?)\b"
)


def device_scope(front_text: str, filename: str) -> dict[str, Any]:
    """The document's own device claim: its title device line and the
    concrete device tokens it prints (family tokens excluded from guesses —
    only what is on the page). This is what binds an errata doc to parts."""

    lines = [" ".join(l.split()) for l in (front_text or "").splitlines() if l.strip()]
    title_line = None
    for line in lines[:15]:
        if _DEVICE_TITLE.match(line) and not line.lower().startswith(
            ("abstract", "errata", "known", "introduction")
        ):
            title_line = line
            break
    tokens = sorted({m.group(1) for m in _DEVICE_TOKENS.finditer(" ".join(lines[:15]))})
    return {
        "device_title_verbatim": title_line,
        "device_tokens": tokens[:40] if tokens else [],
    }


def ti_advisory_details(lines: Sequence[str]) -> list[dict[str, Any]]:
    """TI detail sections: ADVISORY-ID heading followed by labeled fields.

    ``BCL16`` / ``BCL Module`` / ``Category`` ``Functional`` / ``Function``
    ``<title>`` / ``Description`` ``<symptom>`` / ``Workaround`` ``<fix>``.
    """

    records: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    current: dict[str, Any] | None = None
    field: str | None = None
    for line in lines:
        advisory = _ADVISORY_ID.match(line)
        label = _TI_FIELD.match(line)
        if advisory:
            existing = by_id.get(advisory.group(1))
            if existing is None:
                current = {
                    "schema": ERRATA_SCHEMA,
                    "record_kind": "issue",
                    "issue_id": advisory.group(1),
                    "title_verbatim": None,
                    "symptom_verbatim": None,
                    "workaround_verbatim": None,
                    "category": None,
                }
                by_id[advisory.group(1)] = current
                records.append(current)
            else:
                # advisory mentioned again (summary table then detail
                # section): one record, detail fields fill the earlier one
                current = existing
            field = None
            continue
        if current is None:
            continue
        if label:
            field = label.group(1).lower()
            continue
        if field == "category":
            current["category"] = line[:80]
            field = None
        elif field == "function":
            previous = current.get("title_verbatim")
            current["title_verbatim"] = f"{previous} {line}".strip() if previous else line
        elif field == "description":
            previous = current.get("symptom_verbatim")
            current["symptom_verbatim"] = f"{previous} {line}".strip() if previous else line
        elif field == "workaround":
            previous = current.get("workaround_verbatim")
            current["workaround_verbatim"] = f"{previous} {line}".strip() if previous else line
    for record in records:
        for key in ("title_verbatim", "symptom_verbatim", "workaround_verbatim"):
            if record.get(key):
                record[key] = record[key][:1200]
        if not (
            record.get("symptom_verbatim")
            or record.get("workaround_verbatim")
            or record.get("title_verbatim")
        ):
            continue
    return records


def ti_affected_matrix(
    page_words: Sequence[Sequence[tuple[float, float, float, float, str]]],
) -> dict[str, list[str]]:
    """TI summary matrix: advisory rows x revision columns, joined by
    x-position of the printed check marks — the text order alone cannot
    say which revision a mark belongs to."""

    affected: dict[str, list[str]] = {}
    for words in page_words:
        grid = [
            (float(w[0]), float(w[1]), float(w[2]), float(w[3]), str(w[4]))
            for w in words
        ]
        revision_words = [
            word for word in grid if word[4] == "Rev" or _TI_REVISION.match(word[4])
        ]
        rows = [word for word in grid if _ADVISORY_ID.match(word[4])]
        marks = [word for word in grid if word[4] == _TI_MARK]
        if not (revision_words and rows and marks):
            continue
        columns: list[tuple[float, str]] = []
        for word in grid:
            if _TI_REVISION.match(word[4]):
                columns.append(((word[0] + word[2]) / 2.0, _TI_REVISION.match(word[4]).group(1)))
                continue
            # stacked header: bare letter with "Rev" printed directly beneath
            if len(word[4]) == 1 and word[4].isalpha() and word[4].isupper():
                center = (word[0] + word[2]) / 2.0
                beneath = [
                    other
                    for other in grid
                    if other[4] == "Rev"
                    and abs((other[0] + other[2]) / 2.0 - center) < 6.0
                    and 0.0 <= other[1] - word[3] <= 14.0
                ]
                if beneath:
                    columns.append((center, word[4]))
        if not columns:
            continue
        rows = sorted(rows, key=lambda word: word[1])
        for mark in marks:
            center_y = (mark[1] + mark[3]) / 2.0
            center_x = (mark[0] + mark[2]) / 2.0
            nearest = min(rows, key=lambda row: abs((row[1] + row[3]) / 2.0 - center_y))
            if abs((nearest[1] + nearest[3]) / 2.0 - center_y) > 12.0:
                continue
            letter = min(columns, key=lambda column: abs(column[0] - center_x))[1]
            affected.setdefault(nearest[4], set()).add(f"Rev {letter}")
    return {name: sorted(revs) for name, revs in affected.items()}


__all__ = [
    "ERRATA_SCHEMA",
    "clarification_records",
    "device_scope",
    "errata_records",
    "ti_advisory_details",
    "ti_affected_matrix",
]
