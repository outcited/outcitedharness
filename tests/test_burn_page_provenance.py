"""The provenance chain fix — page + sha on burn claims.

Law (the sellable promise): every claim resolves to quote + page + sha, or
the absence is named. These tests hold both halves: the chain is stamped
when locatable, and NEVER invented when not.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from burn_aisle import _join_with_page_spans, _page_for  # noqa: E402
from fold_strata import _burn_rows  # noqa: E402


def _sub():
    return {
        "sha256": "abc123",
        "pages": [
            {"page": "1", "text": "PART OVERVIEW"},
            {"page": "2", "text": "RDS(on) max 1.2 mOhm at VGS=10"},
            {"page": "3", "text": "THERMAL DATA"},
        ],
    }


def test_join_is_byte_identical_to_before():
    sub = _sub()
    text, _ = _join_with_page_spans(sub)
    assert text == "\n".join(p["text"] for p in sub["pages"])[:28000]


def test_page_maps_to_the_right_page():
    sub = _sub()
    text, spans = _join_with_page_spans(sub)
    assert _page_for("PART OVERVIEW", text, spans) == "1"
    assert _page_for("1.2 mOhm", text, spans) == "2"
    assert _page_for("THERMAL DATA", text, spans) == "3"


def test_negative_unlocatable_quote_gets_no_page():
    sub = _sub()
    text, spans = _join_with_page_spans(sub)
    # Not in the document at all — the page is None, never guessed.
    assert _page_for("NOT IN THE DOC", text, spans) is None
    assert _page_for("", text, spans) is None


def test_negative_quote_beyond_cap_gets_no_page():
    sub = {"sha256": "x", "pages": [{"page": "1", "text": "A" * 27990},
                                    {"page": "2", "text": "BEYOND THE CAP"}]}
    text, spans = _join_with_page_spans(sub, cap=28000)
    assert _page_for("BEYOND THE CAP", text, spans) is None  # never landed in text
    assert _page_for("A" * 10, text, spans) == "1"


def test_fold_passes_chain_through_and_names_absence(tmp_path):
    import json
    doc = {
        "stem": "acme-ABC1",
        "claim_data": [
            {"symbol": "RDS(ON)", "value": 1.2, "quote": "1.2 mOhm",
             "row_header": "RDS(on)", "column_header": "max",
             "page": "2", "doc_sha256": "abc123"},
            {"symbol": "VDSS", "value": 40, "quote": "40 V"},  # old row, no chain
        ],
    }
    (tmp_path / "acme-ABC1.json").write_text(json.dumps(doc))
    rows = list(_burn_rows(tmp_path))
    assert rows, "one row per stem"
    _, rec = rows[0]
    chained, old = rec["claims"]
    assert chained["source"]["page"] == "2"
    assert chained["source"]["doc_sha256"] == "abc123"
    assert chained["source"]["sha"] == "abc123"
    # Absence stays absence — an old row without the chain keeps saying None.
    assert old["source"]["page"] is None
    assert old["source"]["doc_sha256"] is None
