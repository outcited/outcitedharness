#!/usr/bin/env python3
"""Generate dw-recon::word_columns gold from the LIVE
`harness/electronics/word_columns.py`.

Every case is computed by the reference itself: `extract` cases carry the
page word lists (`get_text("words")` 5-tuples) and run `extract_pin_columns`
end to end with `_load_words` monkeypatched to replay them (including the
continuation-page lookback), `select`/`bogey`/`capability` cases call the
pure readers. Word geometry is the PDF layer — the port consumes the same
`(x0, y0, x1, y1, text)` tuples.

Cases cover the traps the extractor exists for: identifiers assigned to
package columns by x-overlap with printed headers, names matched within the
row band and joined across runs, multi-row minimums and bare `Pin` headers
demanding pure numbers, continuation pages reusing a prior page's header
band, the two-group table merged to truth, and `select_package_columns`'s
exact-match > identity-match > generic-pin-merge ladder.

Usage: python3 scripts/gold_word_columns.py > tests/fixtures/gold/word_columns_v1.json
"""
from __future__ import annotations

import contextlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import harness.electronics.word_columns as word_columns  # noqa: E402
from harness.electronics.word_columns import (  # noqa: E402
    _pipeline,
    capability_result,
    extract_pin_columns,
    package_pin_bogey,
    select_package_columns,
)


def _span(span) -> dict:
    return {"text": span.text, "bbox": list(span.bbox)}


def _column(column) -> dict:
    return {
        "header": column.header,
        "column_key": column.column_key,
        "header_span": _span(column.header_span),
        "rows": [
            {
                "pin_no": row.pin_no,
                "name": row.name,
                "identifier_span": _span(row.identifier_span),
                "name_span": _span(row.name_span),
            }
            for row in column.rows
        ],
    }


def _run_extract(name: str, pages: list[list[tuple]], page_1based: int) -> dict:
    """Replay page words through the reference's `extract_pin_columns`."""

    def loader(source, page):
        if page < 1 or page > len(pages):
            return None
        return pages[page - 1]

    original = word_columns._load_words
    word_columns._load_words = loader
    try:
        columns = extract_pin_columns(Path(f"/corpus/{name}.pdf"), page_1based)
    finally:
        word_columns._load_words = original
    return {
        "kind": "extract",
        "name": name,
        "input": {
            "page_1based": page_1based,
            "pages": [
                [[w[0], w[1], w[2], w[3], w[4]] for w in page] for page in pages
            ],
        },
        "expected": {key: _column(column) for key, column in columns.items()},
    }


# --- word-layout builders (the PDF layer's output) ---------------------------

def _word(x0, y0, x1, y1, text):
    return (float(x0), float(y0), float(x1), float(y1), text)


def _simple_table() -> list[tuple]:
    """One LQFP144 column: header band, then identifier + name rows."""
    words = [
        _word(74, 20, 120, 30, "LQFP144"),
        _word(150, 20, 180, 30, "Pin"),
        _word(250, 20, 285, 30, "Name"),
    ]
    rows = [("1", "PE2"), ("2", "PE3"), ("3", "PE4"), ("4", "PE5")]
    y = 50
    for pin, name in rows:
        words.append(_word(80, y, 95, y + 8, pin))
        words.append(_word(255, y, 285, y + 8, name))
        y += 12
    return words


def _two_group_table() -> list[tuple]:
    """Two side-by-side generic Pin/Name groups (odd/even rows)."""
    words = [
        _word(40, 20, 70, 30, "Pin"),
        _word(90, 20, 120, 30, "Name"),
        _word(240, 20, 270, 30, "Pin"),
        _word(290, 20, 320, 30, "Name"),
    ]
    left = [("1", "PA0"), ("3", "PA2"), ("5", "PA4")]
    right = [("2", "PA1"), ("4", "PA3"), ("6", "PA5")]
    y = 50
    for (lp, ln), (rp, rn) in zip(left, right):
        words.append(_word(45, y, 58, y + 8, lp))
        words.append(_word(92, y, 118, y + 8, ln))
        words.append(_word(245, y, 258, y + 8, rp))
        words.append(_word(292, y, 318, y + 8, rn))
        y += 12
    return words


def _bga_table() -> list[tuple]:
    """UFBGA176+25 with ball ids in the pin column."""
    words = [
        _word(60, 20, 120, 30, "UFBGA176+25"),
        _word(150, 20, 180, 30, "Ball"),
        _word(250, 20, 285, 30, "Name"),
    ]
    rows = [("A1", "VSS"), ("B1", "PE4"), ("C3", "PE2"), ("D5", "VDD")]
    y = 50
    for pin, name in rows:
        words.append(_word(65, y, 85, y + 8, pin))
        words.append(_word(255, y, 285, y + 8, name))
        y += 12
    return words


def _single_row_table() -> list[tuple]:
    """Only one identifier: below the two-row minimum, yields nothing."""
    words = [
        _word(74, 20, 120, 30, "LQFP144"),
        _word(250, 20, 285, 30, "Name"),
        _word(80, 50, 95, 58, "1"),
        _word(255, 50, 285, 58, "PE2"),
    ]
    return words


def _bare_pin_needs_numbers() -> list[tuple]:
    """A bare `Pin` header with ball ids beneath: rejected (needs digits)."""
    words = [
        _word(40, 20, 65, 30, "Pin"),
        _word(90, 20, 120, 30, "Name"),
    ]
    y = 50
    for pin, name in [("A1", "VSS"), ("B1", "PE4"), ("C3", "PE2")]:
        words.append(_word(42, y, 58, y + 8, pin))
        words.append(_word(92, y, 118, y + 8, name))
        y += 12
    return words


def _continuation_body() -> list[tuple]:
    """A headerless continuation body (no header band)."""
    words = []
    y = 50
    for pin, name in [("9", "PD0"), ("10", "PD1"), ("11", "PD2"), ("12", "PD3")]:
        words.append(_word(80, y, 95, y + 8, pin))
        words.append(_word(255, y, 285, y + 8, name))
        y += 12
    return words


def main() -> int:
    out: dict = {"cases": []}
    out["cases"].append(_run_extract("simple_lqfp144", [_simple_table()], 1))
    out["cases"].append(_run_extract("two_group_merge", [_two_group_table()], 1))
    out["cases"].append(_run_extract("bga_balls", [_bga_table()], 1))
    out["cases"].append(_run_extract("single_row_yields_nothing", [_single_row_table()], 1))
    out["cases"].append(_run_extract("bare_pin_needs_numbers", [_bare_pin_needs_numbers()], 1))
    # Continuation: page 2 has rows only; page 1's header band is revalidated
    # and applied.
    out["cases"].append(
        _run_extract(
            "continuation_lookback",
            [_simple_table(), _continuation_body()],
            2,
        )
    )
    # Out-of-range page: empty.
    out["cases"].append(_run_extract("page_out_of_range", [_simple_table()], 5))

    # select_package_columns over the simple table's columns.
    columns = _pipeline([_word(*w) for w in _simple_table()])
    serialized = {key: _column(column) for key, column in columns.items()}
    for name, requested, expected_headers in [
        ("exact_match", "LQFP144", ["LQFP144"]),
        ("identity_match", "LQFP-144", ["LQFP144"]),
        ("no_match_empty", "", []),
    ]:
        selected = select_package_columns(columns, requested)
        out["cases"].append(
            {
                "kind": "select",
                "name": name,
                "input": {
                    "columns": serialized,
                    "column_order": list(columns),
                    "requested_package": requested,
                },
                "expected": [_column(column) for column in selected],
            }
        )
    # The generic-merge ladder: two bare Pin groups merge to one selection.
    grouped = _pipeline([_word(*w) for w in _two_group_table()])
    grouped_serialized = {key: _column(c) for key, c in grouped.items()}
    out["cases"].append(
        {
            "kind": "select",
            "name": "generic_merge",
            "input": {
                "columns": grouped_serialized,
                "column_order": list(grouped),
                "requested_package": "LQFP100",
            },
            "expected": [
                _column(column) for column in select_package_columns(grouped, "LQFP100")
            ],
        }
    )

    for name, token in [
        ("family_first", "LQFP144"),
        ("plus_variants", "UFBGA176+25"),
        ("digits_first", "64-TQFP"),
        ("no_match", "SOT-23"),
        ("empty_token", ""),
    ]:
        bogey = package_pin_bogey(token)
        out["cases"].append(
            {
                "kind": "bogey",
                "name": name,
                "input": {"token": token},
                "expected": list(bogey) if bogey is not None else None,
            }
        )

    # capability_result over one column.
    first = next(iter(columns.values()))
    out["cases"].append(
        {
            "kind": "capability",
            "name": "simple_column",
            "input": {"column": _column(first)},
            "expected": capability_result(first),
        }
    )

    json.dump(out, sys.stdout, indent=1, sort_keys=True)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
