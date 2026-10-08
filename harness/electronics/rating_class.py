"""Rating-class projection and margin-aware comparison (deterministic).

Spec section 43: absolute maximum, recommended operating, typical,
transient, and surge ratings are DIFFERENT QUANTITIES and must never be
conflated — a 40 V absolute maximum never satisfies a 36 V operating
requirement. The readers already print ``table_kind`` and
``quantity_qualifier``; this module projects them (plus the
pulse/surge symbol vocabulary) into the closed rating-class set:

  absolute_maximum  stress rating (never satisfies operating atoms)
  recommended       recommended-operating / operating range rows
  rated_limit       EC/summary/thermal min|max|rated spec limits
  typical           typ-column performance values
  transient         pulsed / avalanche ratings (IDM, ID,pulse, EAS, IAS,
                    VPULSE, Zth) — even inside absmax tables
  surge             surge ratings (ISM, IASM, IFSM, ITSM, VSURGE)
  unknown           fail-closed: role unknown, diagram fragments, leaks

Pulse/surge vocabulary fires on the SYMBOL; a parameter that prints
"continuous" vetoes it (glued composite symbols like
``ID@TC=25°C ID@TC=100°C IDM`` where the value is the continuous row).

``compare_with_margin`` implements spec section 42: margin is applied to
the REQUIREMENT (36 V max + 20% margin -> rating >= 43.2 V), is never
silent (margin_pct must be passed or is explicitly None), and a claim
that fails the raw requirement but sits within ``near_miss_pct`` of it
is NEAR_MISS (separate cohort), not FAIL.
"""

from __future__ import annotations

import re
from typing import Any

RATING_CLASS_SCHEMA = "harness.electronics-rating-class.v1"

SURGE_SYMBOL = re.compile(r"\b(?:is[am]|iasm|ifsm|itsm|vsurge|vsurge\d?)\b", re.I)
PULSE_SYMBOL = re.compile(
    r"\b(?:idm|idp|idpulse|id,\s*pulse|\be\s*\*?\s*\d?\s*as\b|ias|vpulse|"
    r"vds\s*\(\s*transient\s*\)|zth)\b", re.I)
_CONTINUOUS = re.compile(r"continuous", re.I)
_OPERATING = re.compile(r"operating", re.I)

RATING_CLASSES = ("absolute_maximum", "recommended", "rated_limit",
                  "typical", "transient", "surge", "unknown")


def rating_class(symbol: str | None, parameter: str | None,
                 table_kind: str | None,
                 quantity_qualifier: str | None) -> str:
    """Project one printed row to the closed rating-class set."""
    sym = str(symbol or "")
    text = f"{sym} {parameter or ''}"
    if not _CONTINUOUS.search(text):
        if SURGE_SYMBOL.search(text):
            return "surge"
        if PULSE_SYMBOL.search(text):
            return "transient"
    kind = table_kind or ""
    if kind == "absolute_maximum":
        return "absolute_maximum"
    if kind == "recommended":
        return "recommended"
    qq = quantity_qualifier or ""
    if qq == "absolute_maximum":
        return "absolute_maximum"
    if qq == "typical":
        return "typical"
    if qq in ("minimum", "maximum", "rated"):
        if qq == "rated" and _OPERATING.search(text):
            return "recommended"
        return "rated_limit"
    return "unknown"


# --- margin-aware comparison ---------------------------------------------------

def _single(v: Any) -> float | None:
    if isinstance(v, (int, float)):
        return float(v)
    return None


def _bounds(v: Any) -> tuple[float, float] | None:
    if isinstance(v, (int, float)):
        return (float(v), float(v))
    if isinstance(v, list) and len(v) == 2 and all(
            isinstance(x, (int, float)) for x in v):
        return (float(min(v)), float(max(v)))
    return None


def compare_with_margin(claim_value: Any, requirement: Any,
                        direction: str,
                        margin_pct: float | None = None,
                        near_miss_pct: float = 10.0) -> dict[str, Any]:
    """Compare a claim value against a requirement atom.

    direction: "min_rating" (claim >= requirement satisfies, e.g. voltage
    rating), "max_rating" (claim <= requirement satisfies, e.g. RDS(on)),
    or "inside" (claim within requirement range).

    Margin applies to the REQUIREMENT (never the claim) and must be
    explicitly supplied — it is never invented. Returns verdict
    PASS | NEAR_MISS | FAIL, the effective requirement, and the relative
    distance. Zero/negative claim magnitudes: |value| used for ± prints.
    """
    cb = _bounds(claim_value)
    rb = _bounds(requirement)
    if cb is None or rb is None:
        return {"verdict": "UNKNOWN", "reason": "unparseable_value",
                "effective_requirement": requirement}
    lo_c, hi_c = sorted((abs(cb[0]), abs(cb[1]))) if direction == "min_rating" \
        else (min(cb[0], cb[1]), max(cb[0], cb[1]))
    lo_r, hi_r = rb

    m = float(margin_pct) if margin_pct is not None else 0.0
    if direction == "min_rating":
        # claim must be >= requirement (magnitudes; ±16 V prints as 16)
        eff = hi_r * (1.0 + m / 100.0)
        magnitude = max(abs(cb[0]), abs(cb[1]))
        satisfies_raw = magnitude >= hi_r
        if magnitude >= eff:
            verdict = "PASS"
        elif satisfies_raw:
            # meets the printed requirement, fails the requested margin
            verdict = "NEAR_MISS"
        elif magnitude >= hi_r * (1.0 - near_miss_pct / 100.0):
            verdict = "NEAR_MISS"
        else:
            verdict = "FAIL"
        distance = 100.0 * (hi_r - magnitude) / max(abs(hi_r), 1e-9)
        return {"verdict": verdict, "effective_requirement": eff,
                "distance_pct": round(distance, 4),
                "margin_pct_applied": m or None,
                "satisfies_raw_requirement": satisfies_raw}
    if direction == "max_rating":
        # claim must be <= requirement
        eff = lo_r * (1.0 - m / 100.0)
        satisfies_raw = lo_c <= lo_r
        if lo_c <= eff:
            verdict = "PASS"
        elif satisfies_raw:
            verdict = "NEAR_MISS" if m > 0 else "PASS"
        elif lo_c <= lo_r * (1.0 + near_miss_pct / 100.0):
            verdict = "NEAR_MISS"
        else:
            verdict = "FAIL"
        distance = 100.0 * (lo_c - lo_r) / max(abs(lo_r), 1e-9)
        return {"verdict": verdict, "effective_requirement": eff,
                "distance_pct": round(distance, 4),
                "margin_pct_applied": m or None,
                "satisfies_raw_requirement": satisfies_raw}
    if direction == "inside":
        # claim range must sit within the requirement range
        eff = (lo_r * (1.0 - m / 100.0), hi_r * (1.0 + m / 100.0))
        satisfies_raw = lo_c >= lo_r and hi_c <= hi_r
        if satisfies_raw:
            verdict = "PASS"
        elif lo_c >= lo_r * (1.0 - near_miss_pct / 100.0) and \
                hi_c <= hi_r * (1.0 + near_miss_pct / 100.0):
            verdict = "NEAR_MISS"
        else:
            verdict = "FAIL"
        return {"verdict": verdict, "effective_requirement": eff,
                "margin_pct_applied": m or None,
                "satisfies_raw_requirement": satisfies_raw}
    if direction == "covers":
        # claim range must CONTAIN the requirement range (a part's
        # -55..150 storage range covers a -40..105 environment)
        eff = (lo_r * (1.0 - m / 100.0), hi_r * (1.0 + m / 100.0))
        satisfies_raw = lo_c <= lo_r and hi_c >= hi_r
        margin_ok = lo_c <= eff[0] and hi_c >= eff[1]
        if margin_ok:
            verdict = "PASS"
        elif satisfies_raw:
            verdict = "NEAR_MISS" if m > 0 else "PASS"
        elif hi_c < hi_r:
            short = 100.0 * (hi_r - hi_c) / max(abs(hi_r), 1e-9)
            verdict = "NEAR_MISS" if short <= near_miss_pct else "FAIL"
        else:  # lo_c > lo_r
            short = 100.0 * (lo_c - lo_r) / max(abs(lo_r), 1e-9)
            verdict = "NEAR_MISS" if short <= near_miss_pct else "FAIL"
        return {"verdict": verdict, "effective_requirement": eff,
                "margin_pct_applied": m or None,
                "satisfies_raw_requirement": satisfies_raw}
    return {"verdict": "UNKNOWN", "reason": "bad_direction"}


__all__ = [
    "RATING_CLASSES",
    "RATING_CLASS_SCHEMA",
    "compare_with_margin",
    "rating_class",
]
