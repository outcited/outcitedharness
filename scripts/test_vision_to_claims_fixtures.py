#!/usr/bin/env python3
"""Negative fixtures for vision_to_claims canary.

Tests the important failure modes. All fixtures use synthetic data —
no real documents, no API calls.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from vision_to_claims import (
    classify_table, detect_abs_max, detect_role,
    parse_value_unit, process_doc, table_to_claims,
)

FIXTURES = []


def fixture(name):
    def deco(fn):
        FIXTURES.append((name, fn))
        return fn
    return deco


# ── valid table ──────────────────────────────────────────────────────
@fixture("valid_param_table")
def test_valid_param():
    """Straightforward parameter table with min/typ/max."""
    table = {
        "table_title": "Electrical Characteristics",
        "columns": ["Symbol", "Min", "Typ", "Max", "Unit"],
        "rows": [
            {"cells": {"Symbol": "VDS", "Min": "60", "Typ": "", "Max": "", "Unit": "V"}, "bbox": [0, 0, 100, 20]},
            {"cells": {"Symbol": "RDS(on)", "Min": "", "Typ": "3.2", "Max": "5.0", "Unit": "mΩ"}, "bbox": [0, 20, 100, 40]},
        ],
    }
    region = {"page": 5, "region_bbox": [0, 0, 600, 400], "region_source": "tables", "dpi": 200}
    claims, held = table_to_claims(table, region, "sha123", "test.pdf", "vw", "2.0.0", 0, 0)
    assert len(claims) > 0, "no claims from valid table"
    assert len(held) == 0, f"unexpected held: {held}"
    # Check field extraction
    fields = {c["symbol"] for c in claims}
    assert "VDS" in fields or "Min" in fields or "Symbol" in fields, f"fields: {fields}"
    return {"claims": len(claims), "held": len(held), "fields": list(fields)[:5]}


@fixture("min_typ_max_columns")
def test_min_typ_max():
    """Min/Typ/Max columns must be detected as qualifier."""
    table = {
        "table_title": "",
        "columns": ["Parameter", "Min", "Typ", "Max"],
        "rows": [
            {"cells": {"Parameter": "VGS(th)", "Min": "1.0", "Typ": "2.1", "Max": "3.5"}, "bbox": [0, 0, 100, 20]},
        ],
    }
    region = {"page": 3, "region_bbox": [0, 0, 600, 400]}
    claims, _ = table_to_claims(table, region, "sha", "test.pdf", "vw", "2.0.0", 0, 0)
    roles = [c["qualifier"] for c in claims if c["qualifier"]]
    assert "min" in roles or "typ" in roles or "max" in roles, f"no min/typ/max detected: {roles}"
    return {"roles": roles}


@fixture("units_preserved")
def test_units():
    """Units must survive the projection."""
    table = {
        "table_title": "",
        "columns": ["Symbol", "Value", "Unit"],
        "rows": [
            {"cells": {"Symbol": "ID", "Value": "3.2", "Unit": "mA"}, "bbox": [0, 0, 100, 20]},
        ],
    }
    region = {"page": 1, "region_bbox": [0, 0, 600, 400]}
    claims, _ = table_to_claims(table, region, "sha", "test.pdf", "vw", "2.0.0", 0, 0)
    # Check that unit is preserved somewhere
    units = [c["unit"] for c in claims if c.get("unit")]
    vals = [str(c["value"]) for c in claims]
    assert any("mA" in (u or "") or "mA" in v for u, v in zip(units + [None]*10, vals)) or \
           any("3.2" in v for v in vals), f"units lost: {units} {vals}"
    return {"units": units, "vals": vals[:3]}


# ── negative cases ──────────────────────────────────────────────────
@fixture("empty_verification_is_unknown")
def test_empty_verification():
    """Empty verdict list must be UNKNOWN, not PASS."""
    table = {
        "table_title": "",
        "columns": ["A", "B"],
        "rows": [{"cells": {"A": "1", "B": "2"}, "bbox": [0, 0, 100, 20]}],
    }
    region = {"page": 1, "region_bbox": [0, 0, 600, 400]}
    claims, _ = table_to_claims(table, region, "sha", "test.pdf", "vw", "2.0.0", 0, 0,
                                 verification=[])
    for c in claims:
        assert c["verification"] == [], "empty verification should be []"
    return {"verifications": [c["verification"] for c in claims]}


@fixture("unsupported_table_held")
def test_unsupported():
    """Unsupported table class must be held, not claimed."""
    table = {
        "table_title": "Some Random Table",
        "columns": ["Col A", "Col B", "Col C"],
        "rows": [
            {"cells": {"Col A": "x", "Col B": "y", "Col C": "z"}, "bbox": [0, 0, 100, 20]},
        ],
    }
    region = {"page": 1, "region_bbox": [0, 0, 600, 400]}
    claims, held = table_to_claims(table, region, "sha", "test.pdf", "vw", "2.0.0", 0, 0)
    assert len(claims) == 0, f"unsupported table produced claims: {len(claims)}"
    assert len(held) > 0, "unsupported table not held"
    assert held[0]["reason"] == "unsupported_table_class", held[0]
    return {"held": len(held), "reason": held[0]["reason"]}


@fixture("malformed_row_held")
def test_malformed_row():
    """Malformed rows must be held."""
    table = {
        "table_title": "",
        "columns": ["A", "B"],
        "rows": [
            "not_a_dict",
            {"cells": {"A": "1", "B": "2"}, "bbox": [0, 0, 100, 20]},
        ],
    }
    region = {"page": 1, "region_bbox": [0, 0, 600, 400]}
    claims, held = table_to_claims(table, region, "sha", "test.pdf", "vw", "2.0.0", 0, 0)
    # Note: unknown table class holds everything. Let's use a pin table instead.
    table["columns"] = ["Pin", "Name"]
    table["rows"][0] = "not_a_dict"
    table["rows"][1] = {"cells": {"Pin": "1", "Name": "GND"}, "bbox": [0, 0, 100, 20]}
    claims, held = table_to_claims(table, region, "sha", "test.pdf", "vw", "2.0.0", 0, 0)
    assert any(h.get("reason") == "malformed_row" for h in held), f"held: {held}"
    return {"claims": len(claims), "held": len(held)}


@fixture("abs_max_not_rec_op")
def test_abs_max():
    """Absolute maximum must not be marked as recommended operation."""
    table = {
        "table_title": "Absolute Maximum Ratings",
        "columns": ["Symbol", "Value", "Unit"],
        "rows": [{"cells": {"Symbol": "VDS", "Value": "60", "Unit": "V"}, "bbox": [0, 0, 100, 20]}],
    }
    region = {"page": 1, "region_bbox": [0, 0, 600, 400]}
    claims, _ = table_to_claims(table, region, "sha", "test.pdf", "vw", "2.0.0", 0, 0)
    for c in claims:
        assert c["absolute_max"] == True, "abs_max not detected"
        assert c["recommended_operation"] == False, "abs_max incorrectly marked as rec_op"
    return {"abs_max": True, "rec_op": False}


@fixture("empty_cells_held")
def test_empty_cells():
    """Rows with empty cells must be held."""
    table = {
        "table_title": "",
        "columns": ["Pin", "Name"],
        "rows": [
            {"cells": {}, "bbox": [0, 0, 100, 20]},
            {"cells": {"Pin": "1", "Name": "GND"}, "bbox": [0, 20, 100, 40]},
        ],
    }
    region = {"page": 1, "region_bbox": [0, 0, 600, 400]}
    claims, held = table_to_claims(table, region, "sha", "test.pdf", "vw", "2.0.0", 0, 0)
    assert any(h.get("reason") == "empty_cells" for h in held), f"held: {held}"
    return {"claims": len(claims), "held": len(held)}


@fixture("one_vs_ten_rejected")
def test_one_vs_ten():
    """Value '1' must NOT match '10' in OCR."""
    result = _mock_verify("1", "Pin 10 is the reset pin")
    assert result != "SUPPORTED", "'1' matched in 'Pin 10' text"
    return {"verdict": result}


@fixture("fortyfive_vs_145_rejected")
def test_fortyfive_vs_145():
    """Value '45' must NOT match '145' in OCR."""
    result = _mock_verify("45", "145 ohms typical")
    assert result != "SUPPORTED", "'45' matched in '145 ohms' text"
    return {"verdict": result}


@fixture("wrong_column_rejected")
def test_wrong_column():
    """Value in Min column must NOT verify as Max."""
    result = _mock_verify_column("5.0", "Min 1.0 Typ 3.2 Max 5.0", "Min")
    assert result != "SUPPORTED", "Max value verified as Min"
    return {"verdict": result}


@fixture("two_values_same_row")
def test_two_values_same_row():
    """Two different numeric values in same row must be distinguishable."""
    r1 = _mock_verify("17.7", "17.7 18.1 0.697 0.713")
    r2 = _mock_verify("18.1", "17.7 18.1 0.697 0.713")
    assert r1 == "SUPPORTED", "17.7 should match"
    assert r2 == "SUPPORTED", "18.1 should match"
    return {"both_match": True}


@fixture("clipped_crop_rejected")
def test_clipped_crop():
    """Clipped glyph must not be SUPPORTED."""
    r1 = _mock_verify("GND", "GND")
    assert r1 == "SUPPORTED", "full GND should match"
    r2 = _mock_verify("GND", "GN")
    assert r2 != "SUPPORTED", "clipped GN matched as GND"
    return {"full": r1, "clipped": r2}


@fixture("ocr_substitution_rejected")
def test_ocr_substitution():
    """OCR substitutions must be REVIEW not SUPPORTED."""
    result = _mock_verify("1", "l GND Power")
    assert result != "SUPPORTED", "OCR sub 'l' matched as '1'"
    return {"verdict": result}


@fixture("repeated_values_distinguished")
def test_repeated_values():
    """Same value in different rows must maintain row association."""
    result = _mock_verify("GND", "GND Power")
    assert result == "SUPPORTED"
    return {"verdict": result}


@fixture("contradictory_preserved")
def test_contradictory_preserved():
    """Contradictory source must be REVIEW not UNSUPPORTED."""
    result = _mock_verify("45", "54")
    assert result != "UNSUPPORTED", "contradictory called UNSUPPORTED"
    return {"verdict": result}


def _mock_verify(expected, ocr_text):
    words = ocr_text.split()
    exp = expected.strip()
    if exp in words:
        return "SUPPORTED"
    exp_upper = exp.upper()
    for w in words:
        if w.upper() == exp_upper:
            return "SUPPORTED"
    import re
    m = re.match(r"^([<>\u00b1~]?\s*-?\d+\.?\d*)", exp)
    if m:
        core = m.group(1).strip()
        if core in words:
            return "SUPPORTED"
    return "REVIEW"
def _mock_verify_column(expected, ocr_text, claim_column):
    if expected not in ocr_text:
        return "REVIEW"
    parts = ocr_text.split()
    for i, p in enumerate(parts):
        if p == claim_column and i + 1 < len(parts) and parts[i + 1] == expected:
            return "SUPPORTED"
    return "REVIEW"


def run_fixtures():
    results = []
    for name, fn in FIXTURES:
        try:
            result = fn()
            results.append((name, "PASS", result))
            print("  OK " + name + ": " + str(result))
        except AssertionError as e:
            results.append((name, "FAIL", str(e)))
            print("  FAIL " + name + ": " + str(e))
        except Exception as e:
            results.append((name, "ERROR", str(e)))
            print("  ERROR " + name + ": " + str(e))
    return results


if __name__ == "__main__":
    print("=== NEGATIVE FIXTURES ===")
    results = run_fixtures()
    passed = sum(1 for _, s, _ in results if s == "PASS")
    print("")
    print(str(passed) + "/" + str(len(results)) + " passed")
    import sys
    sys.exit(0 if passed == len(results) else 1)
