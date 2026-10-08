"""Taxonomy + facet tests (category breakdown, 2026-10-08)."""

from __future__ import annotations

from harness.search import facets, taxonomy, units


def test_classify_power_subcategories():
    assert taxonomy.classify_text("600V CoolMOS power MOSFET")[0] == "power"
    assert taxonomy.classify_text("600V CoolMOS power MOSFET")[1] == \
        "discrete-mosfet"
    assert taxonomy.classify_text("Low-dropout linear regulator LDO")[1] == \
        "ldo-regulators"
    assert taxonomy.classify_text("Synchronous buck converter")[1] == \
        "switching-regulators"
    assert taxonomy.classify_text("Low-side gate driver")[1] == "gate-drivers"


def test_classify_mcu_and_connectors():
    assert taxonomy.classify_text("WiFi SoC for IoT") == ("mcu", "wireless-soc")
    assert taxonomy.classify_text("Arm Cortex-M3 microcontroller")[0] == "mcu"
    assert taxonomy.classify_text("Board-to-board connector 12 positions")[0] \
        == "connectors"


def test_classify_fail_closed():
    assert taxonomy.classify_text("AN14908 how to implement a keyboard") == \
        (None, None)
    assert taxonomy.classify_text("") == (None, None)


def test_path_hint_used_when_text_silent():
    assert taxonomy.classify(existing_category=None, text="overview",
                             source_path="/mcu/nxp.com/x.pdf") == ("mcu", None)


def test_backfill_and_facets(tmp_path):
    con = units.connect(str(tmp_path / "s.db"))
    for i, text in enumerate([
            "power MOSFET 60V coolmos", "low dropout LDO regulator",
            "wifi microcontroller soc", "acme connector header positions",
            "AN123 some indecipherable application note"]):
        u = units.make_unit(
            doc_sha256=f"{i:064x}", grain="section",
            locator={"kind": "section", "heading": "front-matter",
                     "page": 1},
            text_repr=text, extraction_version="v1", page=1,
            vendor="infineon.com")
        units.replace_document(con, [u])
    stats = facets.backfill(con)
    assert stats["classified_active"] == 4  # the app note stays unclassified
    f = facets.facets(con)
    assert f["cohort"] == 5
    cats = {v["value"]: v["count"] for v in f["facets"]["category"]}
    assert cats["power"] == 2 and cats["mcu"] == 1 and \
        cats["connectors"] == 1 and cats["(unclassified)"] == 1
    # narrowing the cohort
    power = facets.facets(con, {"category": "power"})
    assert power["cohort"] == 2
    # already-chosen dimension not re-offered
    assert "category" not in power["facets"]
    # evidence_grade split surfaced per value
    assert all("evidence_grade" in v for v in power["facets"]["grain"])