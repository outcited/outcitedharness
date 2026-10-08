"""Elimination-ledger evaluator — deterministic atom evaluation (Phase 1).

The discovery law (spec sections 7, 41, 46): a candidate is eliminated
only by an explicit FAIL on a hard atom; missing evidence is UNKNOWN and
never eliminates; NEAR MISS is a separate cohort; every verdict carries
its evidence. This module evaluates typed requirement atoms against a
part's catalog claims. No model calls, no writes, no invented values.

Atoms are typed requirements:
  Atom(axis="vds_rating_v", op="min_rating", value=40.0, hard=True,
       margin_pct=20.0, required_condition={"tc_c": 100.0})

Per (part, atom) the evaluator:
  1. finds claims whose symbol belongs to the axis
  2. projects each claim's rating class and drops claims whose class the
     axis excludes (transient/surge never satisfy continuous axes)
  3. when the atom declares required conditions, compares the claim's
     typed condition via condition_covers — a condition MISMATCH is
     UNKNOWN ("insufficient evidence"), never FAIL (spec section 7)
  4. compares values via compare_with_margin (margin never silent)
  5. aggregates to the part verdict with the winning claim's evidence

Aggregation (spec section 41): any PASS -> PASS; else any NEAR_MISS ->
NEAR_MISS; else any FAIL -> FAIL; else UNKNOWN. A hard-atom FAIL marks
the part ELIMINATED; UNKNOWN-only parts remain.

Rating-class note (honest v1): catalog claims carry (symbol, qualifier,
condition) — no table_kind yet. Classes are projected from what is
present; per-axis acceptance policies are explicit, and the verdict row
records the class used, so the ledger is transparent about how much of
the section-43 guard each verdict actually exercised.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from harness.electronics.condition_model import (
    condition_covers,
    parse_condition,
)
from harness.electronics.rating_class import compare_with_margin, rating_class

EVALUATOR_SCHEMA = "harness.discovery-evaluator.v1"

_VERDICT_ORDER = {"PASS": 0, "NEAR_MISS": 1, "FAIL": 2, "UNKNOWN": 3}


def _norm_symbol(symbol: str | None) -> str:
    s = re.sub(r"[\s_]+", "", str(symbol or "")).upper()
    return s.replace("(", "").replace(")", "").replace(",", "")


# --- axis registry ---------------------------------------------------------------

@dataclass(frozen=True)
class Axis:
    name: str
    symbols: tuple[str, ...]
    op: str
    accepts_classes: tuple[str, ...] | None = None  # None = any class
    default_near_miss_pct: float = 10.0
    # True: Min-column claims are valid rating evidence (guaranteed-minimum
    # specs like VDSS "breakdown >= 500 V" print in the Min column).
    # False: span quantities (TJ/TSTG "-55 175") — a Min-column claim is
    # the range floor, not a rating magnitude -> wrong_bound UNKNOWN.
    min_column_evidence: bool = True


AXES: dict[str, Axis] = {
    a.name: a for a in (
        Axis("vds_rating_v", ("VDSS", "VBRDSS", "VDSSMAX"), "min_rating"),
        Axis("id_continuous_a", ("IDDC", "ID"), "min_rating"),
        Axis("rds_on_ohm", ("RDSON", "RDS(ON)MAX"), "max_rating",
             accepts_classes=("rated_limit", "typical", "unknown")),
        Axis("tj_max_c", ("TJ", "TJMAX"), "min_rating",
             min_column_evidence=False),
        Axis("tstg_range_c", ("TSTG",), "covers",
             min_column_evidence=False),
    )
}
_AXONYM = {}
for _axis in AXES.values():
    for _sym in _axis.symbols:
        _AXONYM.setdefault(_norm_symbol(_sym), _axis.name)


def axis_for_symbol(symbol: str | None) -> str | None:
    return _AXONYM.get(_norm_symbol(symbol))


@dataclass(frozen=True)
class Atom:
    axis: str
    op: str | None = None
    value: Any = None
    hard: bool = True
    margin_pct: float | None = None
    required_condition: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.axis not in AXES:
            raise ValueError(f"unknown axis {self.axis!r}")
        if self.op is None:
            object.__setattr__(self, "op", AXES[self.axis].op)


# --- per-claim evaluation ---------------------------------------------------------

_BOUND_MIN = re.compile(r"^\s*min", re.I)
_BOUND_MAX = re.compile(r"^\s*max", re.I)


def _column_header(claim: Mapping[str, Any]) -> str:
    if claim.get("column_header"):
        return str(claim["column_header"])
    prov = claim.get("provenance")
    if isinstance(prov, Mapping):
        return str(prov.get("column_header") or "")
    return ""


@dataclass
class Verdict:
    part: str
    atom: str
    verdict: str                     # PASS | NEAR_MISS | FAIL | UNKNOWN
    reason: str
    value: Any = None
    rating_class: str | None = None
    condition_relation: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)


def evaluate_claim(atom: Atom, claim: Mapping[str, Any]) -> Verdict | None:
    """One claim against one atom; None when the claim cannot even run."""
    axis = AXES[atom.axis]
    symbol = str(claim.get("symbol") or "")
    qualifier = claim.get("qualifier")
    cls = rating_class(symbol, None, None, qualifier)
    if axis.accepts_classes is not None and cls not in axis.accepts_classes:
        return None
    if axis.accepts_classes is None and cls in ("transient", "surge"):
        return None  # pulsed/surge claims never satisfy continuous axes

    # bound-role guard: span quantities only (min_column_evidence=False).
    # A TJ range floor "-55" read as a max-temp rating, or a Min-column
    # value trivially passing a max_rating atom, is WRONG-BOUND evidence
    # -> UNKNOWN, never FAIL. Guaranteed-minimum specs (VDSS) keep their
    # Min-column claims as valid evidence.
    col = _column_header(claim)
    if col and not axis.min_column_evidence and \
            atom.op in ("min_rating", "max_rating") and \
            _BOUND_MIN.match(col):
        return Verdict(
            part=claim.get("opn") or "", atom=atom.axis, verdict="UNKNOWN",
            reason="wrong_bound", value=claim.get("value"),
            rating_class=cls, evidence=_evidence_of(claim))

    # covers atoms prove CONTAINMENT: a single scalar endpoint cannot
    # (and must never FAIL on one bound alone)
    if atom.op == "covers" and not isinstance(claim.get("value"), list):
        return Verdict(
            part=claim.get("opn") or "", atom=atom.axis, verdict="UNKNOWN",
            reason="single_bound", value=claim.get("value"),
            rating_class=cls, evidence=_evidence_of(claim))

    condition_raw = claim.get("condition") or ""
    cond_relation: str | None = None
    if atom.required_condition:
        claim_keys = parse_condition(condition_raw)
        if "reject" in claim_keys:
            claim_keys = {"keys": {}}
        cond_relation = condition_covers(claim_keys.get("keys"),
                                         atom.required_condition)
        if cond_relation == "mismatch":
            return Verdict(
                part=claim.get("opn") or "", atom=atom.axis, verdict="UNKNOWN",
                reason="condition_mismatch", value=claim.get("value"),
                rating_class=cls, condition_relation=cond_relation,
                evidence=_evidence_of(claim))
        if cond_relation == "absent":
            return Verdict(
                part=claim.get("opn") or "", atom=atom.axis, verdict="UNKNOWN",
                reason="condition_absent", value=claim.get("value"),
                rating_class=cls, condition_relation=cond_relation,
                evidence=_evidence_of(claim))

    cmp = compare_with_margin(claim.get("value"), atom.value, atom.op,
                              margin_pct=atom.margin_pct,
                              near_miss_pct=axis.default_near_miss_pct)
    reason = cmp.get("reason") or (
        "margin_gap" if cmp.get("verdict") == "NEAR_MISS" and
        cmp.get("satisfies_raw_requirement") else cmp["verdict"].lower())
    return Verdict(
        part=claim.get("opn") or "", atom=atom.axis, verdict=cmp["verdict"],
        reason=reason, value=claim.get("value"), rating_class=cls,
        condition_relation=cond_relation,
        evidence={**_evidence_of(claim), "comparison": {
            k: v for k, v in cmp.items() if k != "verdict"}})


def _evidence_of(claim: Mapping[str, Any]) -> dict[str, Any]:
    prov = claim.get("provenance")
    if isinstance(prov, str):
        try:
            import json
            prov = json.loads(prov)
        except ValueError:
            prov = {"raw": prov}
    return {"claim": {k: claim.get(k) for k in
                      ("symbol", "qualifier", "condition", "value", "unit",
                       "coverage_kind")},
            "provenance": prov or {}}


def evaluate_part(part: str, atoms: Sequence[Atom],
                  claims: Iterable[Mapping[str, Any]]) -> dict[str, Verdict]:
    """Best verdict per atom for one part, with winning evidence.

    covers atoms pair the Min/Max column claims of a span row into one
    range claim first (both quotes ride the evidence); an unpaired bound
    is UNKNOWN single_bound, never FAIL.
    """
    by_axis: dict[str, list[Mapping[str, Any]]] = {a.axis: [] for a in atoms}
    for claim in claims:
        axis_name = axis_for_symbol(claim.get("symbol"))
        if axis_name in by_axis:
            by_axis[axis_name].append(claim)

    out: dict[str, Verdict] = {}
    for atom in atoms:
        axis = AXES[atom.axis]
        rows: list[Verdict] = []
        if atom.op == "covers":
            lo = hi = None
            lo_claim = hi_claim = None
            for c in by_axis[atom.axis]:
                col = _column_header(c)
                v = c.get("value")
                if not isinstance(v, (int, float)):
                    continue
                if _BOUND_MIN.match(col) and (lo is None or v < lo):
                    lo, lo_claim = v, c
                elif _BOUND_MAX.match(col) and (hi is None or v > hi):
                    hi, hi_claim = v, c
            if lo is not None and hi is not None and lo <= hi:
                merged = {**hi_claim, "value": [lo, hi], "opn": part}
                v = evaluate_claim(atom, merged)
                if v is not None:
                    v.evidence["paired_bounds"] = {
                        "min_quote": (lo_claim.get("provenance") or {}).get(
                            "quote") if isinstance(
                            lo_claim.get("provenance"), Mapping) else None,
                        "max_quote": (hi_claim.get("provenance") or {}).get(
                            "quote") if isinstance(
                            hi_claim.get("provenance"), Mapping) else None,
                    }
                    rows.append(v)
            else:
                rows.append(Verdict(part=part, atom=atom.axis,
                                    verdict="UNKNOWN",
                                    reason="single_bound" if
                                    by_axis[atom.axis] else "no_claim"))
        else:
            for c in by_axis[atom.axis]:
                v = evaluate_claim(atom, {**c, "opn": part})
                if v is not None:
                    rows.append(v)
        if not rows:
            out[atom.axis] = Verdict(part=part, atom=atom.axis,
                                     verdict="UNKNOWN", reason="no_claim")
            continue
        rows.sort(key=lambda v: _VERDICT_ORDER[v.verdict])
        out[atom.axis] = rows[0]
    return out


# --- design evaluation (the ledger) ------------------------------------------------

def evaluate_design(atoms: Sequence[Atom],
                    parts_claims: Mapping[str, Sequence[Mapping[str, Any]]],
                    ) -> dict[str, Any]:
    """Full elimination ledger over a candidate population.

    Returns cohorts {passing, near_miss, soft_fail, unknown, eliminated},
    per-part verdicts, and the constraint-driver statistic (which atom
    eliminates what share of candidates — the spec's killer feature).
    Per-atom FAIL counts overlap (a part may fail several atoms); sums
    can exceed the eliminated count — each stat is "parts this atom
    fails", disclosed as such.
    """
    ledger: dict[str, dict[str, Verdict]] = {}
    cohorts: dict[str, list[str]] = {"passing": [], "near_miss": [],
                                     "soft_fail": [], "unknown": [],
                                     "eliminated": []}
    atom_fail_counts: dict[str, int] = {a.axis: 0 for a in atoms}
    family_borrowed = 0

    for part, claims in parts_claims.items():
        verdicts = evaluate_part(part, atoms, claims)
        ledger[part] = verdicts
        eliminated = False
        soft_failed = False
        near = False
        unknown_only = True
        for a in atoms:
            v = verdicts[a.axis]
            if v.verdict == "FAIL" and a.hard:
                eliminated = True
                atom_fail_counts[a.axis] += 1
            elif v.verdict == "FAIL":
                soft_failed = True  # spec 41: soft fail scores lower,
                                    # it never silently passes
            if v.verdict == "NEAR_MISS":
                near = True
            if v.verdict in ("PASS", "NEAR_MISS", "FAIL"):
                unknown_only = False
            cov = (v.evidence.get("claim") or {}).get("coverage_kind")
            if cov == "family":
                family_borrowed += 1
        if eliminated:
            cohorts["eliminated"].append(part)
        elif soft_failed:
            cohorts["soft_fail"].append(part)
        elif near:
            cohorts["near_miss"].append(part)
        elif unknown_only:
            cohorts["unknown"].append(part)
        else:
            cohorts["passing"].append(part)

    n = max(len(parts_claims), 1)
    constraint_driver = {
        axis: {"failed_parts": c, "eliminates_pct": round(100.0 * c / n, 1)}
        for axis, c in atom_fail_counts.items()}
    return {"cohorts": cohorts, "ledger": ledger,
            "constraint_driver": constraint_driver,
            "constraint_driver_note": (
                "per-atom FAIL counts overlap; each stat is 'parts this "
                "atom fails', not a partition"),
            "family_borrowed_verdicts": family_borrowed,
            "candidates": len(parts_claims)}
