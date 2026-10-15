"""Regression tests for printed-pin-count extraction (PINOUT-ADMISSION-02).

Pins the three observed misfire classes from the 2026-10-15 QA round:
  1. "318-pin" class — spurious large numbers from prose/part numbers.
  2. SOT23 class — digit-embedded package names read as counts (23 pins).
  3. Cross-package class — another package's count on the same page
     (a SOT-23 mention inside an 8-pin SOP datasheet yielding 23).
Plus the legitimate forms that MUST keep working.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from pin_admission_tiers import printed_pin_count  # noqa: E402


def test_real_count_forms_parse():
    assert printed_pin_count(["16-pin TSSOP package"], "TSSOP", 16)[0] == 16
    assert printed_pin_count(["TSSOP-16 package"], "16-pin TSSOP", 16)[0] == 16
    assert printed_pin_count(["Pin count: 32"], "QFN32", 32)[0] == 32


def test_sot23_digit_name_is_not_a_count():
    # SOT23 mentioned in prose; record is a 6-pin WSON. Old regex -> 23 (false).
    page = "Available in SOT23 and SOT-23-5. This is a 24-pin WSON device."
    got = printed_pin_count([page], "24-Pin WSON", 24)
    assert got[0] != 23
    assert got[0] == 24


def test_spurious_large_number_rejected():
    # the 3/318 anomaly: prose "318" must never certify a 3-row table
    page = "per PD 318-pin provisioning notice in section 4.2"
    assert printed_pin_count([page], None, 3)[0] is None


def test_cross_package_count_ignored():
    # an 8-pin SOP datasheet mentioning SOT-89 must not certify 89
    page = "Package: SOIC\u22128. Also compatible with 3-lead SOT-89 parts."
    assert printed_pin_count([page], "SO8", 8)[0] is None or \
        printed_pin_count([page], "SO8", 8)[0] <= 8


def test_rows_surplus_marks_suspect():
    # printed 24-pin but 30 rows extracted -> surplus, completeness unverifiable
    assert printed_pin_count(["24-pin QFN package"], "QFN-24", 30) == (None, True)


def test_single_digit_counts_do_not_certify():
    # single-digit "N-pin" strings are the dominant noise class ("1-pin",
    # "2-pin" in prose); they never certify (SC-70/SOT-323 land UNCONFIRMED)
    assert printed_pin_count(["DCK Package 6-Pin SC-70"], "SC-70", 6) == (None, False)


def test_no_evidence_yields_none():
    assert printed_pin_count(["no counts here"], "DCK", 6)[0] is None


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"PASS {name}")
    print("all pin-count regression tests passed")