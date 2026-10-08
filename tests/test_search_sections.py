"""Section-grain unit tests (PRD R1 grain 2, substrate prose index)."""

from __future__ import annotations

import json
import sqlite3

from harness.search import indexer, units


def _payload():
    return {
        "sha256": "a" * 64,
        "filename": "SCT4036DWAHR.pdf",
        "page_count": 2,
        "extractor": "pymupdf",
        "pages": [
            {"page": 1, "label": "1", "text":
                "SCT4036DWAHR\nAutomotive Grade SiC power MOSFET with WiFi "
                "connectivity for industrial IoT gateways and sensor nodes. "
                * 3},
            {"page": 2, "label": "2", "text":
                "Description\n"
                "The SCT4036DWAHR is an automotive-grade silicon carbide "
                "power MOSFET designed for high-efficiency converters in "
                "on-board chargers and industrial power supplies. "
                * 4},
        ],
    }


def _pipeline_db(tmp_path):
    path = tmp_path / "pipeline.db"
    con = sqlite3.connect(path)
    con.executescript(
        "CREATE TABLE jobs (id INTEGER PRIMARY KEY, source_path TEXT);"
        "CREATE TABLE results (id INTEGER PRIMARY KEY, job_id INTEGER,"
        " document_sha256 TEXT, output TEXT);")
    con.execute("INSERT INTO jobs VALUES (1,"
                " '/Volumes/M5_4TB/vault/landing/microchip.com/x.pdf')")
    con.execute("INSERT INTO results VALUES (1, 1, ?, ?)",
                ("a" * 64, json.dumps(_payload())))
    con.commit()
    con.close()
    return str(path)


def test_section_units_front_matter_and_prose(tmp_path):
    con = sqlite3.connect(f"file:{_pipeline_db(tmp_path)}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    out = list(indexer.section_units(con))
    assert out
    headings = {u["locator"]["heading"] for u in out}
    assert "front-matter" in headings
    assert any("Description" in h for h in headings)
    for u in out:
        assert u["grain"] == "section"
        assert u["page"] in (1, 2)
        assert units.unit_evidence_grade(u) == "evidence_grade"
        assert u["applicability"] == []          # never minted here
        assert u["vendor"] == "microchip.com"    # from the path segment
    fm = [u for u in out if u["locator"]["heading"] == "front-matter"][0]
    assert "WiFi" in fm["text_repr"]             # the vocabulary layer
    assert "span" in fm["locator"]


def test_index_sections_idempotent(tmp_path):
    search = units.connect(str(tmp_path / "s.db"))
    pipeline = _pipeline_db(tmp_path)
    first = indexer.index_sections(search, pipeline)
    again = indexer.index_sections(search, pipeline)
    assert first["units"] == again["units"] > 0
    assert units.unit_count(search) == first["units"]
    # section units are searchable by their prose
    r = __import__("harness.search.query", fromlist=["search"]).search(
        search, "silicon carbide automotive power converter", limit=5,
        with_interpretations=False)
    assert r["units"]
    assert all(u["grain"] == "section" for u in r["units"])


def test_heading_gate_rejects_sentences(tmp_path):
    """A long sentence that merely contains 'applications' is not a
    heading (the 31-units/doc over-slice regression)."""
    text = ("For power sensitive applications, the device provides several "
            "modes with balanced consumption and response time, selected by "
            "writing a register bit. " * 3)
    assert indexer._slice_sections(text) == []
    assert indexer._slice_sections("Features\n" + "x " * 150)