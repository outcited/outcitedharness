"""Deterministic interface-count extraction (n_can, n_usb, n_uart, n_spi, n_i2c).

The pillar lane already prints SPI/I2C/UART/USB/CAN counts from front pages.
This module is the fleet-corpus reader: the same fields, read from every page
(EXTRACTION PIPELINE SPEC v1.0 §1 — full document, never first-N pages), with
a wider grammar and a strict no-invention rule.

Two kinds of claim are emitted, and the basis is carried on every row:

``printed_count``
    A numeral is printed as the count: ``2x CAN-FD``, ``3 USARTs``,
    ``USB: 2``, ``4 SPI``. The numeral is copied verbatim; it is never
    derived from a version or a speed grade (``USB 2.0`` is one bus, not two
    ports; ``CAN 2.0B`` is a protocol revision).

``singular_presence``
    A singular count noun names one instance: ``USB 2.0 full-speed device
    interface``, ``CAN-FD controller``. ``USB``/``CAN`` alone, ``CAN bus``,
    and plural nouns without a numeral (``USB ports``) are **not** claims —
    they go to the held queue for the TP4 second pass.

USB speed/version (``1.1``/``2.0``/``FS``/``HS``/``SS``) and CAN-FD are
captured as qualifiers on the claim, never converted into counts.

Every row carries page + exact enclosing line + character span, so an L2
check can re-read the same bytes. Fields match ``pillar_facts`` exactly, so
this reader drops into the existing burn/pillar lane unchanged.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Iterable, Mapping, Sequence


INTERFACE_SCHEMA = "harness.electronics-interface-counts.v1"

# protocol -> (canonical field, token pattern). Order matters only for the
# display of held rows; patterns are applied independently.
_PROTOCOLS: tuple[tuple[str, str, str], ...] = (
    ("can", "n_can", r"CAN(?:\s*[-\s]?\s*FD)?|FDCAN|MCAN|DCAN|bxCAN"),
    ("usb", "n_usb", r"USB"),
    ("uart", "n_uart", r"LPUART|USART|UART|SCI"),
    ("spi", "n_spi", r"SPI"),
    ("i2c", "n_i2c", r"I2C|I\u00b2C|IIC|TWI"),
)

_FIELD_PROTOCOL = {field: proto for proto, field, _ in _PROTOCOLS}

# Protocol token seen in a page, cheap gate for the full-document scan.
# Plural forms ("3 USARTs", "SPIs") must pass the gate too, or a page with a
# printed count is skipped before the explicit patterns ever run.
_GATE = re.compile(
    r"\b(?:CAN|FDCAN|MCAN|DCAN|bxCAN|USB|UART|USART|LPUART|SCI|SPI|I2C|I\u00b2C|IIC|TWI)s?\b",
    re.I,
)

# Count nouns: only these make a singular presence claim. Plural forms are
# deliberately absent (``ports``/``controllers`` require a printed numeral).
_SINGULAR_NOUN = (
    r"(?:interface|port|controller|peripheral|module|channel|instance|"
    r"transceiver|device|phy|host|function|core|engine)"
)
# Tokens allowed between the protocol and its count noun ("USB 2.0 full-speed
# device interface"). Commas/parentheses break the window on purpose.
_NOUN_WINDOW = r"(?:\s+[A-Za-z0-9.+\u00b5\u03bc/-]+){0,5}?"

_UNIT_AFTER_NUMBER = re.compile(
    r"\s*(?:MHz|kHz|GHz|Mbps|kbps|Gbps|Mbit|kbit|V|mV|mA|\u00b5A|uA|A|W|mW|"
    r"bit|bits|kb|KB|MB|GB|%|\u00b0C)\b",
    re.I,
)

_USB_SPEED = re.compile(
    r"\b(full[- ]speed|high[- ]speed|low[- ]speed|super[- ]speed|FS|HS|LS|SS)\b",
    re.I,
)
_USB_VERSION = re.compile(r"\bUSB\s*(\d\.\d)\b", re.I)
_CAN_FD = re.compile(r"CAN\s*[-\s]?\s*FD|FDCAN|flexible\s+data", re.I)

_PLAUSIBLE = range(1, 65)


def _norm(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text or "").split())


def _line_bounds(src: str, start: int, end: int) -> tuple[int, int]:
    ls = src.rfind("\n", 0, start) + 1
    le = src.find("\n", end)
    if le == -1:
        le = len(src)
    return ls, le


def _line(src: str, start: int, end: int) -> str:
    ls, le = _line_bounds(src, start, end)
    line = " ".join(src[ls:le].split())
    if len(line) > 220:
        rel = start - ls
        cut = max(0, rel - 100)
        line = line[cut : cut + 220]
    return line


def _qualifiers(src: str, start: int, end: int, proto: str) -> dict[str, Any]:
    ls, le = _line_bounds(src, start, end)
    window = src[ls:le]
    out: dict[str, Any] = {}
    if proto == "usb":
        version = _USB_VERSION.search(window)
        if version:
            out["usb_version"] = version.group(1)
        speed = _USB_SPEED.search(window)
        if speed:
            out["usb_speed"] = speed.group(1).lower().replace(" ", "-")
    elif proto == "can":
        if _CAN_FD.search(window):
            out["can_fd"] = True
    return out


def _explicit_patterns(proto: str) -> tuple[re.Pattern[str], ...]:
    token = f"(?:{proto})"
    return (
        # 2x CAN-FD / 2 x CAN / 4×SPI
        re.compile(rf"(?<![\d.\-])(\d{{1,2}})\s*[x\u00d7]\s*\b{token}\b", re.I),
        # 3 USARTs / 2 CAN-FD / 4 SPI / 2-channel CAN
        re.compile(
            rf"(?<![\d.\-])(\d{{1,2}})(?:\s+|\s*[- ]channel\s+)\b{token}s?\b",
            re.I,
        ),
        # CAN: 2 / UART 2 / SPI x 4 / USB (2)
        re.compile(
            rf"\b{token}\s*(?::|[-\u2013=]|x|\u00d7)\s*(\d{{1,2}})(?![\d.\-])",
            re.I,
        ),
        re.compile(rf"\b{token}\s*[\(\[]\s*(\d{{1,2}})\s*[\)\]]", re.I),
    )


def _presence_pattern(proto: str) -> re.Pattern[str]:
    return re.compile(
        rf"\b{proto}{_NOUN_WINDOW}\s+{_SINGULAR_NOUN}\b", re.I
    )


def _explicit_hits(text: str, proto: str) -> list[tuple[int, int, int]]:
    """(value, start, end) for every printed count of one protocol."""
    hits: list[tuple[int, int, int]] = []
    for pattern in _explicit_patterns(proto):
        for match in pattern.finditer(text):
            digit = next((g for g in match.groups() if g and g.isdigit()), None)
            if digit is None:
                continue
            value = int(digit)
            if value not in _PLAUSIBLE:
                continue
            after = text[match.end() : match.end() + 12]
            if _UNIT_AFTER_NUMBER.match(after) and not re.match(
                r"\s*(?:interfaces?|ports?|controllers?|peripherals?|modules?|"
                r"channels?|instances?|transceivers?|devices?)",
                after,
                re.I,
            ):
                continue
            hits.append((value, match.start(), match.end()))
    return hits


_CLAUSE_BREAK = re.compile(
    r"\b(?:is|are|was|were|be|been|provides?|supports?|offers?|includes?|"
    r"features?|with|and|or|the|a|an)\b",
    re.I,
)

def _presence_hits(text: str, proto: str, explicit_lines: set[int]) -> list[tuple[int, int, int]]:
    hits: list[tuple[int, int, int]] = []
    pattern = _presence_pattern(proto)
    for match in pattern.finditer(text):
        line_start = text.rfind("\n", 0, match.start()) + 1
        if line_start in explicit_lines:
            continue  # a numeral on the line is the stronger claim
        window = match.group(0)
        if _CLAUSE_BREAK.search(window):
            continue  # comparative/prose sentence, not a part capability
        hits.append((1, match.start(), match.end()))
    return hits


def _merge(
    text: str,
    proto: str,
    field: str,
    hits: Iterable[tuple[int, int, int, str]],
) -> list[dict[str, Any]]:
    """Collapse identical values on a page into one row with a print count."""
    by_value: dict[int, dict[str, Any]] = {}
    for value, start, end, basis in hits:
        row = by_value.get(value)
        if row is None:
            row = {
                "schema": INTERFACE_SCHEMA,
                "field": field,
                "value": float(value),
                "unit": "count",
                "verbatim": _line(text, start, end),
                "count_basis": basis,
                "span": [start, end],
                "qualifiers": _qualifiers(text, start, end, proto),
                "prints": 0,
                "method": "deterministic_interface_v1",
            }
            by_value[value] = row
        row["prints"] += 1
    rows: list[dict[str, Any]] = []
    for value in sorted(by_value):
        row = by_value[value]
        base = 0.95 if row["count_basis"] == "printed_count" else 0.90
        if row["prints"] > 1:
            base += 0.03
        row["confidence"] = round(min(base, 0.99), 2)
        rows.append(row)
    return rows


def interface_counts_from_text(text: str) -> list[dict[str, Any]]:
    """Interface-count claims from one page/block of printed text."""
    if not text or not _GATE.search(text):
        return []
    rows: list[dict[str, Any]] = []
    for proto, field, token in _PROTOCOLS:
        explicit = _explicit_hits(text, token)
        explicit_lines = {
            text.rfind("\n", 0, start) + 1 for _, start, _ in explicit
        }
        hits: list[tuple[int, int, int, str]] = [
            (value, start, end, "printed_count")
            for value, start, end in explicit
        ]
        hits.extend(
            (value, start, end, "singular_presence")
            for value, start, end in _presence_hits(text, token, explicit_lines)
        )
        rows.extend(_merge(text, proto, field, hits))
    return rows


def _collapse_document(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A printed numeral outranks singular presence for the same field."""
    explicit_fields = {
        row["field"] for row in rows if row["count_basis"] == "printed_count"
    }
    return [
        row
        for row in rows
        if row["count_basis"] == "printed_count" or row["field"] not in explicit_fields
    ]


def interface_counts_from_pages(page_texts: Sequence[str]) -> list[dict[str, Any]]:
    """Full-document interface counts, one row per (page, field, value)."""
    rows: list[dict[str, Any]] = []
    for page_index, text in enumerate(page_texts, 1):
        for row in interface_counts_from_text(text):
            row["page_1based"] = page_index
            rows.append(row)
    return _collapse_document(rows)


def protocols_present(text: str) -> dict[str, int]:
    """Protocol token counts on a page (the held-queue signal)."""
    counts: dict[str, int] = {}
    for proto, field, token in _PROTOCOLS:
        hits = len(re.findall(rf"\b(?:{token})\b", text, re.I))
        if hits:
            counts[field] = hits
    return counts


def interface_held_from_pages(page_texts: Sequence[str]) -> list[dict[str, Any]]:
    """Fields the document mentions but never counts — TP4 second-pass input.

    Document grain, not page grain: a field is held only when no page prints a
    count for it. Each held row carries up to two verbatim mention lines so the
    second pass reads the same context the local reader saw.
    """
    seen: dict[str, list[tuple[int, str]]] = {}
    claimed: set[str] = set()
    for page_index, text in enumerate(page_texts, 1):
        if not text:
            continue
        for field in protocols_present(text):
            if field in claimed:
                continue
            seen.setdefault(field, [])
            if len(seen[field]) >= 2:
                continue
            for proto, f, token in _PROTOCOLS:
                if f != field:
                    continue
                match = re.search(rf"\b(?:{token})\b", text, re.I)
                if match:
                    seen[field].append((page_index, _line(text, match.start(), match.end())))
                break
        for row in interface_counts_from_text(text):
            claimed.add(row["field"])
    held: list[dict[str, Any]] = []
    for field in sorted(seen):
        if field in claimed:
            continue
        pages = [page for page, _ in seen[field]]
        quotes = [quote for _, quote in seen[field]]
        held.append(
            {
                "schema": INTERFACE_SCHEMA,
                "field": field,
                "protocol": _FIELD_PROTOCOL[field],
                "reason": "token_without_printed_count",
                "pages": pages,
                "quotes": quotes,
                "method": "deterministic_interface_v1",
            }
        )
    return held


def verify_proposal(
    proposal: Mapping[str, Any],
    page_texts: Sequence[str],
) -> dict[str, Any]:
    """Deterministically verify one TP4 proposal against the source pages.

    A proposal may only become a claim when its numeral is printed in the
    cited verbatim, the verbatim is on the cited page, and the page names the
    protocol. Everything else is held with a reason — never guessed in.
    """
    recorded = dict(proposal)
    field = str(proposal.get("field") or "")
    value = proposal.get("value")
    page = proposal.get("page_1based")
    verbatim = _norm(str(proposal.get("verbatim") or ""))
    recorded["schema"] = INTERFACE_SCHEMA
    recorded["method"] = "deterministic_interface_proposal_v1"

    def hold(reason: str) -> dict[str, Any]:
        recorded["status"] = "held"
        recorded["reason"] = reason
        return recorded

    if field not in _FIELD_PROTOCOL:
        return hold("unknown_field")
    try:
        number = float(value)
    except (TypeError, ValueError):
        return hold("value_not_numeric")
    if not number.is_integer() or int(number) not in _PLAUSIBLE:
        return hold("value_out_of_range")
    if not isinstance(page, int) or page < 1 or page > len(page_texts):
        return hold("page_out_of_range")
    if not verbatim:
        return hold("missing_verbatim")
    page_text = _norm(page_texts[page - 1])
    if verbatim not in page_text:
        return hold("verbatim_not_on_page")
    proto = _FIELD_PROTOCOL[field]
    token = next(t for p, f, t in _PROTOCOLS if f == field)
    if not re.search(rf"\b(?:{token})\b", verbatim, re.I):
        return hold("verbatim_names_no_protocol")
    if not re.search(rf"(?<![\d.]){int(number)}(?![\d.])", verbatim):
        return hold("count_not_printed")
    recorded["status"] = "verified"
    recorded["reason"] = None
    return recorded


__all__ = [
    "INTERFACE_SCHEMA",
    "interface_counts_from_pages",
    "interface_counts_from_text",
    "interface_held_from_pages",
    "protocols_present",
    "verify_proposal",
]
