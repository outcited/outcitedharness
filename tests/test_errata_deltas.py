from __future__ import annotations

from harness.electronics.errata_deltas import (
    clarification_records,
    errata_records,
)

ISSUE_PAGE = """
2.2.1
Interrupts May Be Lost When Writing the Timer Registers in the Asynchronous Timer
The interrupt will be lost if writing a timer register that is a synchronous timer clock when the asynchronous Timer/Counter register (TCNTx) is 0x00.
Work Around
Always check that the asynchronous Timer/Counter register neither has the value 0xFF nor 0x00 before writing to the asynchronous Timer Control Register (TCCRx).
Affected Silicon Revisions
ATmega169A/PA
Rev. I
Rev. J
Rev. K
X
X
X
ATmega329A/PA/3290A/PA
Rev. D
X
"""

CLARIFICATION_PAGE = """
3.
Data Sheet Clarifications
3.2
Power Management and Sleep Modes
A clarification has been made to the Active clock domains and wake-up sources in the different sleep modes table to make the headings visible.
"""


def test_errata_issue_records_extract_delta_fields():
    records = errata_records([ISSUE_PAGE])
    assert len(records) == 1
    record = records[0]
    assert record["issue_id"] == "2.2.1"
    assert record["title_verbatim"].startswith("Interrupts May Be Lost")
    assert record["symptom_verbatim"].startswith("The interrupt will be lost")
    assert record["workaround_verbatim"].startswith("Always check")
    assert "0x00" in record["symptom_verbatim"]
    silicon = record["affected_silicon"]
    assert "ATmega169A/PA" in silicon
    assert silicon["ATmega169A/PA"] == ["I", "J", "K"]
    assert silicon["ATmega329A/PA/3290A/PA"] == ["D"]


def test_errata_records_none_without_numbered_issue():
    assert errata_records(["Some introductory prose only."]) == []
    assert clarification_records(["Introduction prose."]) == []


def test_clarification_records_keep_correction_verbatim():
    records = clarification_records([CLARIFICATION_PAGE])
    assert len(records) == 1
    record = records[0]
    assert record["record_kind"] == "clarification"
    assert record["issue_id"] == "3.2"
    assert record["title_verbatim"] == "Power Management and Sleep Modes"
    assert "clarification has been made" in record["correction_verbatim"]


def test_issue_boundary_flushes_between_issues():
    pages = [
        ISSUE_PAGE,
        "2.3.1\nSecond Issue Title\nSecond symptom text.\nWork Around\nDo the thing.\n",
    ]
    records = errata_records(pages)
    assert [record["issue_id"] for record in records] == ["2.2.1", "2.3.1"]
    assert records[1]["symptom_verbatim"] == "Second symptom text."
    assert records[1]["workaround_verbatim"] == "Do the thing."
    assert records[1]["affected_silicon"] == {}
