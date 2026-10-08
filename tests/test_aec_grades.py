from __future__ import annotations

from harness.electronics.aec_grades import grade_rows


def test_grade_cited_emits_with_verbatim():
    pages = [
        "The device is qualified to AEC-Q100 Grade 1. See the qualification summary.",
    ]
    rows = grade_rows(pages)
    assert len(rows) == 1
    row = rows[0]
    assert row["grade"] == "1"
    assert row["qualified_statement"] is True
    assert "AEC-Q100" in row["standard_verbatim"]
    assert "Grade 1" in row["context_verbatim"]
    assert row["page_1based"] == 1


def test_qualified_without_grade_emits_with_null_grade():
    rows = grade_rows(
        ["Device compliant to AIS-Q100 (see qualification summary)."]
    )
    assert rows and rows[0]["grade"] is None
    assert rows[0]["qualified_statement"] is True
    assert "AIS" in rows[0]["standard_verbatim"]


def test_temperature_grade_wording_is_a_cited_grade():
    # AEC-Q100's own terminology: "temperature grade 1" prints the grade.
    rows = grade_rows(["Compliant to AIS-Q100, temperature grade 1 (see note)."])
    assert rows and rows[0]["grade"] == "1"


def test_plain_mention_without_grade_or_claim_never_emits():
    assert grade_rows(["Supports AEC-Q100 targets where applicable."]) == []
    assert grade_rows(["No automotive content on this page."]) == []


def test_temperature_range_alone_never_emits():
    assert grade_rows(["Operating temperature -40C to +125C, 1.8V to 3.6V."]) == []


def test_grade_colon_and_dash_variants():
    rows = grade_rows(["AEC Q100:Grade 2 qualified device"])
    assert rows and rows[0]["grade"] == "2"
    rows = grade_rows(["AEC-Q100 Rev-G Grade 3"])
    assert rows and rows[0]["grade"] == "3"


def test_subdocument_test_method_citations_stay_silent_without_grade():
    # Q100-011 (CDM) / Q100-002 (HBM) are test-method citations, not
    # qualification claims; they emit only when a grade is printed.
    assert (
        grade_rows(["Charged device model (CDM), per AEC Q100-011, all pins ±500 V"])
        == []
    )
    rows = grade_rows(["ESD per AEC-Q100-002, HBM, Grade 1 classification"])
    assert rows and rows[0]["grade"] == "1"
