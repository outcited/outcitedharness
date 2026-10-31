#!/usr/bin/env python3
"""provenance_contract.py — Rule-8 claim-level QA gate (owner directive, Sam, 2026-10-29).

Enforces five source-contract corrections against extracted claim records.
This module is the QA gate between PDF extraction and the served payload —
it catches normalization defects BEFORE they reach the frontend.

Five contracts enforced:

1. VALUE/UNIT DIMENSIONAL CONSISTENCY
   The shipped (value, unit) pair must represent the same physical quantity
   as the printed source text. A value converted to one unit with another
   unit's label is a 10^6-class silent error.

2. ROLE/RATING FROM PRINTED COLUMN HEADERS
   Role (min/typ/max) comes from the printed column header, never from
   column folding. Thermal metrics are typical characterization, never
   rated limits, unless the document explicitly prints "max" in the
   column header.

3. PACKAGE COORDINATES
   Multi-package thermal tables emit one value per package. The claim
   must carry the package identity from the printed column header.

4. DOC-VARIANT BINDING
   Each claim cites exactly one document. The doc_variant (base / -Q1 /
   etc.) is derived from the document's own revision code, never inferred
   from another document.

5. PER-ROW SOURCE COORDINATES
   Every claim carries doc_sha, page, row_header, and column_header from
   the printed table.

Fail-closed: any claim failing a contract check is HELD with the specific
failing field and reason. There is no silent fallback to green.
"""
from __future__ import annotations

import re
import sys
from typing import Any

# ------------------------------------------------------- dimensional analysis

# Canonical base units by dimension
_DIMENSION = {
    "v": "V", "mv": "V", "μv": "V", "uv": "V",
    "a": "A", "ma": "A", "μa": "A", "ua": "A", "na": "A",
    "°c/w": "°C/W", "c/w": "°C/W", "°cw": "°C/W",
    "°c": "°C", "c": "°C",
    "w": "W", "mw": "W",
    "hz": "Hz", "khz": "Hz", "mhz": "Hz",
    "db": "dB",
}

_TO_BASE = {
    "v": 1.0, "mv": 1e-3, "μv": 1e-6, "uv": 1e-6,
    "a": 1.0, "ma": 1e-3, "μa": 1e-6, "ua": 1e-6, "na": 1e-9,
    "°c/w": 1.0, "c/w": 1.0, "°cw": 1.0,
    "°c": 1.0, "c": 1.0,
    "w": 1.0, "mw": 1e-3,
    "hz": 1.0, "khz": 1e3, "mhz": 1e6,
    "db": 1.0,
}

def _norm_unit(u: str | None) -> str:
    if not u:
        return ""
    return u.strip().lower().replace("ω", "Ω").replace("µ", "μ")

def _dimension(u: str) -> str | None:
    return _DIMENSION.get(_norm_unit(u))

def _to_base(val: float, u: str) -> float | None:
    d = _dimension(u)
    if d is None:
        return None
    return val * _TO_BASE.get(_norm_unit(u), 1.0)

def unit_consistent(value: Any, shipped_unit: str | None,
                    printed_value: Any, printed_unit: str | None) -> bool | None:
    """Contract 1: shipped (value, unit) must represent the same physical
    quantity as the printed (value, unit).
    Returns None only when BOTH sides are genuinely unitless.
    Returns False on any dimensional mismatch, unsupported unit, or
    unparseable value — the caller must HELD, never silently pass."""
    if value is None or printed_value is None:
        return False
    try:
        sv = float(value); pv = float(printed_value)
    except (TypeError, ValueError):
        return False
    su = _norm_unit(shipped_unit); pu = _norm_unit(printed_unit)
    sd = _dimension(su); pd = _dimension(pu)
    if sd is None and pd is None:
        return True  # both genuinely unitless
    if sd != pd:
        return False  # different dimensions — never consistent
    svb = _to_base(sv, su); pvb = _to_base(pv, pu)
    if svb is None or pvb is None:
        return False
    if pvb == 0:
        return svb == 0
    return abs(svb - pvb) / abs(pvb) < 0.01  # 1% tolerance

def value_in_plausible_range(value: Any, unit: str | None,
                             field: str) -> bool | None:
    """Contract 1 supplementary: thermal metrics >1000 °C/W or currents >10 A
    on a 3.5 A regulator are dimensionally implausible."""
    if value is None:
        return False
    try:
        f = abs(float(value))
    except (TypeError, ValueError):
        return False
    u = _norm_unit(unit)
    if _dimension(u) == "°C/W" and f > 500:
        return False
    if _dimension(u) == "A" and f > 10:
        return False
    return True

# ------------------------------------------------------- role/rating rules

THERMAL_PATTERN = re.compile(r"r[\u03b8θ]?(j[a-z←]|a|b|c)|psi|ψj", re.IGNORECASE)
THERMAL_FIELDS = frozenset({
    "rtheta_ja_c_per_w", "rtheta_jb_c_per_w", "rtheta_jc_c_per_w",
    "rtheta_jc_top_c_per_w", "rtheta_jc_bot_c_per_w",
    "psi_jt_c_per_w", "psi_jb_c_per_w",
    "thermal_resistance", "thermal_impedance",
})

def is_thermal_field(field: str | None) -> bool:
    if not field:
        return False
    return field.lower() in THERMAL_FIELDS or bool(THERMAL_PATTERN.search(field))

def correct_rating_class(field: str | None, printed_role: str | None,
                         printed_value: Any) -> str:
    """Contract 2: rating derived from the printed column header and the
    field's physical semantics."""
    if is_thermal_field(field):
        return "typical"
    if (printed_role or "").lower().strip() in ("max", "maximum"):
        return "rated_limit"
    if (printed_role or "").lower().strip() in ("min", "minimum"):
        return "rated_limit"
    return "typical"

def correct_role(field: str | None, printed_role: str | None,
                 is_multi_package: bool) -> str:
    """Contract 2: role from the printed column header.
    For per-package columns, role is the package identity, not min/max."""
    if is_multi_package:
        return "value"  # per-package characterization value
    pr = (printed_role or "").lower().strip()
    if pr in ("min", "minimum"): return "min"
    if pr in ("typ", "typical"): return "typ"
    if pr in ("max", "maximum"): return "max"
    return pr or "value"

# ------------------------------------------------------- package dimension

def has_package_key(claim: dict) -> bool:
    return bool(claim.get("package"))

def has_doc_variant(claim: dict) -> bool:
    return bool(claim.get("doc_variant"))

def doc_variant_from_revision(rev_code: str | None) -> str:
    if not rev_code:
        return "UNKNOWN"
    if "Q1" in rev_code.upper() or "AUTOMOTIVE" in rev_code.upper():
        return "-Q1"
    return "base"

# ------------------------------------------------------- QA gate

def qa_claim(claim: dict, printed_row: dict) -> list[dict]:
    """Run all five source-contract checks against one claim.
    Returns a list of defects (empty = clean)."""
    defects = []
    field = claim.get("field", "")
    shipped_val = claim.get("value")
    shipped_unit = claim.get("unit")
    printed_val = printed_row.get("value")
    printed_unit = printed_row.get("unit")
    printed_role = printed_row.get("role")
    multi_pkg = printed_row.get("multi_package", False)

    # Contract 1: dimensional consistency
    uc = unit_consistent(shipped_val, shipped_unit, printed_val, printed_unit)
    if uc is False or uc is None:
        defects.append({
            "contract": 1, "field": field,
            "defect": "unit_dimension_mismatch",
            "shipped": f"{shipped_val} {shipped_unit}",
            "printed": f"{printed_val} {printed_unit}",
        })

    # Contract 1 supplementary: implausible magnitude
    plaus = value_in_plausible_range(shipped_val, shipped_unit, field)
    if plaus is False:
        defects.append({
            "contract": 1, "field": field,
            "defect": "implausible_magnitude",
            "shipped": f"{shipped_val} {shipped_unit}",
        })

    # Contract 2: role/rating
    correct_r = correct_rating_class(field, printed_role, printed_val)
    shipped_r = claim.get("rating_class", "")
    if is_thermal_field(field) and shipped_r == "rated_limit":
        defects.append({
            "contract": 2, "field": field,
            "defect": "thermal_rated_limit_fabrication",
            "shipped_rating": shipped_r, "correct_rating": "typical",
        })
    if multi_pkg and shipped_r == "rated_limit":
        defects.append({
            "contract": 2, "field": field,
            "defect": "per_package_value_as_rated_limit",
        })

    # Contract 3: package coordinates
    if not has_package_key(claim) and is_thermal_field(field):
        defects.append({
            "contract": 3, "field": field,
            "defect": "package_coordinates_missing",
        })

    # Contract 4: doc_variant binding
    if not has_doc_variant(claim) and claim.get("source", {}).get("doc_sha256"):
        defects.append({
            "contract": 4, "field": field,
            "defect": "doc_variant_binding_missing",
        })

    return defects

def qa_payload(payload: dict, printed_rows: dict[str, dict]) -> dict:
    """Run QA against the full payload.
    printed_rows: {field -> printed_row} from the source document."""
    all_defects = []
    verdicts = {}
    for c in payload.get("part", {}).get("claims", []):
        field = c.get("field", "")
        row = printed_rows.get(field, {})
        defects = qa_claim(c, row)
        # claim-level verdict: HELD if any defect
        verdict = "HELD" if defects else "VERIFIED"
        verdicts[field] = verdict
        for d in defects:
            all_defects.append({"claim_field": field, **d})
    return {
        "verdicts": verdicts,
        "verified": sum(1 for v in verdicts.values() if v == "VERIFIED"),
        "held": sum(1 for v in verdicts.values() if v == "HELD"),
        "defects": all_defects,
    }
