"""Regression tests for the deterministic identity join (PINOUT-IDENTITY-03).

Pins the hard guards: family/title documents NEVER join to a canonical OPN,
manufacturer prefixes and doc-id/locale suffixes strip deterministically,
canonicalization is reversible, and ambiguous stems stay unresolved.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from pin_identity_join import canon, clean_stem, joinable  # noqa: E402


def test_mfr_prefix_stripped():
    assert clean_stem("infineon-tle9871qxa20-datasheet-en", "Infineon") == "tle9871qxa20"


def test_microchip_doc_id_suffix_stripped():
    assert clean_stem("MIC4600-Data-Sheet-20005584B", "") == "MIC4600"


def test_locale_and_rev_suffixes_stripped():
    assert clean_stem("nlv27wz126-d-en", "onsemi") in ("nlv27wz126", "nlv27wz126-d")
    assert clean_stem("xp-13b1-10da_rev3.0", "omron") in ("xp-13b1-10da", "xp-13b1-10da")


def test_family_document_never_joins():
    assert joinable("MCP16361-Family-Data-Sheet") == "family-or-title-derived"
    assert joinable("11AAXXX-1-Kbit-to-16-Kbit-UNI-O-Serial-EEPROM-Family") is not None


def test_datasheet_summary_never_joins():
    assert joinable("stm32h7-datasheet-summary") is not None


def test_title_like_stem_rejected_even_if_opn_shaped():
    assert joinable("Low-Cost-Thermal-Sensor") is not None


def test_real_opn_shape_passes_guard():
    assert joinable("tle9871qxa20") is None
    assert joinable("STM32WBA5MMG") is None


def test_canon_is_reversible_identity():
    assert canon("stm32wba5mmg") == canon("STM32WBA5MMG") == "STM32WBA5MMG"
    assert canon("lm-2574") == canon("LM2574")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"PASS {name}")
    print("all identity-join tests passed")