from __future__ import annotations

from harness.electronics import interface_counts as ic


def _by_field(rows):
    return {r["field"]: r for r in rows}


# --- printed counts ---------------------------------------------------------

def test_2x_can_fd_is_a_printed_count_with_qualifier() -> None:
    rows = _by_field(ic.interface_counts_from_text("2x CAN-FD interfaces"))
    assert rows["n_can"]["value"] == 2.0
    assert rows["n_can"]["count_basis"] == "printed_count"
    assert rows["n_can"]["qualifiers"].get("can_fd") is True


def test_three_usarts_is_printed_count() -> None:
    rows = _by_field(ic.interface_counts_from_text("The MCU provides 3 USARTs"))
    assert rows["n_uart"]["value"] == 3.0
    assert rows["n_uart"]["count_basis"] == "printed_count"


def test_4_spi() -> None:
    rows = _by_field(ic.interface_counts_from_text("4 SPI channels"))
    assert rows["n_spi"]["value"] == 4.0


def test_can_2_0b_revision_is_not_a_port_count() -> None:
    # CAN 2.0B is a protocol revision, not "two CAN" ports.
    rows = _by_field(ic.interface_counts_from_text("CAN 2.0B controller"))
    assert rows.get("n_can", {}).get("value") != 2.0


def test_usb_2_0_is_one_bus_not_two() -> None:
    rows = _by_field(
        ic.interface_counts_from_text("USB 2.0 full-speed device interface")
    )
    assert rows["n_usb"]["value"] == 1.0
    assert rows["n_usb"]["count_basis"] == "singular_presence"
    assert rows["n_usb"]["qualifiers"]["usb_version"] == "2.0"
    assert rows["n_usb"]["qualifiers"]["usb_speed"] == "full-speed"


# --- presence vs no-claim ---------------------------------------------------

def test_bare_usb_without_noun_is_not_a_claim() -> None:
    rows = ic.interface_counts_from_text("This document discusses USB.")
    assert all(r["field"] != "n_usb" for r in rows)


def test_plural_noun_without_numeral_is_not_a_claim() -> None:
    # "USB ports" (plural) with no printed number is not a count.
    rows = ic.interface_counts_from_text("The device has USB ports available.")
    assert all(r["field"] != "n_usb" for r in rows)


def test_singular_presence_claim() -> None:
    rows = _by_field(ic.interface_counts_from_text("integrated CAN controller"))
    assert rows["n_can"]["count_basis"] == "singular_presence"


# --- provenance -------------------------------------------------------------

def test_every_row_carries_verbatim_span_and_method() -> None:
    text = "2x CAN-FD, 3 USARTs, USB 2.0 high-speed interface"
    for row in ic.interface_counts_from_text(text):
        assert row["verbatim"] and row["verbatim"] in text
        assert isinstance(row["span"], list) and len(row["span"]) == 2
        assert row["method"] == "deterministic_interface_v1"
        assert row["schema"] == ic.INTERFACE_SCHEMA


def test_values_are_plausible_small_counts() -> None:
    rows = ic.interface_counts_from_text("2x CAN-FD")
    for row in rows:
        assert 1 <= row["value"] <= 64


# --- document grain ---------------------------------------------------------

def test_from_pages_tags_page_and_collapses_presence_under_count() -> None:
    pages = [
        "Introduction to the USB subsystem",        # presence only, page 1
        "2x USB 2.0 high-speed interfaces",         # printed count, page 2
    ]
    rows = ic.interface_counts_from_pages(pages)
    usb = [r for r in rows if r["field"] == "n_usb"]
    assert len(usb) == 1
    assert usb[0]["value"] == 2.0
    assert usb[0]["count_basis"] == "printed_count"
    assert usb[0]["page_1based"] == 2


def test_empty_text_yields_no_rows() -> None:
    assert ic.interface_counts_from_text("") == []


# --- held queue -------------------------------------------------------------

def test_token_without_count_is_held_for_second_pass() -> None:
    held = ic.interface_held_from_pages(["The system supports USB."])
    fields = {h["field"] for h in held}
    assert "n_usb" in fields
    h = next(h for h in held if h["field"] == "n_usb")
    assert h["reason"] == "token_without_printed_count"
    assert h["quotes"]


def test_claimed_field_is_not_held() -> None:
    held = ic.interface_held_from_pages(["2x CAN-FD"])
    assert all(h["field"] != "n_can" for h in held)


# --- protocols_present ------------------------------------------------------

def test_protocols_present_counts_tokens() -> None:
    counts = ic.protocols_present("USB and USB again, plus CAN")
    assert counts.get("n_usb") == 2
    assert counts.get("n_can") == 1


# --- verify_proposal (TP4 gate) ---------------------------------------------

def test_verify_proposal_accepts_numeral_printed_in_cited_verbatim() -> None:
    pages = ["2x CAN-FD interfaces"]
    proposal = {"field": "n_can", "value": 2, "verbatim": "2x CAN-FD interfaces",
                "page_1based": 1}
    out = ic.verify_proposal(proposal, pages)
    assert out.get("status") == "verified"


def test_verify_proposal_rejects_unprinted_numeral() -> None:
    pages = ["The part features CAN interfaces"]
    proposal = {"field": "n_can", "value": 5, "verbatim": "5 CAN interfaces",
                "page_1based": 1}
    out = ic.verify_proposal(proposal, pages)
    assert out.get("status") != "verified"


def test_verify_proposal_rejects_verbatim_not_on_page() -> None:
    pages = ["2x CAN-FD interfaces"]
    proposal = {"field": "n_can", "value": 2, "verbatim": "2x CAN-FD interfaces",
                "page_1based": 9}
    out = ic.verify_proposal(proposal, pages)
    assert out.get("status") != "verified"

def test_worded_quantities_count(capsys=None):
    # hw-tpk: "dual CAN" was under-counted as 1.
    for text, field, value in [
        ("dual CAN", "n_can", 2.0),
        ("dual-channel CAN", "n_can", 2.0),
        ("two CAN interfaces", "n_can", 2.0),
        ("triple USART", "n_uart", 3.0),
        ("quad SPI", "n_spi", 4.0),
    ]:
        rows = _by_field(ic.interface_counts_from_text(text))
        assert rows[field]["value"] == value, text
        assert rows[field]["count_basis"] == "printed_count", text


def test_worded_quantifier_does_not_reach_a_distant_protocol():
    # "dual-core MCU with CAN" is ONE CAN, not two.
    rows = _by_field(ic.interface_counts_from_text("dual-core MCU with CAN"))
    assert rows["n_can"]["count_basis"] == "singular_presence"
    assert rows["n_can"]["value"] == 1.0
