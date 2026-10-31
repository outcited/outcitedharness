"""hw-wk9 fail-closed: swallowed extraction failures must surface.

Each test monkeypatches an extractor to raise and asserts the counter,
page + reason, and PARTIAL completeness flag appear on the artifact.
"""

import shutil
import sys
from pathlib import Path

import pytest
import pymupdf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from harness.electronics.bom_extract import bom_from_document  # noqa: E402
from harness.electronics.power_datasheet import read_characteristic_tables  # noqa: E402
from harness.pipeline.extraction_health import ExtractionFailures, summarize  # noqa: E402
from harness.pipeline.region_detect import regions_for_page  # noqa: E402


class _BoomPage:
    number = 0
    rect = pymupdf.Rect(0, 0, 300, 400)

    def get_text(self, *args, **kwargs):
        return ""

    def find_tables(self):
        raise RuntimeError("detector exploded")


class _BoomDoc:
    page_count = 1

    def __getitem__(self, index):
        return _BoomPage()


def test_ledger_records_and_flags_partial():
    failures = ExtractionFailures()
    assert failures.completeness == "complete"
    failures.record(7, "find_tables", RuntimeError("boom"))
    summary = failures.summary()
    assert summary["completeness"] == "partial"
    assert summary["failure_count"] == 1
    assert summary["failures"][0]["page"] == 7
    assert summary["failures"][0]["where"] == "find_tables"
    assert "boom" in summary["failures"][0]["reason"]


def test_bom_artifact_carries_partial_flag():
    artifact = bom_from_document(_BoomDoc())
    assert artifact["completeness"] == "partial"
    assert artifact["failure_count"] == 1
    assert artifact["failures"][0]["page"] == 1
    assert artifact["failures"][0]["where"] == "find_tables"


def test_characteristic_tables_records_page_failure():
    failures = ExtractionFailures()
    facts = read_characteristic_tables(_BoomDoc(), failures=failures)
    assert facts == []
    assert failures.completeness == "partial"
    event = failures.events[0]
    assert event["page"] == 1
    assert event["where"] == "find_tables"


def test_region_detect_failure_is_recorded_not_silent():
    failures = ExtractionFailures()
    regions = regions_for_page(_BoomPage(), "", failures=failures)
    assert regions[0]["source"] == "fullpage"  # degraded, but not silently
    assert failures.completeness == "partial"
    assert failures.events[0]["where"] == "region_detect"


def test_incoming_count_never_fabricates_999(monkeypatch):
    import extract_dispatch

    monkeypatch.setattr(extract_dispatch, "sh", lambda cmd: (1, ""))
    assert extract_dispatch.incoming_count("dead-host") is None


def test_push_batch_local_reports_dropped_jobs(monkeypatch, capsys):
    import extract_dispatch

    def _boom(src, dst):
        raise OSError("disk on fire")

    monkeypatch.setattr(shutil, "copy2", _boom)
    failed = extract_dispatch.push_batch_local([{"source_path": "/tmp/nope.pdf"}])
    assert failed == ["/tmp/nope.pdf"]
    assert "COPY FAILED" in capsys.readouterr().out


def _tiny_pdf(tmp_path: Path) -> Path:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "VCC = 5 V  RUN  2 mA")
    path = tmp_path / "tiny.pdf"
    doc.save(str(path))
    doc.close()
    return path


def test_curve_table_facts_surface_failures(monkeypatch, tmp_path):
    import extract_typical_curves as etc

    pdf = _tiny_pdf(tmp_path)

    def _boom(self, *args, **kwargs):
        raise RuntimeError("no tables for you")

    monkeypatch.setattr(pymupdf.Page, "find_tables", _boom)
    facts, events = etc._table_facts(pdf)
    assert facts == []
    assert events, "swallowed failure must produce an event"
    assert summarize(events)["completeness"] == "partial"
    assert any(e["where"] == "find_tables" and e["page"] == 1 for e in events)


def test_facts_cached_round_trips_failure_summary(monkeypatch, tmp_path):
    import extract_typical_curves as etc

    pdf = _tiny_pdf(tmp_path)
    monkeypatch.setenv("FACTS_CACHE_DIR", str(tmp_path / "cache"))

    def _boom(self, *args, **kwargs):
        raise RuntimeError("no tables for you")

    monkeypatch.setattr(pymupdf.Page, "find_tables", _boom)
    payload = etc._facts_cached(pdf, "deadbeef")
    assert payload["failures"]["completeness"] == "partial"
    # cached copy keeps the flag (pre-hw-wk9 caches degrade gracefully)
    etc._FACTS_CACHE.clear()
    payload2 = etc._facts_cached(pdf, "deadbeef")
    assert payload2["failures"]["completeness"] == "partial"
