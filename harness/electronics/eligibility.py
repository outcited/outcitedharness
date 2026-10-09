"""CURVE-08 R2 — hard eligibility before curve comparison.

Ratings and operating constraints are evaluated BEFORE any typical
characteristic is consulted. Verdicts: eligible | ineligible | unknown.
Every ineligibility carries the exact rule, the required value, the rated
value, the applicable scope, and the evidence reference. Missing ratings
are UNKNOWN — never failure. Curve evidence never participates here.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from harness.electronics.candidates import Candidate

ELIGIBILITY_SCHEMA = "harness.electronics-hard-eligibility.v1"

# rule registry for buck converters; each rule maps a requirement key to
# the candidate parametric it must satisfy and the comparison direction
RULES: dict[str, dict[str, Any]] = {
    "vin_min": {
        "param": "vin_max_v",
        "direction": "rated_at_least",
        "scope": "maximum input voltage rating",
        "text": "VIN rating {rated} V must be >= required {req} V",
    },
    "vin_max": {
        "param": "vin_min_v",
        "direction": "rated_at_most",
        "scope": "minimum input operating voltage",
        "text": "VIN operating floor {rated} V must be <= usable maximum "
                "{req} V",
    },
    "iout_min": {
        "param": "iout_max_a",
        "direction": "rated_at_least",
        "scope": "rated continuous output current",
        "text": "rated continuous output {rated} A must be >= required "
                "{req} A",
    },
    "vout": {
        "param": "vout_max_v",
        "direction": "range_contains",
        "scope": "output voltage capability",
        "text": "output capability {rated} must cover required {req} V",
    },
    "temp_max": {
        "param": "temp_max_c",
        "direction": "rated_at_least",
        "scope": "maximum operating temperature rating",
        "text": "temperature rating {rated} C must be >= required {req} C",
    },
}


def _rated(candidate: Candidate, rule: str) -> tuple[Optional[float],
                                                     Optional[dict]]:
    spec = RULES[rule]
    entry = candidate.axis_value(spec["param"]) or {}
    if entry.get("kind") != "point":
        return None, entry or None
    return entry.get("value"), entry


def evaluate_rule(candidate: Candidate, rule: str,
                  required: float) -> dict[str, Any]:
    if rule not in RULES:
        raise ValueError(f"unknown rule {rule!r}")
    spec = RULES[rule]
    value, entry = _rated(candidate, rule)
    base = {
        "rule": rule,
        "scope": spec["scope"],
        "required": required,
        "rated": value,
        "candidate": candidate.candidate_id,
        "part": candidate.device or candidate.opn,
        "family": candidate.family,
    }
    if value is None:
        return {**base, "verdict": "unknown",
                "reason": "rating absent — unknown is never elimination",
                "evidence": (entry or {}).get("evidence") or {}}
    evidence = (entry or {}).get("evidence") or {}
    if rule == "vout":
        lo = candidate.axis_value("vout_min_v") or {}
        vlo = lo.get("value") if lo.get("kind") == "point" else None
        ok = (vlo is None or vlo - 1e-9 <= required) and \
            value + 1e-9 >= required
        if not ok and vlo is not None:
            base["rated"] = [vlo, value]
    elif spec["direction"] == "rated_at_least":
        ok = value + 1e-9 >= required
    else:
        ok = value - 1e-9 <= required
    if ok:
        return {**base, "verdict": "eligible", "evidence": evidence}
    return {
        **base,
        "verdict": "ineligible",
        "reason": spec["text"].format(rated=base["rated"], req=required),
        "evidence": evidence,
    }


def evaluate_candidate(candidate: Candidate,
                       requirements: Mapping[str, float]) -> dict[str, Any]:
    """All hard rules for one candidate. One ineligible rule makes the
    candidate ineligible; otherwise unknown dominates eligible."""

    results = []
    for rule, required in requirements.items():
        if required is None:
            continue
        results.append(evaluate_rule(candidate, rule, float(required)))
    verdicts = [r["verdict"] for r in results]
    if "ineligible" in verdicts:
        verdict = "ineligible"
    elif "unknown" in verdicts:
        verdict = "unknown"
    else:
        verdict = "eligible"
    return {
        "schema": ELIGIBILITY_SCHEMA,
        "candidate": candidate.candidate_id,
        "part": candidate.device or candidate.opn,
        "family": candidate.family,
        "grain": candidate.grain,
        "verdict": verdict,
        "rules": results,
        "parametric_ok": verdict == "eligible",
        "law": "hard eligibility uses ratings only; curve evidence never "
               "eliminates and never rescues",
    }


def thermal_feasibility_note(candidate: Candidate) -> dict[str, Any]:
    """A candidate may be electrically eligible while thermal feasibility
    stays unconfirmed — the note is carried, never upgraded."""

    temp = candidate.axis_value("temp_max_c") or {}
    derating = [e for e in candidate.evidence_refs
                if e.get("phenomenon") == "case_temperature_vs_load"]
    return {
        "candidate": candidate.candidate_id,
        "electrical_rating_present": temp.get("kind") == "point",
        "thermal_curve_evidence": len(derating),
        "thermal_feasibility": "confirmed" if False else (
            "curve_evidence_advisory" if derating else
            "unconfirmed"),
        "note": "advisory case-temperature curves are measurements on a "
                "vendor eval setup, not guaranteed thermal limits",
    }


def evaluate_cohort(candidates: Sequence[Candidate],
                    requirements: Mapping[str, float]) -> dict[str, dict]:
    return {c.candidate_id: evaluate_candidate(c, requirements)
            for c in candidates}


__all__ = ["ELIGIBILITY_SCHEMA", "RULES", "evaluate_candidate",
           "evaluate_cohort", "evaluate_rule", "thermal_feasibility_note"]
